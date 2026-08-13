"""SQLite storage for SRA Curator projects and annotations."""

from __future__ import annotations

import csv
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sra_curator.config import get_data_dir
from sra_curator.parser import parse_sra_xml
from sra_curator.scope import ProjectScope, read_scope, write_scope_file


SCHEMA_VERSION = 1
MAX_QUERY_ROWS = 500
PROJECT_SAMPLE_SIZE = 500


CORE_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_info (
    name        TEXT NOT NULL,
    description TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
    bioproject      TEXT PRIMARY KEY,
    study_accession TEXT,
    study_title     TEXT,
    organism        TEXT,
    pubmed_id       TEXT,
    run_count       INTEGER,
    study_abstract  TEXT,
    center_name     TEXT,
    search_query    TEXT,
    inserted_at     TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    run                           TEXT PRIMARY KEY,
    published                     TEXT,
    total_spots                   INTEGER,
    total_bases                   INTEGER,
    size_bytes                    INTEGER,
    experiment_accession          TEXT,
    experiment_title              TEXT,
    library_strategy              TEXT,
    library_source                TEXT,
    library_selection             TEXT,
    library_layout                TEXT,
    library_construction_protocol TEXT,
    platform                      TEXT,
    instrument_model              TEXT,
    study_accession               TEXT,
    study_title                   TEXT,
    study_abstract                TEXT,
    bioproject                    TEXT,
    pubmed_id                     TEXT,
    sample_accession              TEXT,
    sample_title                  TEXT,
    taxon_id                      TEXT,
    organism                      TEXT,
    biosample                     TEXT,
    center_name                   TEXT,
    submission_accession          TEXT,
    search_query                  TEXT,
    inserted_at                   TEXT
);

CREATE TABLE IF NOT EXISTS sample_attributes (
    run    TEXT NOT NULL REFERENCES runs(run),
    tag    TEXT NOT NULL,
    value  TEXT,
    PRIMARY KEY (run, tag)
);

CREATE TABLE IF NOT EXISTS annotation_candidates (
    candidate_id     INTEGER PRIMARY KEY,
    normalized_field TEXT NOT NULL,
    source_field     TEXT NOT NULL,
    raw_value        TEXT NOT NULL,
    bioproject       TEXT,
    run_count        INTEGER NOT NULL DEFAULT 0,
    status           TEXT NOT NULL DEFAULT 'unreviewed',
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    UNIQUE (normalized_field, source_field, raw_value, bioproject)
);

