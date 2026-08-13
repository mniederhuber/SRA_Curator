"""SRA XML parsing utilities."""

from __future__ import annotations

import xml.etree.ElementTree as ET


def parse_sample_attributes(sample_el: ET.Element) -> dict[str, str]:
    """Parse SRA sample attributes into a simple tag/value mapping."""
    attrs: dict[str, str] = {}
    for attr in sample_el.findall("SAMPLE_ATTRIBUTES/SAMPLE_ATTRIBUTE"):
        tag = attr.findtext("TAG", "").strip()
        value = attr.findtext("VALUE", "").strip()
        if tag:
            attrs[tag] = value
    return attrs


def parse_sra_xml(xml_text: str) -> list[dict]:
    """Parse full SRA experiment package XML into one dict per run."""
    root = ET.fromstring(xml_text)
    results: list[dict] = []

    for pkg in root.findall("EXPERIMENT_PACKAGE"):
        exp = pkg.find("EXPERIMENT")
        study = pkg.find("STUDY")
        sample = pkg.find("SAMPLE")
        submission = pkg.find("SUBMISSION")

        exp_accession = exp.get("accession", "") if exp is not None else ""
        exp_title = exp.findtext("TITLE", "") if exp is not None else ""

        lib = exp.find("DESIGN/LIBRARY_DESCRIPTOR") if exp is not None else None
        library_strategy = lib.findtext("LIBRARY_STRATEGY", "") if lib is not None else ""
        library_source = lib.findtext("LIBRARY_SOURCE", "") if lib is not None else ""
        library_selection = lib.findtext("LIBRARY_SELECTION", "") if lib is not None else ""
        library_layout = (
            "PAIRED"
            if lib is not None and lib.find("LIBRARY_LAYOUT/PAIRED") is not None
            else "SINGLE"
        )
        library_protocol = (
            lib.findtext("LIBRARY_CONSTRUCTION_PROTOCOL", "") if lib is not None else ""
        )

        platform_el = exp.find("PLATFORM") if exp is not None else None
        instrument_model = ""
        platform = ""
        if platform_el is not None:
            for child in platform_el:
                platform = child.tag
                instrument_model = child.findtext("INSTRUMENT_MODEL", "")
                break

        study_accession = study.get("accession", "") if study is not None else ""
        study_title = study.findtext("DESCRIPTOR/STUDY_TITLE", "") if study is not None else ""
        study_abstract = (
            study.findtext("DESCRIPTOR/STUDY_ABSTRACT", "") if study is not None else ""
        )
        bioproject = ""
        if study is not None:
            for ext in study.findall("IDENTIFIERS/EXTERNAL_ID"):
                if ext.get("namespace") == "BioProject":
                    bioproject = ext.text or ""
                    break

        pubmed_id = ""
        if study is not None:
            for link in study.findall("STUDY_LINKS/STUDY_LINK/XREF_LINK"):
                if link.findtext("DB", "").lower() == "pubmed":
                    pubmed_id = link.findtext("ID", "")
                    break

        sample_accession = sample.get("accession", "") if sample is not None else ""
        sample_title = sample.findtext("TITLE", "") if sample is not None else ""
        taxon_id = sample.findtext("SAMPLE_NAME/TAXON_ID", "") if sample is not None else ""
        organism = (
            sample.findtext("SAMPLE_NAME/SCIENTIFIC_NAME", "") if sample is not None else ""
        )
        biosample = ""
        if sample is not None:
            for ext in sample.findall("IDENTIFIERS/EXTERNAL_ID"):
                if ext.get("namespace") == "BioSample":
                    biosample = ext.text or ""
                    break
        sample_attributes = parse_sample_attributes(sample) if sample is not None else {}

        center_name = submission.get("center_name", "") if submission is not None else ""
        submission_accession = submission.get("accession", "") if submission is not None else ""

        for run in pkg.findall("RUN_SET/RUN"):
            results.append(
                {
                    "run": run.get("accession", ""),
                    "published": run.get("published", ""),
                    "is_public": run.get("is_public", ""),
                    "total_spots": run.get("total_spots", ""),
                    "total_bases": run.get("total_bases", ""),
                    "size_bytes": run.get("size", ""),
                    "experiment_accession": exp_accession,
                    "experiment_title": exp_title,
                    "library_strategy": library_strategy,
                    "library_source": library_source,
                    "library_selection": library_selection,
                    "library_layout": library_layout,
                    "library_construction_protocol": library_protocol,
                    "platform": platform,
                    "instrument_model": instrument_model,
                    "study_accession": study_accession,
                    "study_title": study_title,
                    "study_abstract": study_abstract,
                    "bioproject": bioproject,
                    "pubmed_id": pubmed_id,
                    "sample_accession": sample_accession,
                    "sample_title": sample_title,
                    "taxon_id": taxon_id,
                    "organism": organism,
                    "biosample": biosample,
                    "sample_attributes": sample_attributes,
                    "center_name": center_name,
                    "submission_accession": submission_accession,
                }
            )

    return results
