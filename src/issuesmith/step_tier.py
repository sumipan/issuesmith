"""Declarative step tier: design_doc (issue body) or pr_diff (PR metrics)."""

from __future__ import annotations

import argparse
import sys

from issuesmith.config import ConfigError, get_config


def design_doc_tier(body: str) -> str:
    """Return heavy when the body looks like a design doc, else light."""
    sections = get_config().sections
    background = f"## {sections['background']}"
    design = f"## {sections['design']}"
    if background in body or design in body:
        return "heavy"
    return "light"


def pr_diff_tier(
    pr_diff_lines: int,
    unchecked_ac_count: int,
    gates_pass: bool,
    *,
    light_max_diff_lines: int = 200,
) -> str:
    """Return light when diff and AC gates are within configured thresholds."""
    if (
        pr_diff_lines < light_max_diff_lines
        and unchecked_ac_count == 0
        and gates_pass
    ):
        return "light"
    return "heavy"


def determine_tier(
    step_id: str,
    *,
    body: str | None = None,
    pr_diff_lines: int | None = None,
    unchecked_ac_count: int | None = None,
    gates_pass: bool | None = None,
) -> str:
    """Resolve tier for ``step_id`` from config declaration and inputs."""
    cfg = get_config()
    step = cfg.steps.get(step_id)
    if step is None or step.tier is None:
        raise ConfigError(f"steps.{step_id}.tier is not declared")
    tier_cfg = step.tier
    if tier_cfg.kind == "design_doc":
        if body is None:
            raise ValueError("body is required for design_doc tier")
        return design_doc_tier(body)
    if pr_diff_lines is None or unchecked_ac_count is None or gates_pass is None:
        raise ValueError("pr_diff metrics are required for pr_diff tier")
    return pr_diff_tier(
        pr_diff_lines,
        unchecked_ac_count,
        gates_pass,
        light_max_diff_lines=tier_cfg.light_max_diff_lines,
    )


def _parse_gates_pass(raw: str) -> bool:
    return raw.lower() in ("true", "1", "yes")


def main(step_id: str, argv: list[str] | None = None) -> int:
    cfg = get_config()
    step = cfg.steps.get(step_id)
    if step is None or step.tier is None:
        print(f"tier: steps.{step_id}.tier is not declared", file=sys.stderr)
        return 2
    tier_cfg = step.tier
    args_list = list(argv or [])

    if tier_cfg.kind == "design_doc":
        parser = argparse.ArgumentParser(prog=f"issuesmith tier {step_id}")
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--issue", type=int, help="GitHub issue number")
        group.add_argument("--body-file", help="Path to issue body text file")
        args = parser.parse_args(args_list)
        if args.issue is not None:
            from ghdag.forge import get_forge

            data = get_forge().issue_get(args.issue, fields=["body"])
            body = data["body"]
        else:
            body = open(args.body_file, encoding="utf-8").read()
        print(design_doc_tier(body))
        return 0

    parser = argparse.ArgumentParser(prog=f"issuesmith tier {step_id}")
    parser.add_argument("--diff-lines", type=int, required=True)
    parser.add_argument("--unchecked-ac", type=int, required=True)
    parser.add_argument("--gates-pass", default=None)
    parser.add_argument("--p2-all-pass", default=None)
    args = parser.parse_args(args_list)
    pass_raw = args.gates_pass if args.gates_pass is not None else args.p2_all_pass
    if pass_raw is None:
        parser.error("--gates-pass or --p2-all-pass is required")
    gates_pass = _parse_gates_pass(str(pass_raw))
    print(
        pr_diff_tier(
            args.diff_lines,
            args.unchecked_ac,
            gates_pass,
            light_max_diff_lines=tier_cfg.light_max_diff_lines,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1], sys.argv[2:]))
