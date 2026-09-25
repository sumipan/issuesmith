"""python3 -m issuesmith のエントリポイント。"""
from __future__ import annotations

import sys

from issuesmith.cli import main

_ANDON_USAGE = """\
usage: issuesmith andon <subcommand> ...

subcommands:
  list [--all] [--json]    list open (unanswered) andons (--json: records with raised_at / notes)
  show <andon-id>          show a specific andon by id
  answer <andon-id> <action>  post answer, remove label, call resume hook
  note <andon-id> --key <key> --value <value>  record a note on an open andon (no label change)
"""

_LABELS_USAGE = """\
usage: issuesmith labels <subcommand> ...

subcommands:
  reconcile [--fix] [--json]   report (or fix) managed-label divergences across all open Issues
"""


def _cmd_andon(argv: list[str]) -> int:
    import json

    from issuesmith.andon import answer, list_open, list_open_records, note, to_comment

    if not argv:
        print(_ANDON_USAGE, end="", file=sys.stderr)
        return 1
    if argv[0] in {"-h", "--help"}:
        print(_ANDON_USAGE, end="")
        return 0

    sub, *rest = argv

    if sub == "list":
        show_all = "--all" in rest
        as_json = "--json" in rest
        from ghdag.forge import get_forge
        client = get_forge()

        def _sort_key(kind: str, step: str) -> int:
            if kind == "decision":
                return 0
            if kind == "broken":
                return 1
            if kind == "blocked" and step != "observe":
                return 2
            return 3

        if as_json:
            records = list_open_records(client)
            if not show_all:
                records = sorted(records, key=lambda r: _sort_key(r["kind"], r["step"]))
            print(json.dumps(records, ensure_ascii=False))
            return 0
        andons = list_open(client)
        if not show_all:
            andons = sorted(andons, key=lambda a: _sort_key(a.kind, a.step))
        if not andons:
            print("no open andons")
        for a in andons:
            print(f"{a.id}\t{a.kind}\tissue #{a.issue}\t{a.summary}")
        return 0

    if sub == "show":
        if not rest:
            print("andon show: <andon-id> required", file=sys.stderr)
            return 2
        andon_id = rest[0]
        from ghdag.forge import get_forge
        client = get_forge()
        andons = list_open(client)
        matched = [a for a in andons if a.id == andon_id]
        if not matched:
            print(f"andon not found: {andon_id}", file=sys.stderr)
            return 1
        a = matched[0]
        print(to_comment(a))
        return 0

    if sub == "answer":
        if len(rest) < 2:
            print("andon answer: <andon-id> <action> required", file=sys.stderr)
            return 2
        andon_id, action = rest[0], rest[1]
        from ghdag.forge import get_forge
        client = get_forge()
        answer(client, andon_id, action)
        print(f"answered {andon_id} with: {action}")
        return 0

    if sub == "note":
        note_id: str | None = None
        opts: dict[str, str] = {}
        i = 0
        while i < len(rest):
            arg = rest[i]
            if arg in {"--key", "--value"}:
                if i + 1 >= len(rest):
                    note_id = None
                    opts.clear()
                    break
                opts[arg[2:]] = rest[i + 1]
                i += 2
                continue
            if note_id is None:
                note_id = arg
            i += 1
        if note_id is None or "key" not in opts or "value" not in opts:
            print("andon note: <andon-id> --key <key> --value <value> required", file=sys.stderr)
            print(_ANDON_USAGE, end="", file=sys.stderr)
            return 2
        from ghdag.forge import get_forge
        client = get_forge()
        try:
            note(client, note_id, opts["key"], opts["value"])
        except KeyError:
            print(f"andon not found: {note_id}", file=sys.stderr)
            return 1
        except ValueError as exc:
            print(f"andon note: {exc}", file=sys.stderr)
            return 2
        print(f"noted {note_id}: {opts['key']}={opts['value']}")
        return 0

    print(f"andon: unknown subcommand: {sub}", file=sys.stderr)
    print(_ANDON_USAGE, end="", file=sys.stderr)
    return 2


def _cmd_labels(argv: list[str]) -> int:
    if not argv:
        print(_LABELS_USAGE, end="", file=sys.stderr)
        return 1
    if argv[0] in {"-h", "--help"}:
        print(_LABELS_USAGE, end="")
        return 0

    sub, *rest = argv

    if sub == "reconcile":
        fix = "--fix" in rest
        as_json = "--json" in rest
        from ghdag.forge import get_forge

        from issuesmith.ops.labels import reconcile
        client = get_forge()
        reconcile(client, fix=fix, as_json=as_json)
        return 0

    print(f"labels: unknown subcommand: {sub}", file=sys.stderr)
    print(_LABELS_USAGE, end="", file=sys.stderr)
    return 2


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "andon":
        raise SystemExit(_cmd_andon(args[1:]))
    if args and args[0] == "labels":
        raise SystemExit(_cmd_labels(args[1:]))
    main()
