"""Ontology lookup and caching."""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from sra_curator.db import connect, utc_now


OLS4_SEARCH = "https://www.ebi.ac.uk/ols4/api/search"

FIELD_ONTOLOGIES: dict[str, list[tuple[str, str]]] = {
    "disease": [("MONDO", "mondo")],
    "tissue": [("UBERON", "uberon")],
    "cell_type": [("CL", "cl")],
    "assay": [("EFO", "efo"), ("OBI", "obi")],
}

LOCAL_ORGANISMS = {
    "homo sapiens": ("NCBITaxon:9606", "Homo sapiens"),
    "mus musculus": ("NCBITaxon:10090", "Mus musculus"),
    "rattus norvegicus": ("NCBITaxon:10116", "Rattus norvegicus"),
    "danio rerio": ("NCBITaxon:7955", "Danio rerio"),
    "drosophila melanogaster": ("NCBITaxon:7227", "Drosophila melanogaster"),
    "caenorhabditis elegans": ("NCBITaxon:6239", "Caenorhabditis elegans"),
}


async def lookup_ontology(
    field: str,
    raw_value: str,
    project: str = "",
    ontology: str = "",
) -> dict[str, Any]:
    """Lookup a raw annotation value in the ontology configured for its field."""
    normalized_field = field.strip().lower()
    value = " ".join(raw_value.strip().split())

    if normalized_field == "organism":
        return _lookup_organism(value)

    if normalized_field == "gene_target":
        return {
            "field": normalized_field,
            "raw_value": value,
            "matched": False,
            "ontology": ontology or "HGNC/MGI",
            "method": "manual_required",
            "message": "Gene target normalization is staged for manual/Codex review in v1.",
        }

    candidates = FIELD_ONTOLOGIES.get(normalized_field)
    if not candidates:
        raise ValueError(
            "Unsupported field. Expected one of: disease, tissue, cell_type, assay, organism, gene_target."
        )

    if ontology:
        requested = ontology.upper()
        candidates = [(label, slug) for label, slug in candidates if label == requested]
        if not candidates:
            raise ValueError(f"Ontology {ontology!r} is not configured for field {field!r}.")

    for ontology_label, ontology_slug in candidates:
        cached = _get_cached(project, ontology_label, value) if project else None
        if cached is not None:
            return {**cached, "field": normalized_field, "raw_value": value, "cached": True}

        result = await _ols4_lookup(ontology_slug, value, exact=True)
        method = "exact"
        confidence = 1.0
        if result is None:
            result = await _ols4_lookup(ontology_slug, value, exact=False)
            method = "fuzzy"
            confidence = 0.75

        response = _format_result(
            field=normalized_field,
            raw_value=value,
            ontology=ontology_label,
            result=result,
            method=method if result else "no_match",
            confidence=confidence if result else 0.0,
            cached=False,
        )
        if project:
            _store_cached(project, response)
        if response["matched"]:
            return response

    return {
        "field": normalized_field,
        "raw_value": value,
        "matched": False,
        "ontology": candidates[-1][0],
        "method": "no_match",
        "confidence": 0.0,
        "cached": False,
    }


def _lookup_organism(value: str) -> dict[str, Any]:
    taxon_match = re.search(r"(?:taxon:|NCBITaxon:)?(\d{3,})", value, re.I)
    if taxon_match:
        term_id = f"NCBITaxon:{taxon_match.group(1)}"
        label = re.sub(r"\s*\[?taxon:?\s*\d+\]?", "", value, flags=re.I).strip() or term_id
        return {
            "field": "organism",
            "raw_value": value,
            "matched": True,
            "ontology": "NCBITaxon",
            "term_id": term_id,
            "term_label": label,
            "confidence": 1.0,
            "method": "taxon_id",
            "cached": False,
        }

    local = LOCAL_ORGANISMS.get(value.lower())
    if local:
        term_id, label = local
        return {
            "field": "organism",
            "raw_value": value,
            "matched": True,
            "ontology": "NCBITaxon",
            "term_id": term_id,
            "term_label": label,
            "confidence": 1.0,
            "method": "local",
            "cached": False,
        }

    return {
        "field": "organism",
        "raw_value": value,
        "matched": False,
        "ontology": "NCBITaxon",
        "method": "manual_required",
        "confidence": 0.0,
        "cached": False,
    }


async def _ols4_lookup(ontology: str, query: str, exact: bool) -> dict[str, Any] | None:
    import httpx

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                OLS4_SEARCH,
                params={
                    "q": query,
                    "ontology": ontology,
                    "exact": "true" if exact else "false",
                    "rows": "1",
                    "fieldList": "obo_id,label,ontology_prefix",
                },
            )
            resp.raise_for_status()
        docs = resp.json().get("response", {}).get("docs", [])
        if not docs:
            return None
        doc = docs[0]
        return {
            "term_id": doc.get("obo_id", ""),
            "term_label": doc.get("label", ""),
            "raw_json": doc,
        }
    except (httpx.HTTPError, KeyError, ValueError):
        return None


def _format_result(
    field: str,
    raw_value: str,
    ontology: str,
    result: dict[str, Any] | None,
    method: str,
    confidence: float,
    cached: bool,
) -> dict[str, Any]:
    return {
        "field": field,
        "raw_value": raw_value,
        "matched": result is not None,
        "ontology": ontology,
        "term_id": result["term_id"] if result else None,
        "term_label": result["term_label"] if result else None,
        "confidence": confidence,
        "method": method,
        "cached": cached,
        "raw_json": result.get("raw_json") if result else None,
    }


def _get_cached(project: str, ontology: str, query: str) -> dict[str, Any] | None:
    with connect(project=project) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT ontology, query, matched, term_id, term_label, confidence, method, raw_json
            FROM ontology_cache
            WHERE ontology = ? AND query = ?
            """,
            (ontology, query),
        ).fetchone()
    if row is None:
        return None
    return {
        "matched": bool(row["matched"]),
        "ontology": row["ontology"],
        "term_id": row["term_id"],
        "term_label": row["term_label"],
        "confidence": row["confidence"],
        "method": row["method"],
        "raw_json": json.loads(row["raw_json"]) if row["raw_json"] else None,
    }


def _store_cached(project: str, response: dict[str, Any]) -> None:
    now = utc_now()
    raw_json = json.dumps(response.get("raw_json")) if response.get("raw_json") else None
    with connect(project=project) as conn:
        conn.execute(
            """
            INSERT INTO ontology_cache (
                ontology, query, matched, term_id, term_label, confidence,
                method, raw_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (ontology, query)
            DO UPDATE SET
                matched = excluded.matched,
                term_id = excluded.term_id,
                term_label = excluded.term_label,
                confidence = excluded.confidence,
                method = excluded.method,
                raw_json = excluded.raw_json,
                updated_at = excluded.updated_at
            """,
            (
                response["ontology"],
                response["raw_value"],
                1 if response["matched"] else 0,
                response.get("term_id"),
                response.get("term_label"),
                response.get("confidence"),
                response.get("method"),
                raw_json,
                now,
                now,
            ),
        )
        conn.commit()
