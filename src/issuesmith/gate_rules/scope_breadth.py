"""CP1 allow_paths breadth gate (#3427)."""

from __future__ import annotations

from ghdag.workflow.gates import GATE_REGISTRY, Violation

from issuesmith.ac_contract import extract_contract_from_body
from issuesmith.config import get_config
from issuesmith.context_hook import parse_issue_metadata
from issuesmith.contract import change_paths_for_repo
from issuesmith.steps.scope_gate import (
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
    old_text = ", ".join(f"`{p}`" for p in old) or "(none)"
    new_text = ", ".join(f"`{p}`" for p in new) or "(none)"
    return (
        "## CP1: allow_paths を自動で絞り込みました\n"
        "\n"
        f"**理由**: allow_paths が幅ゲートを超過（files={before.files}, lines={before.lines}）。"
        "変更対象ファイル表と受け入れ条件の `paths_must_exist` から決定論で絞り込み、"
        "続行しました。\n"
        "\n"
        f"- 変更前: {old_text}\n"
        f"- 変更後: {new_text}（files={after.files}, lines={after.lines}）\n"
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
                    severity="fail",
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
                "allow_paths を次に絞り込めば閾値内に収まります "
                f"(変更対象ファイル表 + paths_must_exist の合集合): {candidate_text}"
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
