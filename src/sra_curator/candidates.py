"""Candidate extraction from messy SRA metadata."""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from sra_curator.db import connect, utc_now


SKIP_VALUES = {
    "",
    "missing",
    "n/a",
    "na",
    "none",
    "not applicable",
    "not collected",
    "not provided",
    "unknown",
}

ATTRIBUTE_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("disease", ("disease", "diagnosis", "health state", "phenotype")),
    ("tissue", ("tissue", "organ", "source name", "sample type", "anatomical")),
    ("cell_type", ("cell type", "cell_type", "cell line", "cell_line", "cell subtype")),
    ("assay", ("assay", "library strategy", "library_strategy", "experiment type")),
    ("gene_target", ("target", "gene", "genotype", "antibody", "chip antibody")),
]

ASSAY_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("ATAC-seq", re.compile(r"\bATAC(?:-seq)?\b|chromatin accessibility", re.I)),
    ("CUT&RUN", re.compile(r"\bCUT\s*&\s*RUN\b|cleavage under targets", re.I)),
    ("CUT&Tag", re.compile(r"\bCUT\s*&\s*Tag\b|tagmentation", re.I)),
    ("RNA-seq", re.compile(r"\bRNA[- ]?seq\b|transcriptom", re.I)),
    ("scRNA-seq", re.compile(r"\bscRNA[- ]?seq\b|single[- ]cell RNA", re.I)),
    ("ChIP-seq", re.compile(r"\bChIP[- ]?seq\b|chromatin immunoprecipitation", re.I)),
]

GENE_TOKEN = re.compile(r"\b[A-Z][A-Z0-9-]{2,12}\b")
GENE_STOPWORDS = {
    "ATAC",
    "CUT",
    "RUN",
    "TAG",
    "RNA",
    "DNA",
    "SEQ",
    "CHIP",
    "INPUT",
    "CONTROL",
    "WT",
}


def clean_value(value: str | None) -> str:
    """Normalize a raw candidate value for storage."""
    if value is None:
        return ""
    return " ".join(value.strip().split())


def should_skip(value: str) -> bool:
    """Return True if a candidate value is uninformative."""
    return clean_value(value).lower() in SKIP_VALUES


def fields_for_attribute(tag: str) -> list[str]:
    """Infer normalized annotation fields from an SRA sample-attribute tag."""
    normalized = tag.strip().lower().replace("_", " ")
    fields: list[str] = []
    for field, needles in ATTRIBUTE_RULES:
        if any(needle in normalized for needle in needles):
            fields.append(field)
    return fields


def upsert_candidate(
    conn: sqlite3.Connection,
    normalized_field: str,
    source_field: str,
    raw_value: str,
    bioproject: str,
    run_count: int,
) -> None:
    """Insert or refresh a candidate row."""
    now = utc_now()
    conn.execute(
        """
        INSERT INTO annotation_candidates (
            normalized_field, source_field, raw_value, bioproject,
            run_count, status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, 'unreviewed', ?, ?)
        ON CONFLICT (normalized_field, source_field, raw_value, bioproject)
        DO UPDATE SET
            run_count = excluded.run_count,
            updated_at = excluded.updated_at
        """,
        (
            normalized_field,
            source_field,
            raw_value,
            bioproject or "",
            run_count,
            now,
            now,
        ),
    )


def extract_annotation_candidates(project: str, bioproject: str = "") -> dict[str, Any]:
    """Extract annotation candidates from stored SRA metadata."""
    processed = 0
    by_field: dict[str, int] = {}

    with connect(project=project) as conn:
        processed += _extract_organisms(conn, bioproject, by_field)
        processed += _extract_library_fields(conn, bioproject, by_field)
        processed += _extract_sample_attributes(conn, bioproject, by_field)
        processed += _extract_assay_keywords(conn, bioproject, by_field)
        processed += _extract_gene_tokens(conn, bioproject, by_field)
        conn.commit()

        total = conn.execute("SELECT COUNT(*) FROM annotation_candidates").fetchone()[0]
        unreviewed = conn.execute(
            "SELECT COUNT(*) FROM annotation_candidates WHERE status = 'unreviewed'"
        ).fetchone()[0]

    return {
        "project": project,
        "bioproject": bioproject or None,
        "candidates_processed": processed,
        "total_candidates": total,
        "unreviewed_candidates": unreviewed,
        "by_field": by_field,
    }


def list_candidates(project: str, status: str = "", field: str = "") -> list[dict[str, Any]]:
    """List extracted candidates."""
    sql = """
        SELECT candidate_id, normalized_field, source_field, raw_value,
               bioproject, run_count, status, created_at, updated_at
        FROM annotation_candidates
    """
    clauses: list[str] = []
    params: list[str] = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if field:
        clauses.append("normalized_field = ?")
        params.append(field)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY normalized_field, run_count DESC, raw_value"

    with connect(project=project) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _where_bioproject(bioproject: str, prefix: str = "r") -> tuple[str, tuple[str, ...]]:
    if not bioproject:
        return "", ()
    return f" WHERE {prefix}.bioproject = ?", (bioproject,)


