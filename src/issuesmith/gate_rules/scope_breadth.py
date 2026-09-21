"""CP1 allow_paths breadth gate (#3427)."""

from __future__ import annotations

from pathlib import Path

from ghdag.workflow.gates import GATE_REGISTRY, Violation

from issuesmith.config import get_config
from issuesmith.context_hook import parse_issue_metadata
from issuesmith.steps.scope_gate import (
    evaluate,
    measure_scope,
    override_from_metadata,
    parse_allow_paths_from_ctx,
)


def _resolve_root(metadata: dict) -> Path | None:
    """Measurement root: nexus root, or ``paths.external_dir/<repo>`` for cross-repo.

    Same layout as context_hook.target_clone_path (``.claude/external/<repo>``),
    so CP1 measures the tree P0 will measure (#3487: the old
    ``external/<owner>/<repo>`` guess never existed and the gate passed silently).
    """
    target_repo = (metadata.get("target_repo") or "").strip()
    cfg = get_config()
    if not target_repo or target_repo == "sumipan/nexus":
        return cfg.root
    parts = target_repo.split("/", 1)
    if len(parts) != 2:
        return None
    external = cfg.paths.external_dir / parts[1]
    if not (external / ".git").exists():
        return None
    return external


def _parse_allow_paths(metadata: dict) -> list[str]:
    raw = metadata.get("allow_paths")
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(p) for p in raw if p is not None and str(p).strip()]
    if isinstance(raw, str):
        return parse_allow_paths_from_ctx(raw)
    return []


class ScopeBreadthRules:
    def check(self, body: str, labels: list[str]) -> list[Violation]:
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

        root = _resolve_root(metadata)
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

        top5 = ", ".join(f"{d}:{n}" for d, n in measure.by_dir.items()) or "(none)"
        message = (
            f"allow_paths scope too large ({verdict.reason}). "
            f"files={measure.files}/{effective.max_files} "
            f"lines={measure.lines}/{effective.max_lines}. "
            f"top dirs: {top5}"
        )
        return [
            Violation(
                rule_id="scope_breadth.too_large",
                severity="fail",
                message=message,
                location=None,
                auto_fixable=False,
                fix_hint=(
                    "Split allow_paths by directory (see top dirs above) "
                    "so each sub-issue stays within the scope threshold."
                ),
            )
        ]


GATE_REGISTRY["scope_breadth"] = ScopeBreadthRules
