"""Project scope files and run-level matching."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


SCOPE_FIELDS = (
    "organisms",
    "assays",
    "genes",
    "cell_lines",
    "tissues",
    "developmental_stages",
    "mutations",
    "diseases",
    "treatments",
)

RULE_LIST_FIELDS = (
    "library_strategy_keep",
    "library_strategy_exclude",
)

LIST_FIELDS = SCOPE_FIELDS + RULE_LIST_FIELDS

ACCESSIBILITY_STRATEGIES = {
    "atac-seq",
    "faire-seq",
    "dnase-hypersensitivity",
}

STRATEGY_LABELS = {
    "atac-seq": "ATAC-seq",
    "faire-seq": "FAIRE-seq",
    "dnase-hypersensitivity": "DNase-Hypersensitivity",
}

ACCESSIBILITY_EXCLUDE_STRATEGIES = (
    "RNA-Seq",
    "ChIP-Seq",
    "Bisulfite-Seq",
    "Hi-C",
    "RIP-Seq",
    "miRNA-Seq",
)

DEFAULT_OTHER_STRATEGY_POLICY = "require_assay_evidence"

ASSAY_ALIASES = {
    "atac": {"atac-seq"},
    "atac-seq": {"atac-seq"},
    "faire": {"faire-seq"},
    "faire-seq": {"faire-seq"},
    "dnase": {"dnase-hypersensitivity"},
    "dnase-seq": {"dnase-hypersensitivity"},
    "dnase-hypersensitivity": {"dnase-seq", "dnase-hypersensitivity"},
    "chromatin accessibility": ACCESSIBILITY_STRATEGIES,
    "open chromatin": ACCESSIBILITY_STRATEGIES,
}


@dataclass
class ProjectScope:
    """Structured project scope parsed from scope.md."""

    name: str = ""
    description: str = ""
    organisms: list[str] = field(default_factory=list)
    assays: list[str] = field(default_factory=list)
    genes: list[str] = field(default_factory=list)
    cell_lines: list[str] = field(default_factory=list)
    tissues: list[str] = field(default_factory=list)
    developmental_stages: list[str] = field(default_factory=list)
    mutations: list[str] = field(default_factory=list)
    diseases: list[str] = field(default_factory=list)
    treatments: list[str] = field(default_factory=list)
    library_strategy_keep: list[str] = field(default_factory=list)
    library_strategy_exclude: list[str] = field(default_factory=list)
    other_strategy_policy: str = DEFAULT_OTHER_STRATEGY_POLICY

    def is_empty(self) -> bool:
        """Return true when no filtering parameters are set."""
        return (
            not any(getattr(self, field_name) for field_name in LIST_FIELDS)
            and self.other_strategy_policy == DEFAULT_OTHER_STRATEGY_POLICY
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return {
            "name": self.name,
            "description": self.description,
            **{field_name: list(getattr(self, field_name)) for field_name in LIST_FIELDS},
            "other_strategy_policy": self.other_strategy_policy,
        }

    def matches_run(self, run: dict[str, Any]) -> bool:
        """Return true if a parsed SRA run reasonably matches this scope."""
        if self.is_empty():
            return True

        if self.organisms and not _matches_any(run.get("organism", ""), self.organisms):
            return False

        if (
            self.assays or self.library_strategy_keep or self.library_strategy_exclude
        ) and not _matches_assay(run, self):
            return False

        attrs = run.get("sample_attributes", {}) or {}
        text = _run_text(run, attrs)
        checks = (
            self.genes,
            self.cell_lines,
            self.tissues,
            self.developmental_stages,
            self.mutations,
            self.diseases,
            self.treatments,
        )
        return all(not values or _text_matches_any(text, values) for values in checks)


def default_scope_markdown(
    name: str,
    description: str = "",
    **values: list[str],
) -> str:
    """Build the default project scope markdown."""
    scope = ProjectScope(name=name, description=description)
    for field_name in LIST_FIELDS:
        setattr(scope, field_name, [v for v in values.get(field_name, []) if v])
    if not scope.library_strategy_keep:
        scope.library_strategy_keep = _strategy_labels_from_assays(scope.assays)
    if not scope.library_strategy_exclude:
        scope.library_strategy_exclude = _default_exclusions_for_keep(
            scope.library_strategy_keep
        )

    sections = [
        f"# Project Scope: {name}",
        "",
        f"description: {description}",
        "",
        "## Parameters",
        "",
    ]
    for field_name in SCOPE_FIELDS:
        sections.append(f"{field_name}:")
        field_values = getattr(scope, field_name)
        if field_values:
            sections.extend(f"- {value}" for value in field_values)
        else:
            sections.append("- ")
        sections.append("")

    sections.extend(
        [
            "## Run Filtering Rules",
            "",
            "library_strategy_keep:",
            *(_list_items(scope.library_strategy_keep)),
            "",
            "library_strategy_exclude:",
            *(_list_items(scope.library_strategy_exclude)),
            "",
            f"other_strategy_policy: {scope.other_strategy_policy}",
            "",
            "## Search Guidelines",
            "",
            "- Use multiple targeted SRA searches rather than one broad query.",
            "- Keep BioProject inventory broad enough for review.",
            "- During run metadata ingest, only runs matching the parameters above are inserted.",
            "- Add notes here for project-specific inclusion and exclusion criteria.",
            "",
        ]
    )
    return "\n".join(sections)


def read_scope(path: Path) -> ProjectScope:
    """Read and parse a project scope markdown file."""
    if not path.exists():
        return ProjectScope()
    return parse_scope_markdown(path.read_text(encoding="utf-8"))


def parse_scope_markdown(text: str) -> ProjectScope:
    """Parse the simple scope.md format generated by default_scope_markdown."""
    scope = ProjectScope()
    current: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("# Project Scope:"):
            scope.name = line.split(":", 1)[1].strip()
            current = None
            continue
        if line.startswith("description:"):
            scope.description = line.split(":", 1)[1].strip()
            current = None
            continue
        if line.startswith("other_strategy_policy:"):
            scope.other_strategy_policy = line.split(":", 1)[1].strip()
            current = None
            continue
        if line.endswith(":") and line[:-1] in LIST_FIELDS:
            current = line[:-1]
            continue
        if current and line.startswith("-"):
            value = line[1:].strip()
            if value:
                getattr(scope, current).append(value)
            continue
        if line and not line.startswith("-"):
            current = None
    return scope


def write_scope_file(path: Path, name: str, description: str = "", **values: list[str]) -> None:
    """Create a scope.md file if one does not already exist."""
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        default_scope_markdown(name=name, description=description, **values),
        encoding="utf-8",
    )


def _matches_assay(run: dict[str, Any], scope: ProjectScope) -> bool:
    strategy = _norm(run.get("library_strategy", ""))
    keep = _normalized_strategy_set(scope.library_strategy_keep)
    if not keep:
        keep = _strategy_norms_from_assays(scope.assays)
    exclude = _normalized_strategy_set(scope.library_strategy_exclude)

    if strategy and strategy in exclude:
        return False
    if keep:
        if strategy in keep:
            return True
        if strategy and strategy != "other":
            return False
        return _matches_other_strategy(run, scope, keep)

    run_text = _run_specific_text(run, run.get("sample_attributes", {}) or {})
    for assay in scope.assays:
        normalized = _norm(assay)
        aliases = ASSAY_ALIASES.get(normalized, {normalized})
        if strategy in aliases:
            return True
        if normalized in {"chromatin accessibility", "open chromatin"} and strategy in ACCESSIBILITY_STRATEGIES:
            return True
        if strategy and strategy != "other":
            continue
        if any(alias and alias in run_text for alias in aliases | {normalized}):
            return True
    return False


def _matches_other_strategy(
    run: dict[str, Any], scope: ProjectScope, keep: set[str]
) -> bool:
    policy = _norm(scope.other_strategy_policy).replace(" ", "_").replace("-", "_")
    if policy == "allow":
        return True
    if policy == "exclude":
        return False

    evidence_terms = set(keep)
    for assay in scope.assays:
        normalized = _norm(assay)
        evidence_terms.add(normalized)
        evidence_terms.update(ASSAY_ALIASES.get(normalized, {normalized}))

    run_text = _run_specific_text(run, run.get("sample_attributes", {}) or {})
    return any(term and term in run_text for term in evidence_terms)


def _strategy_labels_from_assays(assays: list[str]) -> list[str]:
    keep = _strategy_norms_from_assays(assays)
    ordered = [
        label for norm, label in STRATEGY_LABELS.items()
        if norm in keep
    ]
    extra = sorted(keep - set(STRATEGY_LABELS))
    return [*ordered, *extra]


def _strategy_norms_from_assays(assays: list[str]) -> set[str]:
    strategies: set[str] = set()
    for assay in assays:
        normalized = _norm(assay)
        aliases = ASSAY_ALIASES.get(normalized, {normalized})
        for alias in aliases:
            if alias in ACCESSIBILITY_STRATEGIES:
                strategies.add(alias)
    return strategies


def _default_exclusions_for_keep(keep: list[str]) -> list[str]:
    keep_norms = _normalized_strategy_set(keep)
    if keep_norms & ACCESSIBILITY_STRATEGIES:
        return list(ACCESSIBILITY_EXCLUDE_STRATEGIES)
    return []


def _normalized_strategy_set(values: list[str]) -> set[str]:
    return {_norm(value) for value in values if _norm(value)}


def _list_items(values: list[str]) -> list[str]:
    if not values:
        return ["- "]
    return [f"- {value}" for value in values]


def _matches_any(value: str, candidates: list[str]) -> bool:
    normalized_value = _norm(value)
    return any(_match_token(normalized_value, candidate) for candidate in candidates)


def _text_matches_any(text: str, candidates: list[str]) -> bool:
    return any(_match_token(text, candidate) for candidate in candidates)


def _match_token(text: str, token: str) -> bool:
    normalized = _norm(token)
    if not normalized:
        return False
    if normalized.endswith("*"):
        return text.startswith(normalized[:-1])
    return normalized == text or normalized in text


def _run_text(run: dict[str, Any], attrs: dict[str, str]) -> str:
    pieces = [
        str(run.get("library_strategy", "")),
        str(run.get("library_selection", "")),
        str(run.get("experiment_title", "")),
        str(run.get("sample_title", "")),
        str(run.get("library_construction_protocol", "")),
        str(run.get("study_title", "")),
        str(run.get("organism", "")),
    ]
    pieces.extend(str(key) for key in attrs.keys())
    pieces.extend(str(value) for value in attrs.values())
    return _norm(" ".join(pieces))


def _run_specific_text(run: dict[str, Any], attrs: dict[str, str]) -> str:
    pieces = [
        str(run.get("experiment_title", "")),
        str(run.get("sample_title", "")),
        str(run.get("library_construction_protocol", "")),
    ]
    pieces.extend(str(key) for key in attrs.keys())
    pieces.extend(str(value) for value in attrs.values())
    return _norm(" ".join(pieces))


def _norm(value: object) -> str:
    return " ".join(str(value or "").strip().lower().split())
