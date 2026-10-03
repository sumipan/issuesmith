#!/usr/bin/env python3
"""Real-Issue smoke test of the issuesmith pipeline.

Following the same steps as the dispatcher:
  1. Build a context with build_context() from the given real Issue numbers
  2. Render every step of every handler in workflows/issuesmith.yml with that context
  3. Syntax-check shell steps with bash -n
  4. Exit 1 on any KeyError / bash syntax error

A PR that changes `workflows/issuesmith/*.md` or `workflows/issuesmith.yml`
**must run this script and confirm exit 0 before merging**.

Usage:
    python3 scripts/issuesmith-smoke.py 901 903 906
    # Without arguments, open Issues with a reset / ready label are collected automatically
"""
from __future__ import annotations

import re
import string
import subprocess
import sys

import yaml
from ghdag.forge import get_forge

from issuesmith.config import get_config
from issuesmith.context_hook import build_context

_cfg = get_config()
REPO_ROOT = _cfg.root
WORKFLOW_YAML = _cfg.paths.workflow
TEMPLATE_DIR = _cfg.paths.template_dir


def _fetch_issue_body(issue_number: int) -> str:
    return get_forge().issue_get(issue_number, ["body"]).get("body", "")


def _open_issuesmith_issues() -> list[int]:
    """Return up to 5 open Issues labeled issuesmith:* (fallback sample set)."""
    from issuesmith.queue_triage import READY_LABEL
    client = get_forge()
    ns = _cfg.label_namespace
    sample_labels = [f"{ns}:reset"] + [v for v in READY_LABEL.values()]
    seen: set[int] = set()
    numbers: list[int] = []
    for label in sample_labels:
        if len(numbers) >= 5:
            break
        for issue in client.list_issues(label, "open"):
            n = int(issue["number"])
            if n not in seen:
                seen.add(n)
                numbers.append(n)
                if len(numbers) >= 5:
                    break
    return numbers


def _smoke_one_issue(issue_number: int, yml: dict) -> list[str]:
    errors: list[str] = []
    body = _fetch_issue_body(issue_number)

    for handler_name, handler in yml["handlers"].items():
        if not isinstance(handler, dict) or "steps" not in handler:
            continue

        prev_ids: set[str] = set()
        for step in handler["steps"]:
            step_id = step["id"]
            template_name = step["template"]
            depends = step.get("depends", [])

            # Ordering consistency
            for d in depends:
                if d not in prev_ids:
                    errors.append(
                        f"#{issue_number} {handler_name}.{step_id}: depends '{d}'"
                        f" is not an earlier step (the yml steps order is broken)"
                    )
            prev_ids.add(step_id)

            # Build the real context
            ctx = build_context(issue_number, body=body)
            ctx.update({
                "issue_number": str(issue_number),
                "workflow_name": "issuesmith",
                "handler_name": handler_name,
                "ts": "20260516000000",
                "order_uuid": "00000000-0000-0000-0000-000000000000",
                "result_uuid": "00000000-0000-0000-0000-000000000001",
                "pipeline_id": f"issue-{issue_number}-smoke",
                "result_filename": f"20260516000000-{step_id}-result.md",
            })
            for d in depends:
                ctx[f"{d}_result_filename"] = f"20260516000000-{d}-result.md"

            tpath = TEMPLATE_DIR / f"{template_name}.md"
            if not tpath.exists():
                errors.append(f"#{issue_number} {handler_name}.{step_id}: template missing: {tpath}")
                continue

            try:
                rendered = string.Template(tpath.read_text(encoding="utf-8")).substitute(ctx)
            except KeyError as e:
                errors.append(
                    f"#{issue_number} {handler_name}.{step_id} ({template_name}.md):"
                    f" KeyError ${{{e.args[0]}}} — depends={depends}"
                )
                continue

            if step.get("engine") == "shell":
                proc = subprocess.run(
                    ["bash", "-n"], input=rendered, text=True, capture_output=True
                )
                if proc.returncode != 0:
                    errors.append(
                        f"#{issue_number} {handler_name}.{step_id} ({template_name}.md):"
                        f" bash -n failed: {proc.stderr.strip()}"
                    )
                    continue
                tag = "shell"
            else:
                tag = "llm"
            print(f"  OK  #{issue_number} {handler_name}.{step_id:<5} ({tag}, {len(rendered)} bytes)")

    return errors


_RAW_ADD_LABEL_PATTERN = re.compile(
    r"gh\s+issue\s+edit\b.*?--add-label\s+['\"]?issuesmith:"
)

_STATE_MACHINE_CLI = (
    "python -m ghdag.workflow.state_machine transition --workflow workflows/issuesmith.yml"
)

_DEPRECATED_SM_MODULE = "ghdag.workflow." + "label_" + "state_" + "machine"


def _line_uses_deprecated_state_machine_cli(line: str) -> bool:
    return _DEPRECATED_SM_MODULE in line


def _check_no_deprecated_state_machine_references() -> list[str]:
    """Check that no deprecated label transition CLI references remain in workflows/ and scripts/."""
    errors: list[str] = []
    for base in (REPO_ROOT / "workflows", REPO_ROOT / "scripts"):
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.suffix not in {".md", ".py", ".yml", ".yaml"}:
                continue
            for lineno, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            ):
                if _line_uses_deprecated_state_machine_cli(line):
                    rel = path.relative_to(REPO_ROOT)
                    errors.append(f"{rel}:{lineno}: deprecated label transition CLI reference: {line.strip()}")
    return errors


