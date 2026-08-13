"""Command-line interface for SRA Curator."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from sra_curator import candidates, db, ontology


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = args.func(args)
        if _is_awaitable(result):
            result = asyncio.run(result)
        if result is not None:
            print_json(result)
        return 0
    except Exception as exc:
        print_json({"error": str(exc)})
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sra-curator",
        description="Build ontology-normalized maps of SRA datasets.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    _add_project_commands(sub)
    _add_search_commands(sub)
    _add_candidate_commands(sub)
    _add_ontology_commands(sub)
    _add_annotation_commands(sub)
    _add_export_command(sub)
    return parser


def _add_project_commands(sub: argparse._SubParsersAction) -> None:
    project = sub.add_parser("project", help="Manage local curation projects")
    project_sub = project.add_subparsers(dest="project_command", required=True)

    create = project_sub.add_parser("create", help="Create a project database")
    create.add_argument("name")
    create.add_argument("--description", default="")
    create.add_argument("--organism", action="append", default=[])
    create.add_argument("--assay", action="append", default=[])
    create.add_argument("--gene", action="append", default=[])
    create.add_argument("--cell-line", action="append", default=[])
    create.add_argument("--tissue", action="append", default=[])
    create.add_argument("--developmental-stage", action="append", default=[])
    create.add_argument("--mutation", action="append", default=[])
    create.add_argument("--disease", action="append", default=[])
    create.add_argument("--treatment", action="append", default=[])
    create.set_defaults(
        func=lambda args: db.create_project(
            args.name,
            args.description,
            organisms=args.organism,
            assays=args.assay,
            genes=args.gene,
            cell_lines=args.cell_line,
            tissues=args.tissue,
            developmental_stages=args.developmental_stage,
            mutations=args.mutation,
            diseases=args.disease,
            treatments=args.treatment,
        )
    )

    list_cmd = project_sub.add_parser("list", help="List project databases")
    list_cmd.set_defaults(func=lambda args: db.list_projects())

    scope = project_sub.add_parser("scope", help="Show the parsed scope for a project")
    scope.add_argument("name")
    scope.set_defaults(func=lambda args: db.show_project_scope(args.name))


def _add_search_commands(sub: argparse._SubParsersAction) -> None:
    search = sub.add_parser("search", help="Search SRA and save a BioProject inventory")
    search.add_argument("query", nargs="+")
    search.add_argument("--project", required=True)
    search.add_argument("--max-results", type=int, default=db.PROJECT_SAMPLE_SIZE)
    search.set_defaults(
        func=lambda args: db.save_search(
            _join_query(args.query),
            project=args.project,
            max_results=args.max_results,
        )
    )

    search_projects = sub.add_parser("search-projects", help="Search SRA without saving")
    search_projects.add_argument("query", nargs="+")
    search_projects.add_argument("--max-projects", type=int, default=20)
    search_projects.set_defaults(
        func=lambda args: db.search_projects(
            _join_query(args.query),
            max_projects=args.max_projects,
        )
    )

    ingest = sub.add_parser("ingest-bioproject", help="Ingest full run metadata for a BioProject")
    ingest.add_argument("bioproject")
    ingest.add_argument("--project", required=True)
    ingest.add_argument("--max-results", type=int, default=500)
    ingest.set_defaults(
        func=lambda args: db.ingest_bioproject(
            args.bioproject,
            project=args.project,
            max_results=args.max_results,
        )
    )

    ingest_runs = sub.add_parser(
        "ingest-runs",
        help="Ingest run metadata for all saved BioProjects, or one selected BioProject",
    )
    ingest_runs.add_argument("--project", required=True)
    ingest_runs.add_argument("--bioproject", default="")
    ingest_runs.add_argument("--max-results", type=int, default=1000)
    ingest_runs.add_argument("--delay-seconds", type=float, default=1.0)
    ingest_runs.set_defaults(
        func=lambda args: db.ingest_project_runs(
            project=args.project,
            bioproject=args.bioproject,
            max_results=args.max_results,
            delay_seconds=args.delay_seconds,
        )
    )


def _add_candidate_commands(sub: argparse._SubParsersAction) -> None:
    cand = sub.add_parser("candidates", help="Extract or inspect annotation candidates")
    cand_sub = cand.add_subparsers(dest="candidate_command", required=True)

    extract = cand_sub.add_parser("extract", help="Extract candidates from stored metadata")
    extract.add_argument("--project", required=True)
    extract.add_argument("--bioproject", default="")
    extract.set_defaults(
        func=lambda args: candidates.extract_annotation_candidates(
            project=args.project,
            bioproject=args.bioproject,
        )
    )

    list_cmd = cand_sub.add_parser("list", help="List extracted candidates")
    list_cmd.add_argument("--project", required=True)
    list_cmd.add_argument("--status", default="")
    list_cmd.add_argument("--field", default="")
    list_cmd.set_defaults(
        func=lambda args: {
            "candidates": candidates.list_candidates(
                project=args.project,
                status=args.status,
                field=args.field,
            )
        }
    )


def _add_ontology_commands(sub: argparse._SubParsersAction) -> None:
    onto = sub.add_parser("ontology", help="Lookup ontology terms")
    onto_sub = onto.add_subparsers(dest="ontology_command", required=True)

    lookup = onto_sub.add_parser("lookup", help="Lookup a raw value")
    lookup.add_argument("--field", required=True)
    lookup.add_argument("--value", required=True)
    lookup.add_argument("--project", default="")
    lookup.add_argument("--ontology", default="")
    lookup.set_defaults(
        func=lambda args: ontology.lookup_ontology(
            field=args.field,
            raw_value=args.value,
            project=args.project,
            ontology=args.ontology,
        )
    )


def _add_annotation_commands(sub: argparse._SubParsersAction) -> None:
    ann = sub.add_parser("annotation", help="Manage curated annotation decisions")
    ann_sub = ann.add_subparsers(dest="annotation_command", required=True)

    add = ann_sub.add_parser("add", help="Store a curated annotation decision")
    add.add_argument("--project", required=True)
    add.add_argument("--field", required=True)
    add.add_argument("--raw-value", required=True)
    add.add_argument("--ontology", required=True)
    add.add_argument("--term-id", required=True)
    add.add_argument("--term-label", required=True)
    add.add_argument("--confidence", type=float, default=0.9)
    add.add_argument("--method", default="codex_reviewed")
    add.add_argument("--evidence", default="")
    add.add_argument("--notes", default="")
    add.add_argument("--reviewer", default="codex")
    add.add_argument("--status", default="accepted")
    add.set_defaults(
        func=lambda args: db.add_annotation_decision(
            project=args.project,
            field=args.field,
            raw_value=args.raw_value,
            ontology=args.ontology,
            term_id=args.term_id,
            term_label=args.term_label,
            confidence=args.confidence,
            method=args.method,
            evidence=args.evidence,
            notes=args.notes,
            reviewer=args.reviewer,
            status=args.status,
        )
    )

    list_cmd = ann_sub.add_parser("list", help="List annotation decisions")
    list_cmd.add_argument("--project", required=True)
    list_cmd.add_argument("--status", default="")
    list_cmd.set_defaults(
        func=lambda args: {
            "annotations": db.list_annotation_decisions(
                project=args.project,
                status=args.status,
            )
        }
    )


def _add_export_command(sub: argparse._SubParsersAction) -> None:
    export = sub.add_parser("export", help="Export curated annotations")
    export.add_argument("--project", required=True)
    export.add_argument("--format", choices=("csv", "json"), required=True)
    export.add_argument("--out", required=True)
    export.set_defaults(
        func=lambda args: db.export_annotations(
            project=args.project,
            fmt=args.format,
            out=args.out,
        )
    )


def _join_query(parts: list[str]) -> str:
    return " ".join(parts)


def print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def _is_awaitable(value: Any) -> bool:
    return hasattr(value, "__await__")


if __name__ == "__main__":
    sys.exit(main())
