"""Unified issuesmith CLI (``python3 -m issuesmith <subcommand>``)."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Sequence

from issuesmith.cli_errors import ContractParser, fail

_ANDON_USAGE = """\
usage: issuesmith andon <subcommand> ...

subcommands:
  list [--all] [--json]    list open (unanswered) andons (--json: records with raised_at / notes)
  show <andon-id>          show a specific andon by id
  answer <andon-id> <action> [--by human|agent]  post answer, remove label, call resume hook
  note <andon-id> --key <key> --value <value>  record a note on an open andon (no label change)
  auto-answer [--apply] [--json]  plan (or apply) configured auto-answer rules
  diagnose [--apply] [--json]     plan (or apply) deterministic diagnosis bundling / triggers
"""

_USAGE = """\
usage: issuesmith <command> ...

commands:
  context
  gate|gate-preflight
  verify
  queue  deps  tier  comments  gh
  engine  dispatch  publish  labels  doctor  smoke  gen-live  version-bump
  resume --from <step> | --phase <phase>
  convert-to-milestone
  milestone  status / resume / prune / consolidate <child> --into <sibling>
  config show
  observe [--apply] [--json]
  lanes check [--apply] [--json]  report [--json]
  metrics rework [--since YYYY-MM-DD] [--issue N] [--audit PATH] [--json]
  main-health  (run observe.main_health.command on the base branch, write state)
  andon list | show | answer | note | auto-answer
