---
name: sra-curator
description: Use when Codex needs to curate NCBI SRA datasets with the local sra-curator CLI: create/search project databases, ingest BioProject run metadata, filter runs by organism/assay before entity extraction, extract title/attribute candidates, normalize metadata to ontologies, store reviewed annotation decisions, or export curated SRA maps.
---

# SRA Curator

Use `sra-curator` as a local CLI workbench for building ontology-normalized SRA
dataset maps. Each new project is a directory with a SQLite DB and `scope.md`.
Keep raw search inventory separate from run-level curation: `projects` records
the BioProject inventory; `runs` and `sample_attributes` hold only run metadata
that matches the project scope.

## Setup

Run commands from the repo root.

```bash
uv sync --dev
SRA_CURATOR_DATA_DIR="$PWD/sra-data" uv run sra-curator --help
```

If `uv` cannot write its default cache inside a sandbox, prefix commands with:

```bash
UV_CACHE_DIR=/tmp/sra-curator-uv-cache
```

## Workflow

1. Create a project with scope parameters.

```bash
uv run sra-curator project create PROJECT \
  --description "..." \
  --organism "..." \
  --assay "..."
```

Review `scope.md` before ingest. Standard optional scope parameters are:
organisms, assays, genes, cell lines, tissues, developmental stages, mutations,
diseases, and treatments.

2. Search broadly and save BioProject inventory. Use multiple specific search
   phrases rather than one clever query; duplicates are skipped.

```bash
uv run sra-curator search "QUERY" --project PROJECT --max-results 500
```

3. Ingest run-level metadata for the saved BioProjects. Ingest reads `scope.md`
   and inserts only runs that reasonably match all populated scope categories.

```bash
uv run sra-curator ingest-runs --project PROJECT --max-results 1000 --delay-seconds 1
```

4. Confirm run-level scope before entity extraction.

Read `references/filtering.md` before changing filters, deleting rows, or
repairing a DB ingested before scope rules were set.

5. Extract candidates from filtered runs.

```bash
uv run sra-curator candidates extract --project PROJECT
uv run sra-curator candidates list --project PROJECT --status unreviewed
```

6. Normalize and store reviewed decisions with evidence.

```bash
uv run sra-curator ontology lookup --project PROJECT --field FIELD --value "raw value"
uv run sra-curator annotation add \
  --project PROJECT \
  --field FIELD \
  --raw-value "raw value" \
  --ontology ONTOLOGY \
  --term-id TERM_ID \
  --term-label "label" \
  --confidence 0.9 \
  --method codex_reviewed \
  --evidence "SRA attribute/title/protocol or cited source"
```

7. Export curated decisions.

```bash
uv run sra-curator export --project PROJECT --format csv --out exports/PROJECT.csv
```

## Curation Rules

- Prefer exact run/sample attributes over title-only inference.
- Do not normalize ambiguous values silently; leave them unreviewed or store a
  low-confidence decision with notes.
- Use `method=codex_reviewed` when judgment beyond exact lookup is involved.
- Include concrete evidence in every stored annotation decision.
- Treat `LibraryStrategy=OTHER` as untrusted until title/protocol/sample metadata
  supports a specific assay.
- Preserve BioProject inventory rows unless the user explicitly asks to remove
  projects; most filtering should operate on `runs` plus `sample_attributes`.
