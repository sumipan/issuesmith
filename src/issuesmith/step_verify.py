"""Declarative step verify: design_doc checks via shared B1 gate bundle."""

from __future__ import annotations

import argparse
import sys

from issuesmith.b1_verify import (
    apply_deterministic_recovery,
    collect_violations,
    detect_oscillation,
    format_report,
)
from issuesmith.config import get_config


def main(step_id: str, argv: list[str] | None = None) -> int:
    cfg = get_config()
    step = cfg.steps.get(step_id)
    if step is None or step.verify != "design_doc":
        print(f"verify: steps.{step_id}.verify is not declared", file=sys.stderr)
        return 2

    from ghdag.forge import get_forge

    parser = argparse.ArgumentParser(prog=f"issuesmith verify {step_id}")
    parser.add_argument("issue_number", type=int)
    parser.add_argument("--prev-report", default=None, metavar="FILE")
    parser.add_argument(
        "--apply-deterministic",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not persist deterministic body/label/milestone changes",
    )
    args = parser.parse_args(list(argv or []))

    forge = get_forge()
    data = forge.issue_get(args.issue_number, fields=["body", "labels", "milestone"])
    body = data["body"] or ""
    labels = [label["name"] for label in data.get("labels", [])]
    if args.apply_deterministic:
        result = apply_deterministic_recovery(
            forge,
            args.issue_number,
            body,
            labels,
            dry_run=args.dry_run,
            persist=not args.dry_run,
        )
        body = result.body
        labels = result.labels
        if result.applied:
            sys.stdout.write(
                "DETERMINISTIC_APPLIED: " + " ".join(result.applied) + "\n"
            )
        if result.can_done:
            sys.stdout.write("VERIFY_RECOVERY: DONE\n")
        elif result.unresolved_reason:
            sys.stdout.write("VERIFY_RECOVERY: UNRESOLVED\n")
            sys.stdout.write(f"REASON: {result.unresolved_reason}\n")
        violations = list(result.remaining)
    else:
        violations = collect_violations(
            body,
            labels,
            issue_number=args.issue_number,
        )

    if args.prev_report is not None:
        if args.prev_report == "-":
            prev_report = sys.stdin.read()
        else:
            with open(args.prev_report, encoding="utf-8") as fh:
                prev_report = fh.read()
        oscillation = detect_oscillation(violations, prev_report)
        if oscillation is not None:
            violations.append(oscillation)
    sys.stdout.write(format_report(violations))
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1], sys.argv[2:]))
