"""B1 gate: change-table rows must fall within YAML allow_paths (#5589)."""

from __future__ import annotations

import fnmatch

from ghdag.workflow.gates import GATE_REGISTRY, Violation

from issuesmith.config import get_config
from issuesmith.context_hook import parse_issue_metadata
from issuesmith.contract import extract_change_table_rows
from issuesmith.pr_scope import normalize_allow_path
from issuesmith.scope_gate import parse_allow_paths_from_ctx

_MILESTONE_LABEL = "scope:milestone"
_RULE_ID = "change_table_scope.outside_allow_paths"


def _paths_from_metadata(metadata: dict, key: str) -> list[str]:
    raw = metadata.get(key)
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(p) for p in raw if p is not None and str(p).strip()]
    if isinstance(raw, str):
        return parse_allow_paths_from_ctx(raw)
    return []


def _normalized_allow_patterns(metadata: dict, key: str) -> list[str]:
    patterns: list[str] = []
    for raw in _paths_from_metadata(metadata, key):
        try:
            patterns.append(normalize_allow_path(raw))
        except ValueError:
            continue
    return patterns


def _path_matches_allow_list(path: str, patterns: list[str]) -> bool:
    try:
        norm_path = normalize_allow_path(path)
    except ValueError:
        return False
    for pattern in patterns:
        if norm_path == pattern or fnmatch.fnmatch(norm_path, pattern):
            return True
    return False


class ChangeTableScopeRules:
    def check(self, body: str, labels: list[str]) -> list[Violation]:
        if _MILESTONE_LABEL in labels:
            return []
        try:
            metadata = parse_issue_metadata(body)
        except Exception:
            return []

        target_repo = metadata.get("target_repo")
        if not target_repo:
            return []

        rows = extract_change_table_rows(body)
        if not rows:
            return []

        host_repo = get_config().repo
        target_patterns = _normalized_allow_patterns(metadata, "allow_paths")
        host_patterns = _normalized_allow_patterns(metadata, "host_allow_paths")

        violations: list[Violation] = []
        for row_repo, path, _change_type in rows:
            if not path:
                continue
            repo = row_repo.strip()
            if not repo or repo == target_repo:
                if _path_matches_allow_list(path, target_patterns):
                    continue
                violations.append(
                    Violation(
                        rule_id=_RULE_ID,
                        severity="fail",
                        message=(
                            f"change table path {path!r} is not covered by allow_paths"
                        ),
                        location=path,
                        auto_fixable=True,
                        fix_hint=(
                            f"add {path!r} to allow_paths or remove the row from the"
                            " change table"
                        ),
                    )
                )
                continue
            if repo == host_repo:
                if _path_matches_allow_list(path, host_patterns):
                    continue
                violations.append(
                    Violation(
                        rule_id=_RULE_ID,
                        severity="fail",
                        message=(
                            f"change table path {path!r} is not covered by"
                            " host_allow_paths"
                        ),
                        location=path,
                        auto_fixable=True,
                        fix_hint=(
                            f"add {path!r} to host_allow_paths or remove the row from"
                            " the change table"
                        ),
                    )
                )
                continue
            violations.append(
                Violation(
                    rule_id=_RULE_ID,
                    severity="fail",
                    message=(
                        f"change table lists repository {repo!r}; expected"
                        f" {target_repo!r} or host {host_repo!r}"
                    ),
                    location=path,
                    auto_fixable=True,
                    fix_hint=(
                        f"fix the repository cell for {path!r} or adjust target_repo"
                    ),
                )
            )
        return violations


GATE_REGISTRY["change_table_scope"] = ChangeTableScopeRules
