"""Detect allow_paths entries that overlap host forbidden_pr_paths (#5408)."""

from __future__ import annotations

import fnmatch

from ghdag.workflow.gates import GATE_REGISTRY, Violation

from issuesmith.config import get_config
from issuesmith.context_hook import parse_issue_metadata
from issuesmith.pr_scope import _is_fixture_path, _strip_path
from issuesmith.scope_gate import parse_allow_paths_from_ctx


def _paths_from_metadata(metadata: dict, key: str) -> list[str]:
    raw = metadata.get(key)
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(p) for p in raw if p is not None and str(p).strip()]
    if isinstance(raw, str):
        return parse_allow_paths_from_ctx(raw)
    return []


def _patterns_overlap(allow_path: str, forbidden: str) -> bool:
    return fnmatch.fnmatch(allow_path, forbidden) or fnmatch.fnmatch(forbidden, allow_path)


def _allow_path_exempt(normalized: str, forbidden_except: list[str]) -> bool:
    if _is_fixture_path(normalized):
        return True
    return any(fnmatch.fnmatch(normalized, pat) for pat in forbidden_except)


class AllowPathsForbiddenRules:
    def check(self, body: str, labels: list[str]) -> list[Violation]:
        try:
            metadata = parse_issue_metadata(body)
        except Exception:
            return []

        if "scope_gate" in metadata:
            return []

        allow_entries = _paths_from_metadata(metadata, "allow_paths")
        allow_entries.extend(_paths_from_metadata(metadata, "host_allow_paths"))
        if not allow_entries:
            return []

        cfg = get_config()
        forbidden_patterns = list(cfg.forbidden_pr_paths)
        forbidden_except = list(cfg.forbidden_pr_paths_except)

        violations: list[Violation] = []
        for raw in allow_entries:
            normalized = _strip_path(raw)
            if not normalized or _allow_path_exempt(normalized, forbidden_except):
                continue
            matched_forbidden = next(
                (
                    pat
                    for pat in forbidden_patterns
                    if _patterns_overlap(normalized, _strip_path(pat))
                ),
                None,
            )
            if matched_forbidden is None:
                continue
            violations.append(
                Violation(
                    rule_id="allow_paths_forbidden.overlap",
                    severity="fail",
                    message=(
                        f"allow_paths entry {normalized!r} overlaps "
                        f"forbidden_pr_paths {matched_forbidden!r}"
                    ),
                    location=normalized,
                    auto_fixable=False,
                    fix_hint=(
                        f"remove {normalized} from allow_paths, or ask a human to "
                        f"revisit forbidden_pr_paths"
                    ),
                )
            )
        return violations


GATE_REGISTRY["allow_paths_forbidden"] = AllowPathsForbiddenRules
