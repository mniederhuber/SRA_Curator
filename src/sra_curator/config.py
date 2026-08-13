"""Configuration and path safety for SRA Curator."""

from __future__ import annotations

import os
from pathlib import Path


def get_data_dir() -> Path:
    """Return the base data directory, creating it if needed."""
    raw = os.environ.get("SRA_CURATOR_DATA_DIR") or os.environ.get("SRA_OUTPUT_DIR")
    base = Path(raw).expanduser().resolve() if raw else Path.home() / "sra-curator"
    base.mkdir(parents=True, exist_ok=True)
    return base


def safe_accession_path(accession: str) -> Path:
    """Return a validated path for an accession under the data directory."""
    clean = Path(accession).name
    if not clean or clean != accession:
        raise ValueError(f"Invalid accession: {accession!r}")

    base = get_data_dir()
    target = (base / clean).resolve()
    try:
        target.relative_to(base)
    except ValueError as exc:
        raise ValueError(f"Accession {accession!r} resolves outside data directory") from exc
    return target
