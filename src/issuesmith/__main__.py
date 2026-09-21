"""python3 -m issuesmith のエントリポイント。"""
from __future__ import annotations

import sys

from issuesmith.cli import main

_ANDON_USAGE = """\
usage: issuesmith andon <subcommand> ...

subcommands:
  list                     list open (unanswered) andons
  show <andon-id>          show a specific andon by id
  answer <andon-id> <action>  post answer, remove label, call resume hook
"""


def _cmd_andon(argv: list[str]) -> int:
    from issuesmith.andon import answer, list_open, to_comment

    if not argv:
        print(_ANDON_USAGE, end="", file=sys.stderr)
        return 1
    if argv[0] in {"-h", "--help"}:
        print(_ANDON_USAGE, end="")
        return 0

    sub, *rest = argv

    if sub == "list":
        from ghdag.forge import get_forge
        client = get_forge()
        andons = list_open(client)
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

    print(f"andon: unknown subcommand: {sub}", file=sys.stderr)
    print(_ANDON_USAGE, end="", file=sys.stderr)
    return 2


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "andon":
        raise SystemExit(_cmd_andon(args[1:]))
    main()