def _record(by_field: dict[str, int], field: str) -> None:
    by_field[field] = by_field.get(field, 0) + 1


def _extract_organisms(
    conn: sqlite3.Connection,
    bioproject: str,
    by_field: dict[str, int],
) -> int:
    where, params = _where_bioproject(bioproject, prefix="r")
    rows = conn.execute(
        f"""
        SELECT r.bioproject, r.organism, COUNT(DISTINCT r.run) AS run_count
        FROM runs r
        {where}
        GROUP BY r.bioproject, r.organism
        """,
        params,
    ).fetchall()
    count = 0
    for bp, organism, run_count in rows:
        value = clean_value(organism)
        if should_skip(value):
            continue
        upsert_candidate(conn, "organism", "organism", value, bp, int(run_count))
        _record(by_field, "organism")
        count += 1
    return count


def _extract_library_fields(
    conn: sqlite3.Connection,
    bioproject: str,
    by_field: dict[str, int],
) -> int:
    where, params = _where_bioproject(bioproject, prefix="r")
    count = 0
    for source_field in ("library_strategy", "library_selection"):
        rows = conn.execute(
            f"""
            SELECT r.bioproject, r.{source_field}, COUNT(DISTINCT r.run) AS run_count
            FROM runs r
            {where}
            GROUP BY r.bioproject, r.{source_field}
            """,
            params,
        ).fetchall()
        for bp, raw_value, run_count in rows:
            value = clean_value(raw_value)
            if should_skip(value):
                continue
            upsert_candidate(conn, "assay", source_field, value, bp, int(run_count))
            _record(by_field, "assay")
            count += 1
    return count


def _extract_sample_attributes(
    conn: sqlite3.Connection,
    bioproject: str,
    by_field: dict[str, int],
) -> int:
    where = "WHERE r.bioproject = ?" if bioproject else ""
    params = (bioproject,) if bioproject else ()
    rows = conn.execute(
        f"""
        SELECT r.bioproject, sa.tag, sa.value, COUNT(DISTINCT r.run) AS run_count
        FROM sample_attributes sa
        JOIN runs r ON sa.run = r.run
        {where}
        GROUP BY r.bioproject, sa.tag, sa.value
        """,
        params,
    ).fetchall()
    count = 0
    for bp, tag, raw_value, run_count in rows:
        value = clean_value(raw_value)
        if should_skip(value):
            continue
        for field in fields_for_attribute(tag or ""):
            upsert_candidate(conn, field, tag, value, bp, int(run_count))
            _record(by_field, field)
            count += 1
    return count


def _extract_assay_keywords(
    conn: sqlite3.Connection,
    bioproject: str,
    by_field: dict[str, int],
) -> int:
    where, params = _where_bioproject(bioproject, prefix="r")
    rows = conn.execute(
        f"""
        SELECT r.bioproject, r.run, r.sample_title, r.experiment_title,
               r.library_construction_protocol
        FROM runs r
        {where}
        """,
        params,
    ).fetchall()
    grouped: dict[tuple[str, str, str], set[str]] = {}
    for bp, run, sample_title, experiment_title, protocol in rows:
        text = " ".join(filter(None, [sample_title, experiment_title, protocol]))
        for label, pattern in ASSAY_PATTERNS:
            if pattern.search(text):
                grouped.setdefault((bp or "", "text_keyword", label), set()).add(run)

    count = 0
    for (bp, source_field, label), runs in grouped.items():
        upsert_candidate(conn, "assay", source_field, label, bp, len(runs))
        _record(by_field, "assay")
        count += 1
    return count


def _extract_gene_tokens(
    conn: sqlite3.Connection,
    bioproject: str,
    by_field: dict[str, int],
) -> int:
    where = "WHERE r.bioproject = ?" if bioproject else ""
    params = (bioproject,) if bioproject else ()
    rows = conn.execute(
        f"""
        SELECT r.bioproject, sa.tag, sa.value, r.run
        FROM sample_attributes sa
        JOIN runs r ON sa.run = r.run
        {where}
        """,
        params,
    ).fetchall()

    grouped: dict[tuple[str, str, str], set[str]] = {}
    for bp, tag, raw_value, run in rows:
        fields = fields_for_attribute(tag or "")
        if "gene_target" not in fields:
            continue
        value = clean_value(raw_value)
        if should_skip(value):
            continue
        grouped.setdefault((bp or "", tag or "gene_target", value), set()).add(run)
        for token in GENE_TOKEN.findall(value):
            if token.upper() in GENE_STOPWORDS:
                continue
            grouped.setdefault((bp or "", f"{tag}:token", token), set()).add(run)

    count = 0
    for (bp, source_field, raw_value), runs in grouped.items():
        upsert_candidate(conn, "gene_target", source_field, raw_value, bp, len(runs))
        _record(by_field, "gene_target")
        count += 1
    return count
