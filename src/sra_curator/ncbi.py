"""NCBI E-utilities access for SRA metadata."""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET

from sra_curator.parser import parse_sra_xml


EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
SUMMARY_KEYS = {
    "run",
    "bioproject",
    "study_accession",
    "organism",
    "library_strategy",
    "platform",
    "sample_title",
    "total_bases",
    "published",
}


def base_params() -> dict[str, str]:
    """Return common E-utilities parameters."""
    params = {"tool": "sra-curator", "db": "sra"}
    email = os.getenv("NCBI_EMAIL", "")
    api_key = os.getenv("NCBI_API_KEY", "")
    if email:
        params["email"] = email
    if api_key:
        params["api_key"] = api_key
    return params


async def esearch_ids(query: str, retmax: int) -> tuple[list[str], int]:
    """Search SRA and return experiment ids plus total hit count."""
    import httpx

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{EUTILS}/esearch.fcgi",
            data={**base_params(), "term": query, "retmax": str(retmax), "retmode": "json"},
        )
        resp.raise_for_status()
    data = resp.json()["esearchresult"]
    return data["idlist"], int(data["count"])


async def efetch_xml_by_ids(ids: list[str]) -> str:
    """Fetch full SRA XML by experiment ids."""
    import httpx

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{EUTILS}/efetch.fcgi",
            data={**base_params(), "id": ",".join(ids), "rettype": "full", "retmode": "xml"},
        )
        resp.raise_for_status()
    return resp.text


async def search_runs(
    query: str,
    max_results: int = 20,
    summarize: bool = False,
) -> list[dict]:
    """Search SRA and return parsed run metadata."""
    import httpx

    async with httpx.AsyncClient(timeout=60.0) as client:
        esearch_resp = await client.post(
            f"{EUTILS}/esearch.fcgi",
            data={**base_params(), "term": query, "usehistory": "y", "retmax": "0"},
        )
        esearch_resp.raise_for_status()

        root = ET.fromstring(esearch_resp.text)
        web_env = root.findtext("WebEnv")
        query_key = root.findtext("QueryKey")
        if not web_env or not query_key:
            return []

        efetch_resp = await client.post(
            f"{EUTILS}/efetch.fcgi",
            data={
                **base_params(),
                "query_key": query_key,
                "WebEnv": web_env,
                "retmax": str(max_results),
                "rettype": "full",
                "retmode": "xml",
            },
        )
        efetch_resp.raise_for_status()

    results = parse_sra_xml(efetch_resp.text)
    if summarize:
        return [{k: v for k, v in row.items() if k in SUMMARY_KEYS} for row in results]
    return results


async def fetch_bioproject_runs(
    bioproject: str,
    max_results: int = 500,
) -> tuple[list[dict], int]:
    """Fetch run metadata for a BioProject and return runs plus experiment count."""
    import httpx

    query = f"{bioproject}[BioProject]"
    async with httpx.AsyncClient(timeout=60.0) as client:
        search_resp = await client.post(
            f"{EUTILS}/esearch.fcgi",
            data={**base_params(), "term": query, "usehistory": "y", "retmax": "0"},
        )
        search_resp.raise_for_status()
        root = ET.fromstring(search_resp.text)
        web_env = root.findtext("WebEnv")
        query_key = root.findtext("QueryKey")
        total_count = int(root.findtext("Count") or 0)
        if not web_env or not query_key or total_count == 0:
            return [], 0

        efetch_resp = await client.post(
            f"{EUTILS}/efetch.fcgi",
            data={
                **base_params(),
                "query_key": query_key,
                "WebEnv": web_env,
                "retmax": str(min(max_results, total_count)),
                "rettype": "full",
                "retmode": "xml",
            },
        )
        efetch_resp.raise_for_status()

    return parse_sra_xml(efetch_resp.text), total_count