def _check_issuesmith_yml_state_machine(workflow_name: str = "issuesmith") -> list[str]:
    """Verify that the workflow YAML has required state machine declarations."""
    from issuesmith.queue_triage import DONE_LABEL, RUNNING_LABEL
    errors: list[str] = []
    yml = yaml.safe_load(WORKFLOW_YAML.read_text(encoding="utf-8"))
    ns = yml.get("label_namespace") or ""
    if ns != workflow_name:
        errors.append(f"{workflow_name}.yml: label_namespace: {workflow_name} is not defined")
    reset_lbl = f"{ns}:reset"
    if yml.get("reset_label") != reset_lbl:
        errors.append(f'{workflow_name}.yml: reset_label: "{reset_lbl}" is not defined')
    transitions = yml.get("transitions") or {}
    required_edges: list[tuple[str, str]] = []
    for pname, running in RUNNING_LABEL.items():
        done = DONE_LABEL.get(pname, "")
        if done:
            required_edges.append((running, done))
    for src, dst in required_edges:
        if dst not in (transitions.get(src) or []):
            errors.append(f"{workflow_name}.yml: transitions lacks {src} → {dst}")
    return errors


def _check_templates_use_state_machine_cli() -> list[str]:
    """Check that label transition CLIs in templates use the state_machine + --workflow form."""
    errors: list[str] = []
    transition_re = re.compile(
        r"python\s+-m\s+ghdag\.workflow\.state_machine\s+transition"
    )
    for tpath in sorted(TEMPLATE_DIR.glob("*.md")):
        for lineno, line in enumerate(tpath.read_text(encoding="utf-8").splitlines(), 1):
            if not transition_re.search(line):
                continue
            if "state_machine transition --workflow workflows/issuesmith.yml" not in line:
                errors.append(
                    f"{tpath.name}:{lineno}: state_machine CLI is not in --workflow form:"
                    f" {line.strip()}"
                )
    return errors


def _check_raw_add_label_in_templates() -> list[str]:
    """Check that no raw `gh issue edit --add-label issuesmith:*` remains in templates.

    Cleanup with only --remove-label is allowed; only lines with --add-label are errors.
    """
    errors: list[str] = []
    for tpath in sorted(TEMPLATE_DIR.glob("*.md")):
        for lineno, line in enumerate(tpath.read_text(encoding="utf-8").splitlines(), 1):
            if _RAW_ADD_LABEL_PATTERN.search(line):
                errors.append(
                    f"{tpath.name}:{lineno}: raw `gh issue edit --add-label issuesmith:*` found."
                    f" Replace it with `{_STATE_MACHINE_CLI}`."
                    f" Line: {line.strip()}"
                )
    return errors


def main(argv: list[str]) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="issuesmith pipeline smoke test")
    parser.add_argument("issues", nargs="*", type=int)
    parser.add_argument("--workflow", default="issuesmith", help="workflow name")
    args = parser.parse_args(argv)
    workflow_name = args.workflow
    if args.issues:
        issues = args.issues
    else:
        issues = _open_issuesmith_issues()
        if not issues:
            ns = _cfg.label_namespace
            print(
                f"ERROR: no Issues to check. Pass Issue numbers as arguments or prepare"
                f" open Issues with a {ns}:reset / ready label",
                file=sys.stderr,
            )
            return 2

    all_errors: list[str] = []

    print("\n=== Template static check: deprecated label transition CLI references ===")
    lsm_errors = _check_no_deprecated_state_machine_references()
    if lsm_errors:
        all_errors.extend(lsm_errors)
        for e in lsm_errors:
            print(f"  FAIL: {e}")
    else:
        print("  OK  no deprecated label transition CLI references in workflows/ scripts/")

    print(f"\n=== {workflow_name}.yml state machine declarations ===")
    yml_sm_errors = _check_issuesmith_yml_state_machine(workflow_name)
    if yml_sm_errors:
        all_errors.extend(yml_sm_errors)
        for e in yml_sm_errors:
            print(f"  FAIL: {e}")
    else:
        print("  OK  label_namespace / reset_label / transitions")

    print("\n=== Template static check: state_machine CLI form ===")
    cli_errors = _check_templates_use_state_machine_cli()
    if cli_errors:
        all_errors.extend(cli_errors)
        for e in cli_errors:
            print(f"  FAIL: {e}")
    else:
        print("  OK  every template uses the state_machine --workflow form")

    print("\n=== Template static check: raw add-label detection ===")
    static_errors = _check_raw_add_label_in_templates()
    if static_errors:
        all_errors.extend(static_errors)
        for e in static_errors:
            print(f"  WARN: {e}")
    else:
        print("  OK  no raw `gh issue edit --add-label issuesmith:*`")

    yml = yaml.safe_load(WORKFLOW_YAML.read_text(encoding="utf-8"))
    for n in issues:
        print(f"\n=== Issue #{n} ===")
        all_errors.extend(_smoke_one_issue(n, yml))

    print()
    if all_errors:
        print("=== E2E SMOKE FAILED ===")
        for e in all_errors:
            print(f"  FAIL: {e}")
        print(f"\n{len(all_errors)} problem(s) found in total. Fix them and re-run.")
        return 1

    print(f"=== E2E SMOKE PASSED ({len(issues)} issues x all steps) ===")
    print("Every dispatcher step renders with real Issue bodies without KeyError / bash syntax errors.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