CREATE TABLE IF NOT EXISTS annotation_decisions (
    decision_id   INTEGER PRIMARY KEY,
    candidate_id  INTEGER REFERENCES annotation_candidates(candidate_id),
    field         TEXT NOT NULL,
    raw_value     TEXT NOT NULL,
    ontology      TEXT,
    term_id       TEXT,
    term_label    TEXT,
    confidence    REAL,
    method        TEXT NOT NULL,
    evidence      TEXT,
    notes         TEXT,
    reviewer      TEXT,
    status        TEXT NOT NULL DEFAULT 'accepted',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ontology_cache (
    ontology    TEXT NOT NULL,
    query       TEXT NOT NULL,
    matched     INTEGER NOT NULL,
    term_id     TEXT,
    term_label  TEXT,
    confidence  REAL,
    method      TEXT,
    raw_json    TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (ontology, query)
);

CREATE INDEX IF NOT EXISTS idx_runs_bioproject ON runs(bioproject);
CREATE INDEX IF NOT EXISTS idx_sample_attributes_tag_value ON sample_attributes(tag, value);
CREATE INDEX IF NOT EXISTS idx_candidates_field_value ON annotation_candidates(normalized_field, raw_value);
CREATE INDEX IF NOT EXISTS idx_decisions_field_value ON annotation_decisions(field, raw_value);
""".strip()


TABLE_COLUMNS: dict[str, dict[str, str]] = {
    "projects": {
        "study_abstract": "TEXT",
        "center_name": "TEXT",
    },
    "annotation_candidates": {
        "status": "TEXT NOT NULL DEFAULT 'unreviewed'",
        "updated_at": "TEXT",
    },
    "annotation_decisions": {
        "status": "TEXT NOT NULL DEFAULT 'accepted'",
        "updated_at": "TEXT",
    },
}


def utc_now() -> str:
    """Return an ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


def sanitize_project_name(name: str) -> str:
    """Return the filesystem-safe project database stem."""
    cleaned = name.lower().strip()
    cleaned = re.sub(r"[\s\-]+", "_", cleaned)
    cleaned = re.sub(r"[^\w]", "", cleaned)
    return (cleaned or "unnamed")[:64]


def resolve_project_dir(project: str) -> Path:
    """Resolve the directory for a named project."""
    return get_data_dir() / sanitize_project_name(project)


def resolve_scope_path(project: str) -> Path:
    """Resolve the scope.md path for a named project."""
    sanitized = sanitize_project_name(project)
    project_dir = resolve_project_dir(sanitized)
    if project_dir.exists() or not (get_data_dir() / f"{sanitized}.db").exists():
        return project_dir / "scope.md"
    return get_data_dir() / f"{sanitized}.scope.md"


def resolve_db_path(project: str = "", db_path: str = "") -> Path:
    """Resolve a project or explicit database path."""
    if db_path:
        return Path(db_path).expanduser().resolve()
    if project:
        sanitized = sanitize_project_name(project)
        project_dir = resolve_project_dir(sanitized)
        directory_db = project_dir / f"{sanitized}.db"
        legacy_db = get_data_dir() / f"{sanitized}.db"
        if directory_db.exists() or project_dir.exists():
            return directory_db
        if legacy_db.exists():
            return legacy_db
        return directory_db
    return get_data_dir() / "sra_curator.db"


def connect(project: str = "", db_path: str = "") -> sqlite3.Connection:
    """Open a SQLite connection and initialize/migrate the schema."""
    path = resolve_db_path(project, db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    init_db(conn)
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Create or migrate the current database schema."""
    conn.executescript(CORE_SCHEMA)
    _ensure_columns(conn)
    conn.execute(
        "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
        (SCHEMA_VERSION, utc_now()),
    )
    conn.commit()


def _ensure_columns(conn: sqlite3.Connection) -> None:
    """Add known columns missing from older development databases."""
    for table, columns in TABLE_COLUMNS.items():
        existing = {
            row[1]
            for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if not existing:
            continue
        for column, definition in columns.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def create_project(
    name: str,
    description: str = "",
    organisms: list[str] | None = None,
    assays: list[str] | None = None,
    genes: list[str] | None = None,
    cell_lines: list[str] | None = None,
    tissues: list[str] | None = None,
    developmental_stages: list[str] | None = None,
    mutations: list[str] | None = None,
    diseases: list[str] | None = None,
    treatments: list[str] | None = None,
) -> dict[str, Any]:
    """Create or initialize a named project database."""
    sanitized = sanitize_project_name(name)
    project_dir = resolve_project_dir(sanitized)
    legacy_db = get_data_dir() / f"{sanitized}.db"
    if legacy_db.exists() and not project_dir.exists():
        db_path = legacy_db
        scope_path = get_data_dir() / f"{sanitized}.scope.md"
    else:
        project_dir.mkdir(parents=True, exist_ok=True)
        db_path = project_dir / f"{sanitized}.db"
        scope_path = project_dir / "scope.md"
    scope_values = {
        "organisms": organisms or [],
        "assays": assays or [],
        "genes": genes or [],
        "cell_lines": cell_lines or [],
        "tissues": tissues or [],
        "developmental_stages": developmental_stages or [],
        "mutations": mutations or [],
        "diseases": diseases or [],
        "treatments": treatments or [],
    }
    write_scope_file(scope_path, name=name, description=description, **scope_values)
    with connect(db_path=str(db_path)) as conn:
        existing = conn.execute("SELECT name FROM project_info LIMIT 1").fetchone()
        if not existing:
            conn.execute(
                "INSERT INTO project_info (name, description, created_at) VALUES (?, ?, ?)",
                (name, description, utc_now()),
            )
            conn.commit()

    return {
        "project": sanitized,
        "name": name,
        "description": description,
        "db_path": str(db_path),
        "project_dir": str(project_dir if db_path.parent == project_dir else db_path.parent),
        "scope_path": str(scope_path),
        "already_existed": existing is not None,
    }


def list_projects() -> dict[str, Any]:
    """List project databases in the data directory."""
    projects: list[dict[str, Any]] = []
    db_files = list(get_data_dir().glob("*.db"))
    db_files.extend(get_data_dir().glob("*/*.db"))
    for db_file in sorted(set(db_files)):
        entry: dict[str, Any] = {"project": db_file.stem, "db_path": str(db_file)}
        scope_path = db_file.parent / "scope.md"
        if scope_path.exists():
            entry["scope_path"] = str(scope_path)
        try:
            with connect(db_path=str(db_file)) as conn:
                info = conn.execute(
                    "SELECT name, description, created_at FROM project_info LIMIT 1"
                ).fetchone()
                if info:
                    entry["name"] = info[0]
                    entry["description"] = info[1] or ""
                    entry["created_at"] = info[2]
                run_count = conn.execute("SELECT COUNT(*) FROM runs").fetchone()
                candidate_count = conn.execute(
                    "SELECT COUNT(*) FROM annotation_candidates"
                ).fetchone()
                decision_count = conn.execute(
                    "SELECT COUNT(*) FROM annotation_decisions"
                ).fetchone()
                entry["run_count"] = run_count[0] if run_count else 0
                entry["candidate_count"] = candidate_count[0] if candidate_count else 0
                entry["decision_count"] = decision_count[0] if decision_count else 0
        except sqlite3.Error:
            entry["run_count"] = None
        projects.append(entry)
    return {"projects": projects, "count": len(projects)}


def load_project_scope(project: str) -> ProjectScope:
    """Load a project's scope.md, returning an empty scope if absent."""
    return read_scope(resolve_scope_path(project))


def show_project_scope(project: str) -> dict[str, Any]:
    """Return a parsed project scope."""
    scope_path = resolve_scope_path(project)
    scope = read_scope(scope_path)
    return {
        "project": sanitize_project_name(project),
        "scope_path": str(scope_path),
        "exists": scope_path.exists(),
        "scope": scope.to_dict(),
    }


def insert_runs(conn: sqlite3.Connection, runs: list[dict], search_query: str) -> tuple[int, int]:
    """Insert parsed runs and sample attributes."""
    inserted_at = utc_now()
    inserted = 0
    skipped = 0
    for run in runs:
        row = dict(run)
        attrs = row.pop("sample_attributes", {})
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO runs (
                run, published, total_spots, total_bases, size_bytes,
                experiment_accession, experiment_title,
                library_strategy, library_source, library_selection, library_layout,
                library_construction_protocol, platform, instrument_model,
                study_accession, study_title, study_abstract,
                bioproject, pubmed_id,
                sample_accession, sample_title, taxon_id, organism, biosample,
                center_name, submission_accession, search_query, inserted_at
            ) VALUES (
                :run, :published, :total_spots, :total_bases, :size_bytes,
                :experiment_accession, :experiment_title,
                :library_strategy, :library_source, :library_selection, :library_layout,
                :library_construction_protocol, :platform, :instrument_model,
                :study_accession, :study_title, :study_abstract,
                :bioproject, :pubmed_id,
                :sample_accession, :sample_title, :taxon_id, :organism, :biosample,
                :center_name, :submission_accession, :search_query, :inserted_at
            )
            """,
            {**row, "search_query": search_query, "inserted_at": inserted_at},
        )
        if cur.rowcount:
            inserted += 1
            for tag, value in attrs.items():
                conn.execute(
                    "INSERT OR IGNORE INTO sample_attributes (run, tag, value) VALUES (?, ?, ?)",
                    (row["run"], tag, value),
                )
        else:
            skipped += 1
    conn.commit()
    return inserted, skipped


def filter_runs_by_scope(runs: list[dict], scope: ProjectScope) -> tuple[list[dict], int]:
    """Return runs matching a scope and the number filtered out."""
    if scope.is_empty():
        return runs, 0
    kept = [run for run in runs if scope.matches_run(run)]
    return kept, len(runs) - len(kept)


async def save_search(query: str, project: str, max_results: int = PROJECT_SAMPLE_SIZE) -> dict[str, Any]:
    """Search SRA, group sampled results by BioProject, and save project inventory."""
    from sra_curator.ncbi import efetch_xml_by_ids, esearch_ids

    ids, total_count = await esearch_ids(query, retmax=max_results)
    if not ids:
        return {
            "inserted": 0,
            "skipped": 0,
            "total_projects": 0,
            "total_run_count": 0,
            "sampled": False,
            "db_path": str(resolve_db_path(project=project)),
        }

    runs = parse_sra_xml(await efetch_xml_by_ids(ids))
    grouped: dict[str, dict[str, Any]] = {}
    for run in runs:
        bioproject = run.get("bioproject") or "unknown"
        if bioproject not in grouped:
            grouped[bioproject] = {
                "bioproject": bioproject,
                "study_accession": run.get("study_accession", ""),
                "study_title": run.get("study_title", ""),
                "organism": run.get("organism", ""),
                "pubmed_id": run.get("pubmed_id", ""),
                "study_abstract": run.get("study_abstract", ""),
                "center_name": run.get("center_name", ""),
                "run_count": 0,
            }
        grouped[bioproject]["run_count"] += 1

    inserted = skipped = 0
    with connect(project=project) as conn:
        for row in grouped.values():
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO projects
                    (bioproject, study_accession, study_title, organism, pubmed_id,
                     study_abstract, center_name, run_count, search_query, inserted_at)
                VALUES
                    (:bioproject, :study_accession, :study_title, :organism, :pubmed_id,
                     :study_abstract, :center_name, :run_count, :search_query, :inserted_at)
                """,
                {**row, "search_query": query, "inserted_at": utc_now()},
            )
            inserted += 1 if cur.rowcount else 0
            skipped += 0 if cur.rowcount else 1
        conn.commit()

    return {
        "inserted": inserted,
        "skipped": skipped,
        "total_projects": len(grouped),
        "total_run_count": total_count,
        "sampled": total_count > max_results,
        "db_path": str(resolve_db_path(project=project)),
    }


async def search_projects(query: str, max_projects: int = 20) -> dict[str, Any]:
    """Search SRA and return BioProject groups without saving."""
    from sra_curator.ncbi import efetch_xml_by_ids, esearch_ids

    ids, total_count = await esearch_ids(query, retmax=PROJECT_SAMPLE_SIZE)
    if not ids:
        return {"projects": [], "total_run_count": 0, "sampled": False}

    runs = parse_sra_xml(await efetch_xml_by_ids(ids))
    grouped: dict[str, dict[str, Any]] = {}
    for run in runs:
        bioproject = run.get("bioproject") or "unknown"
        if bioproject not in grouped:
            grouped[bioproject] = {
                "bioproject": bioproject,
                "study_accession": run.get("study_accession", ""),
                "study_title": run.get("study_title", ""),
                "organism": run.get("organism", ""),
                "pubmed_id": run.get("pubmed_id", ""),
                "run_count": 0,
            }
        grouped[bioproject]["run_count"] += 1

    projects = sorted(grouped.values(), key=lambda row: row["run_count"], reverse=True)
    return {
        "projects": projects[:max_projects],
        "total_run_count": total_count,
        "sampled": total_count > PROJECT_SAMPLE_SIZE,
    }


async def ingest_bioproject(
    bioproject: str,
    project: str,
    max_results: int = 500,
) -> dict[str, Any]:
    """Fetch full run metadata for a BioProject and save it."""
    from sra_curator.ncbi import fetch_bioproject_runs

    runs, experiment_count = await fetch_bioproject_runs(bioproject, max_results=max_results)
    if not runs:
        return {
            "inserted": 0,
            "skipped": 0,
            "db_path": str(resolve_db_path(project=project)),
            "total_experiments_found": 0,
            "truncated": False,
        }

    scope = load_project_scope(project)
    scoped_runs, filtered_out = filter_runs_by_scope(runs, scope)
    with connect(project=project) as conn:
        inserted, skipped = insert_runs(conn, scoped_runs, search_query=bioproject)

    return {
        "inserted": inserted,
        "skipped": skipped,
        "db_path": str(resolve_db_path(project=project)),
        "total_experiments_found": experiment_count,
        "runs_returned": len(runs),
        "runs_matched_scope": len(scoped_runs),
        "filtered_out": filtered_out,
        "scope_applied": not scope.is_empty(),
        "truncated": experiment_count > max_results,
    }


async def ingest_project_runs(
    project: str,
    bioproject: str = "",
    max_results: int = 1000,
    delay_seconds: float = 1.0,
) -> dict[str, Any]:
    """Ingest run metadata for one or all BioProjects in a saved inventory."""
    import asyncio

    with connect(project=project) as conn:
        if bioproject:
            rows = conn.execute(
                "SELECT bioproject FROM projects WHERE bioproject = ? ORDER BY bioproject",
                (bioproject,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT bioproject FROM projects ORDER BY bioproject"
            ).fetchall()

    bioprojects = [row[0] for row in rows]
    if bioproject and not bioprojects:
        raise ValueError(f"BioProject {bioproject} is not present in project inventory.")

    results: list[dict[str, Any]] = []
    for index, bp in enumerate(bioprojects):
        try:
            result = await ingest_bioproject(
                bp,
                project=project,
                max_results=max_results,
            )
            result["bioproject"] = bp
        except Exception as exc:
            result = {"bioproject": bp, "error": str(exc)}
        results.append(result)
        if delay_seconds and index < len(bioprojects) - 1:
            await asyncio.sleep(delay_seconds)

    return {
        "project": sanitize_project_name(project),
        "requested_bioproject": bioproject or None,
        "projects_seen": len(bioprojects),
        "inserted": sum(int(row.get("inserted", 0)) for row in results),
        "skipped": sum(int(row.get("skipped", 0)) for row in results),
        "filtered_out": sum(int(row.get("filtered_out", 0)) for row in results),
        "errors": [row for row in results if row.get("error")],
        "results": results,
    }


def read_only_query(sql: str, project: str = "", db_path: str = "") -> dict[str, Any]:
    """Run a capped read-only SELECT query."""
    if not sql.strip().upper().startswith("SELECT"):
        raise ValueError("Only SELECT statements are permitted.")
    path = resolve_db_path(project=project, db_path=db_path)
    if not path.exists():
        raise FileNotFoundError(f"Database not found: {path}")
    with connect(db_path=str(path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql).fetchall()
    return {
        "rows": [dict(row) for row in rows[:MAX_QUERY_ROWS]],
        "total_count": len(rows),
        "returned": min(len(rows), MAX_QUERY_ROWS),
        "truncated": len(rows) > MAX_QUERY_ROWS,
    }


def schema(project: str = "", db_path: str = "") -> dict[str, str]:
    """Return CREATE TABLE statements for a database."""
    path = resolve_db_path(project=project, db_path=db_path)
    if not path.exists():
        return {"schema": CORE_SCHEMA, "note": "Database does not exist yet."}
    with connect(db_path=str(path)) as conn:
        rows = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    return {"schema": "\n\n".join(row[0] for row in rows if row[0]), "db_path": str(path)}


def find_candidate_id(
    conn: sqlite3.Connection,
    field: str,
    raw_value: str,
) -> int | None:
    """Find the first candidate matching a normalized field/raw value pair."""
    row = conn.execute(
        """
        SELECT candidate_id
        FROM annotation_candidates
        WHERE normalized_field = ? AND raw_value = ?
        ORDER BY run_count DESC, candidate_id
        LIMIT 1
        """,
        (field, raw_value),
    ).fetchone()
    return int(row[0]) if row else None


def add_annotation_decision(
    project: str,
    field: str,
    raw_value: str,
    ontology: str,
    term_id: str,
    term_label: str,
    confidence: float,
    method: str,
    evidence: str = "",
    notes: str = "",
    reviewer: str = "codex",
    status: str = "accepted",
) -> dict[str, Any]:
    """Store a curated ontology decision."""
    now = utc_now()
    with connect(project=project) as conn:
        candidate_id = find_candidate_id(conn, field, raw_value)
        cur = conn.execute(
            """
            INSERT INTO annotation_decisions (
                candidate_id, field, raw_value, ontology, term_id, term_label,
                confidence, method, evidence, notes, reviewer, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                candidate_id,
                field,
                raw_value,
                ontology,
                term_id,
                term_label,
                confidence,
                method,
                evidence,
                notes,
                reviewer,
                status,
                now,
                now,
            ),
        )
        if candidate_id is not None:
            conn.execute(
                "UPDATE annotation_candidates SET status = 'reviewed', updated_at = ? WHERE candidate_id = ?",
                (now, candidate_id),
            )
        conn.commit()
        decision_id = int(cur.lastrowid)

    return {
        "decision_id": decision_id,
        "candidate_id": candidate_id,
        "project": sanitize_project_name(project),
        "field": field,
        "raw_value": raw_value,
        "ontology": ontology,
        "term_id": term_id,
        "term_label": term_label,
        "confidence": confidence,
        "method": method,
        "status": status,
    }


def list_annotation_decisions(project: str, status: str = "") -> list[dict[str, Any]]:
    """List annotation decisions for a project."""
    sql = """
        SELECT
            d.decision_id,
            d.candidate_id,
            d.field,
            d.raw_value,
            d.ontology,
            d.term_id,
            d.term_label,
            d.confidence,
            d.method,
            d.evidence,
            d.notes,
            d.reviewer,
            d.status,
            d.created_at,
            c.source_field,
            c.bioproject,
            c.run_count
        FROM annotation_decisions d
        LEFT JOIN annotation_candidates c ON d.candidate_id = c.candidate_id
    """
    params: tuple[str, ...] = ()
    if status:
        sql += " WHERE d.status = ?"
        params = (status,)
    sql += " ORDER BY d.field, d.raw_value, d.decision_id"
    with connect(project=project) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def export_annotations(project: str, fmt: str, out: str) -> dict[str, Any]:
    """Export annotation decisions as JSON or CSV."""
    rows = list_annotation_decisions(project)
    out_path = Path(out).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if fmt == "json":
        out_path.write_text(
            json.dumps({"project": sanitize_project_name(project), "annotations": rows}, indent=2),
            encoding="utf-8",
        )
    elif fmt == "csv":
        with out_path.open("w", encoding="utf-8", newline="") as handle:
            fieldnames = [
                "decision_id",
                "candidate_id",
                "field",
                "raw_value",
                "ontology",
                "term_id",
                "term_label",
                "confidence",
                "method",
                "evidence",
                "notes",
                "reviewer",
                "status",
                "source_field",
                "bioproject",
                "run_count",
            ]
            writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
    else:
        raise ValueError("format must be 'json' or 'csv'")

    return {"project": sanitize_project_name(project), "format": fmt, "out": str(out_path), "rows": len(rows)}
