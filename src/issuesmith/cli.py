"""Unified issuesmith CLI (``python3 -m issuesmith <subcommand>``)."""

from __future__ import annotations

import importlib
import sys
from typing import Sequence

_STASH_MOVED_MSG = (
    "apply / ingest-review は tools/stash/ に移動しました。"
    "例: python3 tools/stash/apply.py <path>"
)

_USAGE = """\
usage: issuesmith <command> ...

commands:
  context|context_hook|context-hook
  gate|gate-preflight|cp1-gate|m2-gate
  verify|b1-verify
  queue  deps  tier  comments  gh
  engine  dispatch  publish  labels  doctor  smoke  gen-live  version-bump
  recover  redispatch
  apply  ingest-review  (moved to tools/stash/; exit 2)
"""


def _run_context(argv: list[str]) -> int:
    sys.argv = ["issuesmith.context_hook", *argv]
    from issuesmith.context_hook import main as hook_main

    hook_main()
    return 0


def _run_gate_preflight(argv: list[str]) -> int:
    import issuesmith.gate_rules  # noqa: F401 — populates GATE_REGISTRY

    sys.argv = ["ghdag.workflow.gates", *argv]
    gates_main = importlib.import_module("ghdag.workflow.gates.__main__")
    gates_main.main()
    return 0


def _run_cp1_gate(argv: list[str]) -> int:
    sys.argv = ["issuesmith.cp1_gate", *argv]
    from issuesmith.cp1_gate import main as cp1_gate_main

    cp1_gate_main()
    return 0


def _run_m2_gate(argv: list[str]) -> int:
    sys.argv = ["issuesmith.m2_gate", *argv]
    from issuesmith.m2_gate import main as m2_gate_main

    m2_gate_main()
    return 0


def _run_b1_verify(argv: list[str]) -> int:
    sys.argv = ["issuesmith.b1_verify", *argv]
    from issuesmith.b1_verify import main as b1_verify_main

    return int(b1_verify_main())


def _cmd_gate(argv: list[str]) -> int:
    if not argv:
        print("gate: gate name required", file=sys.stderr)
        return 2
    gate_name, *rest = argv

    # New form: gate <name> --body-file ...  (== gate-preflight --gate <name> ...)
    if "--body-file" in rest or (rest and rest[0].startswith("-") and "--gate" not in rest):
        return _run_gate_preflight(["--gate", gate_name, *rest])

    if gate_name == "cp1" and rest and not rest[0].startswith("-"):
        return _run_cp1_gate(rest)
    if gate_name == "m2" and rest and not rest[0].startswith("-"):
        return _run_m2_gate(rest)

    # Explicit preflight-style flags after gate name
    if rest:
        return _run_gate_preflight(["--gate", gate_name, *rest])

    print(f"gate {gate_name}: --body-file か issue 番号が必要です", file=sys.stderr)
    return 2


def _cmd_verify(argv: list[str]) -> int:
    if not argv:
        print("verify: target required (b1)", file=sys.stderr)
        return 2
    name, *rest = argv
    if name != "b1":
        print(f"Unknown verify target: {name}", file=sys.stderr)
        return 2
    return _run_b1_verify(rest)


def _cmd_queue(argv: list[str]) -> int:
    from issuesmith.queue import main as queue_main

    return int(queue_main(argv))


def _cmd_deps(argv: list[str]) -> int:
    sys.argv = ["issuesmith.dep_extractor", *argv]
    from issuesmith.dep_extractor import main as deps_main

    deps_main()
    return 0


def _cmd_tier(argv: list[str]) -> int:
    if not argv:
        print("tier: b1 or cp2 required", file=sys.stderr)
        return 2
    name, *rest = argv
    if name == "b1":
        sys.argv = ["issuesmith.b1_tier", *rest]
        from issuesmith.b1_tier import main as tier_main

        tier_main()
        return 0
    if name == "cp2":
        sys.argv = ["issuesmith.cp2_tier", *rest]
        from issuesmith.cp2_tier import main as tier_main

        tier_main()
        return 0
    print(f"Unknown tier: {name}", file=sys.stderr)
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

    return int(dispatch_main(argv))


def _cmd_publish(argv: list[str]) -> int:
    from issuesmith.ops.publish import main as publish_main

    return int(publish_main(argv))


def _cmd_labels(argv: list[str]) -> int:
    if not argv or argv[0] != "hygiene":
        print("labels: expected 'hygiene'", file=sys.stderr)
        return 2
    from issuesmith.ops.label_hygiene import main as hygiene_main

    return int(hygiene_main(argv[1:]))


def _cmd_doctor(_argv: list[str]) -> int:
    from issuesmith.ops.preflight import main as preflight_main

    return int(preflight_main())


def _cmd_smoke(argv: list[str]) -> int:
    from issuesmith.ops.smoke import main as smoke_main

    return int(smoke_main(argv))


def _cmd_gen_live(argv: list[str]) -> int:
    from issuesmith.ops.gen_live_dispatch import main as gen_main

    return int(gen_main(argv))


def _cmd_version_bump(argv: list[str]) -> int:
    from issuesmith.ops.version_bump import main as bump_main

    return int(bump_main(argv))


def _cmd_recover(argv: list[str]) -> int:
    from issuesmith.recovery import main as recovery_main

    return int(recovery_main(["recover", *argv]))


def _cmd_redispatch(argv: list[str]) -> int:
    from issuesmith.recovery import main as recovery_main

    return int(recovery_main(["redispatch", *argv]))


def _cmd_stash_moved(_argv: list[str]) -> int:
    print(_STASH_MOVED_MSG, file=sys.stderr)
    return 2


_HANDLERS = {
    "context": _run_context,
    "context_hook": _run_context,
    "context-hook": _run_context,
    "gate": _cmd_gate,
    "gate-preflight": _run_gate_preflight,
    "cp1-gate": _run_cp1_gate,
    "m2-gate": _run_m2_gate,
    "verify": _cmd_verify,
    "b1-verify": _run_b1_verify,
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
    "recover": _cmd_recover,
    "redispatch": _cmd_redispatch,
    "apply": _cmd_stash_moved,
    "ingest-review": _cmd_stash_moved,
}


def main(argv: Sequence[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help"}:
        print(_USAGE, end="")
        raise SystemExit(0 if args else 1)
    cmd, *rest = args
    handler = _HANDLERS.get(cmd)
    if handler is None:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        print(_USAGE, end="", file=sys.stderr)
        raise SystemExit(1)
    raise SystemExit(handler(rest))


if __name__ == "__main__":
    main()
