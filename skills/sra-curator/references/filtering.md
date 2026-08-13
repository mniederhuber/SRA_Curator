# Filtering Rules

Use these rules before entity extraction. Prefer setting scope before ingest so
unwanted runs are never inserted. Use direct SQL deletion only to repair an
already-ingested database.

Project scope lives in `scope.md` beside the project DB. Standard parameters:

- organisms
- assays
- genes
- cell_lines
- tissues
- developmental_stages
- mutations
- diseases
- treatments

When a parameter list is populated, a run should match that category before it is
inserted. Keep the BioProject inventory in `projects`; constrain the working
run-level dataset in `runs` and `sample_attributes`.

## Repair Procedure For Already-Ingested DBs

1. Preview the current run universe.

```bash
sqlite3 DB 'SELECT COUNT(*) FROM runs;'
sqlite3 DB 'SELECT organism, COUNT(*) FROM runs GROUP BY organism ORDER BY COUNT(*) DESC;'
sqlite3 DB 'SELECT library_strategy, COUNT(*) FROM runs GROUP BY library_strategy ORDER BY COUNT(*) DESC;'
```

2. Define the keep predicate in plain English and SQL.

Example:

```sql
organism LIKE 'Oryza%'
AND library_strategy IN ('ATAC-seq', 'FAIRE-seq', 'DNase-Hypersensitivity')
```

3. Preview keep/delete counts before deleting.

```bash
sqlite3 DB 'SELECT COUNT(*) FROM runs WHERE KEEP_PREDICATE;'
sqlite3 DB 'SELECT COUNT(*) FROM runs WHERE NOT (KEEP_PREDICATE);'
```

4. Delete in one transaction. Delete dependent `sample_attributes` first.

```sql
BEGIN;
DELETE FROM sample_attributes
WHERE run IN (
  SELECT run FROM runs WHERE NOT (KEEP_PREDICATE)
);
DELETE FROM runs
WHERE NOT (KEEP_PREDICATE);
COMMIT;
```

5. Verify integrity.

```bash
sqlite3 DB 'SELECT COUNT(*) FROM sample_attributes sa LEFT JOIN runs r ON sa.run = r.run WHERE r.run IS NULL;'
sqlite3 DB 'SELECT COUNT(*) FROM runs WHERE NOT (KEEP_PREDICATE);'
```

Both verification counts must be `0`.

## Chromatin Accessibility Assays

For chromatin accessibility projects, keep these library strategies by default:

- `ATAC-seq`
- `FAIRE-seq`
- `DNase-Hypersensitivity`

Exclude these by default before entity extraction:

- `RNA-Seq`
- `ChIP-Seq`
- `Bisulfite-Seq`
- `Hi-C`
- `RIP-Seq`
- `miRNA-Seq`
- non-target organisms from multi-species BioProjects

Handle `OTHER` cautiously. Keep `OTHER` only when titles, protocol text, or
sample attributes explicitly support a chromatin accessibility assay. Otherwise
exclude it from the entity-extraction dataset.

## Organism Filtering

For species-specific projects, filter at the run level, not just the BioProject
level. BioProjects can bundle multiple organisms.

Rice example:

```sql
organism LIKE 'Oryza%'
```

Mouse example:

```sql
organism = 'Mus musculus'
```

After filtering, report retained runs by organism and assay.

## When Not To Delete

Create a SQLite view instead of deleting when:

- the user is exploring and has not committed to a target dataset;
- the keep predicate depends on subjective assay interpretation;
- `LibraryStrategy=OTHER` may contain relevant records that need manual review;
- the database is being used as a raw archive rather than a curated working set.