"""

def _parse_args(parser: ContractParser, argv: list[str]) -> argparse.Namespace | int:
    """Parse argv; on argparse failure return an exit code instead of raising."""
    try:
        return parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code
        return 0 if code is None else int(code)


def _run_context(argv: list[str]) -> int:
    sys.argv = ["issuesmith.context_hook", *argv]
    from issuesmith.context_hook import main as hook_main

    hook_main()
    return 0


def _run_gate_preflight(argv: list[str]) -> int:
    from pathlib import Path

    import issuesmith.gate_rules  # noqa: F401 — populates GATE_REGISTRY
    from issuesmith.gate_registry import GATE_REGISTRY

    parser = ContractParser(command="gate-preflight")
    parser.add_argument("--gate", required=True, help="Gate name (rule-class GATE_REGISTRY)")
    parser.add_argument("--body-file", required=True, help="Path to issue body text file")
    parser.add_argument("--labels-file", default=None, help="Path to newline-separated labels file")
    parsed = _parse_args(parser, argv)
    if isinstance(parsed, int):
        return parsed
    args = parsed

    if args.gate not in GATE_REGISTRY:
        registered = ", ".join(sorted(GATE_REGISTRY))
        return fail(
            "INVALID_VALUE",
            f"gate {args.gate!r} is not registered",
            f"registered gates: {registered}",
        )

    body_path = Path(args.body_file)
    if not body_path.exists():
        return fail("NOT_FOUND", f"body file {args.body_file!r} does not exist")

    body = body_path.read_text(encoding="utf-8")

    labels: list[str] = []
    if args.labels_file is not None:
        labels_path = Path(args.labels_file)
        if labels_path.exists():
            labels = [
                line.strip()
                for line in labels_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

    rule_cls = GATE_REGISTRY[args.gate]
    violations = rule_cls().check(body, labels)
    print(json.dumps([asdict(v) for v in violations]))
    return 0


_ISSUE_GATE_ENTRIES = {
    "design_lint": "issuesmith.design_lint_entry",
    "ac_complete": "issuesmith.ac_complete_entry",
}


def _cmd_gate(argv: list[str]) -> int:
    if not argv:
        print("gate: gate name required", file=sys.stderr)
        return 2
    gate_name, *rest = argv

    # New form: gate <name> --body-file ...  (== gate-preflight --gate <name> ...)
    if "--body-file" in rest or (rest and rest[0].startswith("-") and "--gate" not in rest):
        return _run_gate_preflight(["--gate", gate_name, *rest])

    if rest and not rest[0].startswith("-"):
        mod_name = _ISSUE_GATE_ENTRIES.get(gate_name)
        if mod_name is None:
            print(
                f"gate {gate_name}: issue-number form supports: "
                f"{', '.join(sorted(_ISSUE_GATE_ENTRIES))}",
                file=sys.stderr,
            )
            return 2
        gate_mod = importlib.import_module(mod_name)
        sys.argv = [mod_name, *rest]
        gate_mod.main()
        return 0

    # Explicit preflight-style flags after gate name
    if rest:
        return _run_gate_preflight(["--gate", gate_name, *rest])

    print(f"gate {gate_name}: --body-file or an issue number is required", file=sys.stderr)
    return 2


def _cmd_verify(argv: list[str]) -> int:
    if not argv:
        print("verify: step id required", file=sys.stderr)
        return 2
    step_id, *rest = argv
    from issuesmith.config import get_config

    step = get_config().steps.get(step_id)
    if step is not None and step.verify == "design_doc":
        from issuesmith.step_verify import main as step_verify_main

        return int(step_verify_main(step_id, rest))

    print(f"verify: steps.{step_id}.verify is not declared", file=sys.stderr)
    return 2


def _cmd_queue(argv: list[str]) -> int:
    from issuesmith.queue import main as queue_main

    return int(queue_main(argv))


def _deps_parser() -> ContractParser:
    parser = ContractParser(command="deps")
    sub = parser.add_subparsers(dest="cmd", required=True)
    check = sub.add_parser("check")
    check.add_argument("issue", type=int)
    return parser


def _cmd_deps(argv: list[str]) -> int:
    parser = _deps_parser()
    if not argv or argv[0] in {"-h", "--help"}:
        parser.print_help()
        return 0
    parsed = _parse_args(parser, argv)
    if isinstance(parsed, int):
        return parsed
    args = parsed
    from issuesmith.dep_extractor import check_issue, check_result_to_dict

    if args.cmd == "check":
        print(json.dumps(check_result_to_dict(check_issue(args.issue))))
        return 0
    return fail("UNKNOWN_COMMAND", f"unknown deps subcommand: {args.cmd}")


def _cmd_tier(argv: list[str]) -> int:
    if not argv:
        print("tier: step id required", file=sys.stderr)
        return 2
    step_id, *rest = argv
    from issuesmith.config import get_config

    step = get_config().steps.get(step_id)
    if step is not None and step.tier is not None:
        from issuesmith.step_tier import main as step_tier_main

        return int(step_tier_main(step_id, rest))

    print(f"tier: steps.{step_id}.tier is not declared", file=sys.stderr)
    return 2


def _cmd_comments(argv: list[str]) -> int:
    sys.argv = ["issuesmith.pipeline_comments", *argv]
    from issuesmith.pipeline_comments import main as comments_main

    comments_main()
    return 0


def _cmd_gh(argv: list[str]) -> int:
    sys.argv = ["issuesmith.github_api", *argv]
    from issuesmith.github_api import main as gh_main

    gh_main()
    return 0


def _cmd_engine(argv: list[str]) -> int:
    from issuesmith.engine import main as engine_main

    return int(engine_main(argv))


def _cmd_dispatch(argv: list[str]) -> int:
    from issuesmith.ops.dispatch import main as dispatch_main
    from issuesmith.worktree import CALLER_STEP_ENV_VAR

    prior = os.environ.get(CALLER_STEP_ENV_VAR)
    if argv:
        os.environ[CALLER_STEP_ENV_VAR] = argv[0]
    try:
        return int(dispatch_main(argv))
    finally:
        if prior is None:
            os.environ.pop(CALLER_STEP_ENV_VAR, None)
        else:
            os.environ[CALLER_STEP_ENV_VAR] = prior


def _cmd_publish(argv: list[str]) -> int:
    from issuesmith.ops.publish import main as publish_main

    return int(publish_main(argv))


_LABELS_USAGE = (
    "usage: issuesmith labels <subcommand> ...\n\n"
    "subcommands:\n"
    "  reconcile [--fix] [--json]   report (or fix) managed-label divergences\n"
)


def _andon_parser() -> ContractParser:
    parser = ContractParser(command="andon")
    sub = parser.add_subparsers(dest="subcmd", required=True)
    p_list = sub.add_parser("list")
    p_list.add_argument("--all", action="store_true")
    p_list.add_argument("--json", action="store_true")
    p_show = sub.add_parser("show")
    p_show.add_argument("andon_id")
    p_show.add_argument("--json", action="store_true")
    p_answer = sub.add_parser("answer")
    p_answer.add_argument("andon_id")
    p_answer.add_argument("action")
    p_answer.add_argument("--by", default="human", choices=["human", "agent"])
    p_note = sub.add_parser("note")
    p_note.add_argument("andon_id")
    p_note.add_argument("--key", required=True)
    p_note.add_argument("--value", required=True)
    p_auto = sub.add_parser("auto-answer")
    p_auto.add_argument("--apply", action="store_true")
    p_auto.add_argument("--json", action="store_true")
    p_diag = sub.add_parser("diagnose")
    p_diag.add_argument("--apply", action="store_true")
    p_diag.add_argument("--json", action="store_true")
    return parser


def _andon_sort_key(kind: str, step: str) -> int:
    if kind == "decision":
        return 0
    if kind == "broken":
        return 1
    if kind == "blocked" and step != "observe":
        return 2
    return 3


def _active_issue_numbers(cfg: Any) -> set[int]:
    """Issue numbers that must not receive andon auto-answer (queue + in-flight + live DAG)."""
    from issuesmith.observe.dag_state import load_dag_states
    from issuesmith.queue_store import QueueStore

    active: set[int] = set()
    try:
        snap = QueueStore().snapshot()
        for req in snap.active_requests:
            active.add(req.issue)
        for entry in snap.in_flight:
            raw_issue = entry.get("issue")
            if isinstance(raw_issue, int) and not isinstance(raw_issue, bool) and raw_issue > 0:
                active.add(raw_issue)
    except OSError:
        pass

    try:
        dag_states = load_dag_states(
            cfg.paths.exec_jsonl,
            cfg.paths.done_dir,
            cfg.paths.done_dir.parent / "running",
        )
    except OSError:
        dag_states = {}
    for issue, dag in dag_states.items():
        if dag.status in ("running", "pending"):
            active.add(issue)
    return active


def _cmd_andon(argv: list[str]) -> int:
    from issuesmith.andon import (
        andon_record,
        answer,
        apply_auto_answers,
        list_open,
        list_open_records,
        note,
        plan_auto_answers,
        to_comment,
    )

    if not argv:
        return fail(
            "MISSING_ARGUMENT",
            "subcommand required",
            "subcommands: list, show, answer, note, auto-answer, diagnose",
        )
    parser = _andon_parser()
    if argv[0] in {"-h", "--help"}:
        parser.print_help()
        return 0
    parsed = _parse_args(parser, argv)
    if isinstance(parsed, int):
        return parsed
    args = parsed
    from ghdag.forge import get_forge

    client = get_forge()

    if args.subcmd == "list":
        if args.json:
            records = list_open_records(client)
            if not args.all:
                records = sorted(
                    records, key=lambda r: _andon_sort_key(r["kind"], r["step"])
                )
            print(json.dumps(records, ensure_ascii=False))
            return 0
        andons = list_open(client)
        if not args.all:
            andons = sorted(andons, key=lambda a: _andon_sort_key(a.kind, a.step))
        if not andons:
            print("no open andons")
        for a in andons:
            print(f"{a.id}\t{a.kind}\tissue #{a.issue}\t{a.summary}")
        return 0

    if args.subcmd == "show":
        if args.json:
            record = andon_record(client, args.andon_id)
            if record is None:
                return fail("NOT_FOUND", f"andon not found: {args.andon_id}")
            print(json.dumps(record, ensure_ascii=False))
            return 0
        andons = list_open(client)
        matched = [a for a in andons if a.id == args.andon_id]
        if not matched:
            return fail("NOT_FOUND", f"andon not found: {args.andon_id}")
        print(to_comment(matched[0]))
        return 0

    if args.subcmd == "diagnose":
        from datetime import datetime, timezone

        from issuesmith.config import get_config
        from issuesmith.diagnose import DiagnosePlan, apply_diagnose_plan, plan_diagnoses

        cfg = get_config()
        settings = cfg.andon.diagnose
        if not settings.enabled:
            answer_plan = DiagnosePlan()
        else:
            records = list_open_records(client)
            active_issues = _active_issue_numbers(cfg)
            answer_plan = plan_diagnoses(
                client,
                records,
                now=datetime.now(timezone.utc),
                active_issues=active_issues,
                settings=settings,
                ledger_path=cfg.paths.diagnoses,
            )
            if args.apply:
                answer_plan = apply_diagnose_plan(
                    client,
                    answer_plan,
                    exec_jsonl=cfg.paths.exec_jsonl,
                )
        if args.json:
            payload = {
                "bundled": answer_plan.bundled,
                "requested": answer_plan.requested,
                "failures": answer_plan.failures,
            }
            print(json.dumps(payload, ensure_ascii=False))
            return 0
        for entry in answer_plan.bundled:
            print(
                f"bundle {entry.get('andon_id')} issue #{entry.get('issue')} "
                f"with {entry.get('bundled_with')}"
            )
        for entry in answer_plan.requested:
            print(
                f"request {entry.get('andon_id')} issue #{entry.get('issue')} "
                f"handler={entry.get('handler')}"
            )
        for entry in answer_plan.failures:
            print(
                f"failure {entry.get('andon_id')} {entry.get('operation')}: "
                f"{entry.get('error')}"
            )
        return 0

    if args.subcmd == "auto-answer":
        from issuesmith.config import get_config

        cfg = get_config()
        rules = cfg.andon.auto_answer
        answer_plan = plan_auto_answers(
            client,
            rules,
            active_issues=_active_issue_numbers(cfg),
        )
        if args.apply:
            apply_auto_answers(client, answer_plan)
        if args.json:
            print(json.dumps(answer_plan.__dict__, ensure_ascii=False))
            return 0
        for entry in answer_plan.answers:
            print(
                f"answer {entry['andon_id']} issue #{entry['issue']} "
                f"rule={entry['rule']} action={entry['action']}"
            )
        for entry in answer_plan.escalations:
            print(f"escalation {entry.get('kind')} issue #{entry.get('issue', '-')}")
        return 0

    if args.subcmd == "answer":
        try:
            answer(client, args.andon_id, args.action, answered_by=args.by)
        except ValueError as exc:
            return fail("INVALID_VALUE", str(exc))
        print(f"answered {args.andon_id} with: {args.action}")
        return 0

    if args.subcmd == "note":
        try:
            note(client, args.andon_id, args.key, args.value)
        except KeyError:
            return fail("NOT_FOUND", f"andon not found: {args.andon_id}")
        except ValueError as exc:
            return fail("INVALID_VALUE", str(exc))
        print(f"noted {args.andon_id}: {args.key}={args.value}")
        return 0

    return fail("UNKNOWN_COMMAND", f"unknown andon subcommand: {args.subcmd}")


def _labels_parser() -> ContractParser:
    parser = ContractParser(command="labels")
    sub = parser.add_subparsers(dest="subcmd", required=True)
    p = sub.add_parser("reconcile")
    p.add_argument("--fix", action="store_true")
    p.add_argument("--json", action="store_true")
    return parser


def _cmd_labels(argv: list[str]) -> int:
    if not argv:
        return fail("MISSING_ARGUMENT", "subcommand required", "subcommands: reconcile")
    parser = _labels_parser()
    if argv[0] in {"-h", "--help"}:
        parser.print_help()
        return 0
    parsed = _parse_args(parser, argv)
    if isinstance(parsed, int):
        return parsed
    args = parsed
    if args.subcmd == "reconcile":
        from ghdag.forge import get_forge

        from issuesmith.ops.labels import reconcile

        reconcile(get_forge(), fix=args.fix, as_json=args.json)
        return 0
    return fail("UNKNOWN_COMMAND", f"unknown labels subcommand: {args.subcmd}")


def _doctor_parser() -> ContractParser:
    parser = ContractParser(command="doctor")
    parser.add_argument("--json", action="store_true")
    return parser


def _cmd_doctor(argv: list[str]) -> int:
    from issuesmith.ops.preflight import main as preflight_main

    parser = _doctor_parser()
    if argv and argv[0] in {"-h", "--help"}:
        parser.print_help()
        return 0
    parsed = _parse_args(parser, argv)
    if isinstance(parsed, int):
        return parsed
    args = parsed
    return int(preflight_main(as_json=args.json))


def _cmd_smoke(argv: list[str]) -> int:
    from issuesmith.ops.smoke import main as smoke_main

    return int(smoke_main(argv))


def _cmd_gen_live(argv: list[str]) -> int:
    from issuesmith.ops.gen_live_dispatch import main as gen_main

    return int(gen_main(argv))


def _cmd_version_bump(argv: list[str]) -> int:
    from issuesmith.ops.version_bump import main as bump_main

    return int(bump_main(argv))


def _cmd_resume(argv: list[str]) -> int:
    parser = ContractParser(command="resume")
    parser.add_argument("issue", type=int)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--from", dest="from_step")
    group.add_argument("--phase")
    parser.add_argument(
        "--workflow",
        default=None,
        help="ghdag workflow name for --from (default: stem of paths.workflow, e.g. 'issuesmith')",
    )
    parser.add_argument(
        "--handler",
        default=None,
        help="ghdag handler name for --from (default: derived from the step)",
    )
    parser.add_argument(
        "--mark-done",
        dest="mark_done",
        action="append",
        default=None,
        help="mark a failed/cancelled/skipped ancestor step as succeeded before recovering"
        " (requires --from; may be repeated)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="with --from: re-run the step and everything downstream even if they already succeeded",
    )
    args = parser.parse_args(argv)
    if args.mark_done and args.from_step is None:
        parser.error("--mark-done requires --from")
    from issuesmith.resume import resume as _cmd_resume_fn
    kwargs: dict = dict(
        from_step=args.from_step,
        phase=args.phase,
        workflow=args.workflow,
        handler=args.handler,
    )
    if args.mark_done is not None:
        kwargs["mark_done"] = args.mark_done
    if args.force:
        if args.from_step is None:
            print("resume: --force requires --from", file=sys.stderr)
            return 2
        kwargs["force"] = True
    return _cmd_resume_fn(args.issue, **kwargs)


def _cmd_convert_to_milestone(argv: list[str]) -> int:
    from issuesmith.convert_to_milestone import main as convert_main

    return int(convert_main(argv))


def _milestone_parser() -> ContractParser:
    parser = ContractParser(command="milestone")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_status = sub.add_parser("status")
    p_status.add_argument("parent", type=int)
    p_status.add_argument("--json", action="store_true")
    p_resume = sub.add_parser("resume")
    p_resume.add_argument("parent", type=int)
    p_prune = sub.add_parser("prune")
    p_prune.add_argument("--dry-run", action="store_true")
    p_cons = sub.add_parser("consolidate")
    p_cons.add_argument("child", type=int)
    p_cons.add_argument("--into", type=int, required=True, dest="into_sibling")
    return parser


def _cmd_milestone(argv: list[str]) -> int:
    from issuesmith.milestone import (
        milestone_consolidate,
        milestone_prune,
        milestone_resume,
        milestone_status,
    )

    if not argv:
        return fail(
            "MISSING_ARGUMENT",
            "subcommand required",
            "subcommands: status, resume, prune, consolidate",
        )
    parser = _milestone_parser()
    if argv[0] in {"-h", "--help"}:
        parser.print_help()
        return 0
    parsed = _parse_args(parser, argv)
    if isinstance(parsed, int):
        return parsed
    args = parsed
    if args.cmd == "status":
        return milestone_status(args.parent, as_json=args.json)
    if args.cmd == "resume":
        return milestone_resume(args.parent)
    if args.cmd == "prune":
        return milestone_prune(dry_run=args.dry_run)
    if args.cmd == "consolidate":
        return milestone_consolidate(args.child, args.into_sibling)
    return fail("UNKNOWN_COMMAND", f"unknown milestone subcommand: {args.cmd}")


def _json_default(o: object) -> object:
    if isinstance(o, (set, frozenset)):
        return sorted(o)
    return str(o)


def _config_subcommand_hint() -> str:
    return "subcommands: show, extract-line"


def _config_parser() -> ContractParser:
    parser = ContractParser(command="config")
    sub = parser.add_subparsers(dest="subcmd", required=True)
    sub.add_parser("show")
    extract = sub.add_parser("extract-line")
    extract.add_argument("line_name")
    extract.add_argument("dest_dir")
    return parser


def _cmd_config(argv: list[str]) -> int:
    from issuesmith.config import ConfigError, get_config
    from issuesmith.gates import validate_step_requires
    from issuesmith.lines import LINES, extract_line

    if not argv:
        return fail("MISSING_ARGUMENT", "subcommand required", _config_subcommand_hint())
    parser = _config_parser()
    if argv[0] in {"-h", "--help"}:
        parser.print_help()
        return 0
    parsed = _parse_args(parser, argv)
    if isinstance(parsed, int):
        return parsed
    args = parsed
    if args.subcmd == "extract-line":
        if args.line_name not in LINES:
            return fail(
                "INVALID_VALUE",
                f"unknown line: {args.line_name}",
                f"lines: {', '.join(LINES)}",
            )
        dest = Path(args.dest_dir)
        try:
            written = extract_line(args.line_name, dest)
        except FileExistsError as exc:
            return fail("CONFLICT", str(exc))
        dest_resolved = dest.resolve()
        for path in sorted(written, key=lambda p: str(p.relative_to(dest_resolved))):
            print(path.relative_to(dest_resolved))
        return 0
    if args.subcmd != "show":
        return fail("UNKNOWN_COMMAND", f"unknown config subcommand: {args.subcmd}")
    cfg = get_config()
    print(json.dumps(asdict(cfg), default=_json_default, indent=2))
    try:
        validate_step_requires(cfg.steps)
    except ConfigError as exc:
        return fail("CONFIG_ERROR", f"invalid requires: {exc}")
    return 0


def _cmd_observe(argv: list[str]) -> int:
    import json as _json

    parser = ContractParser(command="observe")
    parser.add_argument("--apply", action="store_true", help="Execute policy actions")
    parser.add_argument("--json", action="store_true", dest="as_json", help="JSON output")
    parsed = _parse_args(parser, argv)
    if isinstance(parsed, int):
        return parsed
    args = parsed

    from ghdag.forge import get_forge

    from issuesmith.config import get_config
    from issuesmith.observe import observe
    from issuesmith.queue_store import QueueStore

    cfg = get_config()
    store = QueueStore()
    snapshot = store.snapshot()
    client = get_forge()

    events = observe(snapshot, client, cfg)

    if args.as_json:
        import dataclasses
        print(_json.dumps([dataclasses.asdict(e) for e in events], ensure_ascii=False, default=_json_default))
    else:
        for evt in events:
            print(f"  {evt.kind}: {evt}")

    if args.apply:
        from issuesmith.observe.policy import evaluate, execute
        actions = evaluate(events, cfg.observe)
        execute(actions, store, sinks=[], client=client)
        if not args.as_json:
            print(f"applied {len(actions)} action(s)")

    return 0


def _cmd_metrics_rework(argv: list[str]) -> int:
    from issuesmith.metrics_rework import main as metrics_rework_main

    return int(metrics_rework_main(argv))


def _cmd_metrics(argv: list[str]) -> int:
    if not argv or argv[0] != "rework":
        print("metrics: expected 'rework'", file=sys.stderr)
        return 2
    return _cmd_metrics_rework(argv[1:])


def _cmd_main_health(_argv: list[str]) -> int:
    from issuesmith.config import get_config
    from issuesmith.observe.main_health import (
        MainHealthError,
        check,
        state_path,
    )

    cfg = get_config()
    mh = cfg.observe.main_health
    if mh is None:
        print("main_health: disabled")
        return 0
    had_worktree = mh.worktree.is_dir()
    try:
        state = check(mh, state_path(cfg), repo_root=cfg.root)
    except MainHealthError as exc:
        print(f"main_health: {exc}", file=sys.stderr)
        return 2
    if not had_worktree and mh.worktree.is_dir():
        print(f"main_health: created worktree {mh.worktree}")
    print(f"main_health: {state.status} {state.sha[:12]}")
    if state.failing:
        for test_id in state.failing:
            print(f"  {test_id}")
    elif state.status == "red" and state.output_tail:
        tail_lines = state.output_tail.splitlines()[-20:]
        for line in tail_lines:
            print(f"  | {line}")
    return 0


def _lanes_role_engine_map(cfg: Any) -> dict[str, str]:
    from issuesmith.queue import resolve_engine

    return {ph.role: resolve_engine(ph.name) for ph in cfg.phases}


def _lanes_parser() -> ContractParser:
    parser = ContractParser(command="lanes")
    sub = parser.add_subparsers(dest="subcmd", required=True)
    p_check = sub.add_parser("check")
    p_check.add_argument("--apply", action="store_true")
    p_check.add_argument("--json", action="store_true")
    p_check.add_argument("--out", metavar="PATH")
    p_report = sub.add_parser("report")
    p_report.add_argument("--json", action="store_true")
    return parser


def _cmd_lanes(argv: list[str]) -> int:
    from datetime import datetime

    from ghdag.forge import get_forge

    from issuesmith.andon import plan_auto_answers
    from issuesmith.config import ConfigError, get_config
    from issuesmith.lanes import (
        apply,
        check,
        check_to_dict,
        count_merge_done_since,
        fetch_issue_views,
        ledger_state_path,
        load_ledger,
        load_report_state,
        report,
        resolve_report_since,
        save_report_state,
        select_workflow,
    )
    from issuesmith.queue import plan as queue_plan_fn
    from issuesmith.queue_store import QueueStore
    from issuesmith.quota_gate import is_github_api_low, read_github_api_state

    if not argv:
        return fail(
            "MISSING_ARGUMENT",
            "subcommand required",
            "subcommands: check, report",
        )
    parser = _lanes_parser()
    if argv[0] in {"-h", "--help"}:
        parser.print_help()
        return 0
    parsed = _parse_args(parser, argv)
    if isinstance(parsed, int):
        return parsed
    args = parsed
    cfg = get_config()
    if cfg.paths.lanes is None:
        print("lanes: paths.lanes is not configured", file=sys.stderr)
        return 2

    ledger_path = cfg.paths.lanes
    try:
        ledger = load_ledger(ledger_path, default_api_min=cfg.api_brake.min_remaining)
    except ConfigError as exc:
        print(f"lanes: {exc}", file=sys.stderr)
        return 2

    ledger = select_workflow(ledger, cfg.label_namespace)

    apply_enqueue = getattr(args, "apply", False)
    as_json = args.json
    now = datetime.now().astimezone()
    client = get_forge()
    snapshot = QueueStore().snapshot()
    audit_path = cfg.paths.exec_jsonl.parent / "audit.jsonl"
    api_state = read_github_api_state(audit_path)
    api_low = is_github_api_low(api_state, ledger.api_min_remaining, now)

    issue_numbers: set[int] = set(ledger.hold) | set(ledger.backlog)
    for nums in ledger.lanes.values():
        issue_numbers.update(nums)

    issues = fetch_issue_views(client, issue_numbers)

    queue_plan = None
    if not api_low:
        queue_plan = queue_plan_fn(QueueStore(), client, now=now)

    api_remaining = api_state.remaining if api_state is not None else None
    role_engine_map = _lanes_role_engine_map(cfg)
    lane_check = check(
        ledger,
        snapshot,
        issues,
        cfg,
        now,
        role_engine_map=role_engine_map,
        queue_plan=queue_plan,
        api_remaining=api_remaining,
        api_low=api_low,
    )
    lane_plan = lane_check.plan

    if args.subcmd == "check":
        out_path = getattr(args, "out", None)
        check_payload = check_to_dict(lane_check)
        if out_path:
            save_report_state(Path(str(out_path)), check_payload)
        if apply_enqueue and not api_low and not lane_plan.halt:
            apply(lane_plan, QueueStore())
        if as_json:
            print(json.dumps(check_payload, ensure_ascii=False))
            return 0
        for entry in lane_plan.enqueue:
            print(f"enqueue #{entry['issue']} phase={entry['phase']} lane={entry.get('lane')}")
        for entry in lane_plan.escalations:
            print(f"escalation {entry['kind']}")
        return 0

    if args.subcmd == "report":
        from issuesmith.observe.dag_state import load_dag_states

        dag_states = load_dag_states(
            cfg.paths.exec_jsonl,
            cfg.paths.done_dir,
            cfg.paths.done_dir.parent / "running",
        )
        active_issues = {
            issue
            for issue, dag in dag_states.items()
            if dag.status in ("running", "pending")
        }
        auto_plan = plan_auto_answers(
            client, cfg.andon.auto_answer, active_issues=active_issues
        )
        since = resolve_report_since(ledger.report_since, now, cfg.timezone)
        merged = count_merge_done_since(client, since, cfg) if since.strip() else None
        state_path = ledger_state_path(ledger_path)
        state = load_report_state(state_path)
        payload, summary = report(lane_plan, auto_plan, merged, state, now, ledger)
        if payload.get("report_due"):
            state["last_report_at"] = now.isoformat()
            state["enqueue"] = lane_plan.enqueue
            state["escalations"] = payload["escalations"]
            save_report_state(state_path, state)
        if as_json:
            print(json.dumps(payload, ensure_ascii=False))
            return 0
        print(summary)
        return 0

    return fail("UNKNOWN_COMMAND", f"unknown lanes subcommand: {args.subcmd}")


_HANDLERS = {
    "context": _run_context,
    "gate": _cmd_gate,
    "gate-preflight": _run_gate_preflight,
    "verify": _cmd_verify,
    "queue": _cmd_queue,
    "deps": _cmd_deps,
    "tier": _cmd_tier,
    "comments": _cmd_comments,
    "gh": _cmd_gh,
    "engine": _cmd_engine,
    "dispatch": _cmd_dispatch,
    "publish": _cmd_publish,
    "labels": _cmd_labels,
    "doctor": _cmd_doctor,
    "smoke": _cmd_smoke,
    "gen-live": _cmd_gen_live,
    "version-bump": _cmd_version_bump,
    "resume": _cmd_resume,
    "convert-to-milestone": _cmd_convert_to_milestone,
    "andon": _cmd_andon,
    "milestone": _cmd_milestone,
    "config": _cmd_config,
    "observe": _cmd_observe,
    "metrics": _cmd_metrics,
    "main-health": _cmd_main_health,
    "lanes": _cmd_lanes,
}


def main(argv: Sequence[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        command_hint = ", ".join(sorted(_HANDLERS))
        raise SystemExit(fail("MISSING_ARGUMENT", "command required", f"commands: {command_hint}"))
    if args[0] in {"-h", "--help"}:
        print(_USAGE, end="")
        raise SystemExit(0)
    cmd, *rest = args
    handler = _HANDLERS.get(cmd)
    if handler is None:
        command_hint = ", ".join(sorted(_HANDLERS))
        raise SystemExit(
            fail("UNKNOWN_COMMAND", f"unknown command: {cmd}", f"commands: {command_hint}")
        )
    raise SystemExit(handler(rest))


if __name__ == "__main__":
    main()
