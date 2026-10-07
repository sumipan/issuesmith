"""CP1 allow_paths breadth gate (#3427)."""

from __future__ import annotations

from ghdag.workflow.gates import GATE_REGISTRY, Violation

import issuesmith.config as config_module
from issuesmith.ac_contract import extract_contract_from_body
from issuesmith.config import get_config
from issuesmith.context_hook import parse_issue_metadata
from issuesmith.contract import change_paths_for_repo
from issuesmith.scope_gate import (
    ScopeMeasure,
    evaluate,
    measure_scope,
    override_from_metadata,
    parse_allow_paths_from_ctx,
    resolve_scope_root,
)


def _parse_allow_paths(metadata: dict) -> list[str]:
    raw = metadata.get("allow_paths")
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(p) for p in raw if p is not None and str(p).strip()]
    if isinstance(raw, str):
        return parse_allow_paths_from_ctx(raw)
    return []


def _autofix_candidate(body: str, target_repo: str) -> list[str]:
    """Deterministic narrower allow_paths: union of the change table and AC paths_must_exist.

    Both name concrete files the Issue's own design already commits to
    touching, so narrowing to their union cannot silently drop scope the
    Issue intends to change — it only drops the unrelated width a copied or
    default allow_paths pattern (e.g. ``tests/**``) picked up (#3487).
    """
    candidate: list[str] = list(change_paths_for_repo(body, target_repo or None))
    contract = extract_contract_from_body(body) or {}
    for raw in contract.get("paths_must_exist") or []:
        path = str(raw).strip()
        if path and path not in candidate:
            candidate.append(path)
    return candidate


def _format_autofix_note(
    old: list[str],
    new: list[str],
    before: ScopeMeasure,
    after: ScopeMeasure,
) -> str:
    """Render the Issue comment announcing the narrowed allow_paths (language pack text)."""
    # Resolved through the config module: callers may stub this module's get_config
    # with a scope-only config (tests/test_cp1_scope_autofix.py).
    language = config_module.get_config().language
    none = language.message("scope_breadth.none")
    return language.message(
        "scope_breadth.autofix_note",
        before_files=before.files,
        before_lines=before.lines,
        old=", ".join(f"`{p}`" for p in old) or none,
        new=", ".join(f"`{p}`" for p in new) or none,
        after_files=after.files,
        after_lines=after.lines,
    )


class ScopeBreadthRules:
    def __init__(self) -> None:
        # Populated by check() when it auto-narrowed allow_paths instead of
        # failing (#3487 AC-2). Callers with forge write access (cp1_gate.main)
        # use this to persist the narrowed list and post the note; check()
        # itself never mutates the Issue.
        self.autofix_note: str | None = None
        self.autofix_new_allow_paths: list[str] | None = None

    def check(self, body: str, labels: list[str]) -> list[Violation]:
        self.autofix_note = None
        self.autofix_new_allow_paths = None
        # scope:milestone parents contain the union of all sub-issues; applying
        # per-issue scope limits always fails for them (#4165).
        if "scope:milestone" in labels:
            return []
        try:
            metadata = parse_issue_metadata(body)
        except Exception:
            return []

        allow_paths = _parse_allow_paths(metadata)
        if not allow_paths:
            return []

        cfg = get_config().scope_gate
        override = override_from_metadata(metadata, cfg)
        effective = override if override is not None else cfg
        if not effective.enabled:
            return []

        root = resolve_scope_root(metadata, get_config())
        if root is None:
            target_repo = (metadata.get("target_repo") or "").strip()
            return [
                Violation(
                    rule_id="scope_breadth.root_unavailable",
                    # warn, not fail: a multi-repo milestone parent may name repos with
                    # no local clone; B1 verify only fails on severity == "fail" (#4076).
                    severity="warn",
                    message=(
                        f"allow_paths scope cannot be measured: no clone for {target_repo!r}"
                        f" under {get_config().paths.external_dir}"
                    ),
                    location=None,
                    auto_fixable=False,
                    fix_hint=(
                        "clone the target repo into .claude/external/<repo> "
                        "(same layout P0 uses) and re-run the gate"
                    ),
                )
            ]

        measure = measure_scope(root, allow_paths)
        verdict = evaluate(measure, cfg, override)

        if not verdict.exceeded:
            return []

        target_repo = (metadata.get("target_repo") or "").strip()
        candidate = _autofix_candidate(body, target_repo)
        if candidate:
            candidate_measure = measure_scope(root, candidate)
            candidate_verdict = evaluate(candidate_measure, cfg, override)
            if not candidate_verdict.exceeded:
                self.autofix_note = _format_autofix_note(
                    allow_paths, candidate, measure, candidate_measure
                )
                self.autofix_new_allow_paths = candidate
                return []

        top5 = ", ".join(f"{d}:{n}" for d, n in measure.by_dir.items()) or "(none)"
        message = (
            f"allow_paths scope too large ({verdict.reason}). "
            f"files={measure.files}/{effective.max_files} "
            f"lines={measure.lines}/{effective.max_lines}. "
            f"top dirs: {top5}"
        )
        if candidate:
            candidate_text = ", ".join(f"`{p}`" for p in candidate)
            fix_hint = (
                "narrowing allow_paths to the following fits within the threshold "
                f"(union of the changed-files table and paths_must_exist): {candidate_text}"
            )
        else:
            fix_hint = (
                "Split allow_paths by directory (see top dirs above) "
                "so each sub-issue stays within the scope threshold."
            )
        return [
            Violation(
                rule_id="scope_breadth.too_large",
                severity="fail",
                message=message,
                location=None,
                auto_fixable=bool(candidate),
                fix_hint=fix_hint,
            )
        ]


GATE_REGISTRY["scope_breadth"] = ScopeBreadthRules
