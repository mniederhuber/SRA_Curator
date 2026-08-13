# sra-curator

`sra-curator` is a Python CLI workbench for building ontology-normalized maps of
NCBI SRA datasets. It is designed for Codex-operated curation: the app handles
SRA search, local SQLite storage, candidate extraction, ontology lookup, and
exports, while Codex helps review messy metadata and record curated decisions.

The project used to be MCP-first. MCP is now deferred as a future integration
layer around the same core functions.

## Quickstart

Install and run with uv:

```bash
uv sync --dev
uv run sra-curator --help
```

Create a project database:

```bash
uv run sra-curator project create mouse_ap1_cutnrun \
  --description "Mouse AP-1 CUT&RUN and ChIP-seq datasets" \
  --organism "Mus musculus" \
  --assay "CUT&RUN" \
  --assay "ChIP-seq"
```

New projects are stored as directories containing both a database and scope file:

```text
$SRA_CURATOR_DATA_DIR/mouse_ap1_cutnrun/
  mouse_ap1_cutnrun.db
  scope.md
```

The scope file defines optional inclusion parameters used during run metadata
ingest: organisms, assays, genes, cell lines, tissues, developmental stages,
mutations, diseases, and treatments.

Search SRA and save a BioProject inventory:

```bash
uv run sra-curator search "mouse AP-1 CUT&RUN ChIP-seq" \
  --project mouse_ap1_cutnrun \
  --max-results 500
```

Ingest full run metadata for a BioProject:

```bash
uv run sra-curator ingest-bioproject PRJNA123456 \
  --project mouse_ap1_cutnrun
```

Or ingest run metadata for every BioProject already saved in a project inventory:

```bash
uv run sra-curator ingest-runs \
  --project mouse_ap1_cutnrun
```

When `scope.md` contains parameters, ingest filters fetched runs before insertion
and reports `filtered_out`.

Limit ingestion to one saved BioProject:

```bash
uv run sra-curator ingest-runs \
  --project mouse_ap1_cutnrun \
  --bioproject PRJNA123456
```

Extract annotation candidates:

```bash
uv run sra-curator candidates extract \
  --project mouse_ap1_cutnrun \
  --bioproject PRJNA123456
```

List candidates for review:

```bash
uv run sra-curator candidates list \
  --project mouse_ap1_cutnrun \
  --status unreviewed
```

Lookup ontology terms:

```bash
uv run sra-curator ontology lookup \
  --project mouse_ap1_cutnrun \
  --field disease \
  --value "lung adenocarcinoma"
```

Store a Codex-reviewed annotation decision:

```bash
uv run sra-curator annotation add \
  --project mouse_ap1_cutnrun \
  --field disease \
  --raw-value "lung adenocarcinoma" \
  --ontology MONDO \
  --term-id MONDO:0005061 \
  --term-label "lung adenocarcinoma" \
  --confidence 0.95 \
  --method codex_reviewed \
  --evidence "SRA disease attribute exact match"
```

Export the curated map:

```bash
uv run sra-curator export \
  --project mouse_ap1_cutnrun \
  --format csv \
  --out exports/mouse_ap1_cutnrun.csv
```

## What Gets Normalized

V1 targets the core fields needed to make SRA metadata maps useful:

| Field | Source examples | Target ontology |
| --- | --- | --- |
| `disease` | disease, diagnosis, phenotype attributes | MONDO |
| `tissue` | tissue, organ, source name attributes | UBERON |
| `cell_type` | cell type, cell line attributes | CL |
| `assay` | library strategy, assay fields, title/protocol keywords | EFO, then OBI |
| `organism` | SRA organism and taxon metadata | NCBITaxon |
| `gene_target` | genotype, target, antibody fields | manual/Codex review in v1 |

Candidate extraction is conservative. It stages values for review instead of
silently applying inferred ontology mappings.

## Configuration

| Env var | Default | Description |
| --- | --- | --- |
| `SRA_CURATOR_DATA_DIR` | `~/sra-curator` | Base directory for project databases and local artifacts |
| `SRA_OUTPUT_DIR` | unset | Backward-compatible fallback for the data directory |
| `NCBI_API_KEY` | unset | Optional NCBI API key for higher E-utilities rate limits |
| `NCBI_EMAIL` | unset | Optional email passed to NCBI E-utilities |

New projects are stored as directories named after the sanitized project:

```text
$SRA_CURATOR_DATA_DIR/mouse_ap1_cutnrun/
  mouse_ap1_cutnrun.db
  scope.md
```

## Future MCP Wrapper

MCP is useful once the project is stable enough for other agent clients to use.
The intended future shape is a thin MCP wrapper around `sra_curator` core
functions, not a separate implementation.
