import re
import sys

from ghdag.github_cli import cli_main
from ghdag.github_client import DEFAULT_REPO

_READY_LABEL_RE = re.compile(r"issuesmith:(?:draft|develop|merge)-ready")


def _has_ready_label_in_argv(argv: list[str]) -> bool:
    for i, arg in enumerate(argv):
        if arg == "--add-label" and i + 1 < len(argv):
            if _READY_LABEL_RE.search(argv[i + 1]):
                return True
        elif arg.startswith("--add-label="):
            if _READY_LABEL_RE.search(arg):
                return True
    return False


def _issue_create_target(argv: list[str]) -> str | None:
    """Return non-nexus --repo value for `issue create`, else None.

    Issue 作成は sumipan/nexus のみ許可（CLAUDE.md §6）。
    `--repo` 省略時は DEFAULT_REPO が使われるため安全として通過させる。
    """
    if len(argv) < 2 or argv[0] != "issue" or argv[1] != "create":
        return None
    for i, arg in enumerate(argv):
        if arg == "--repo" and i + 1 < len(argv):
            repo = argv[i + 1]
            return None if repo == DEFAULT_REPO else repo
        if arg.startswith("--repo="):
            repo = arg[len("--repo=") :]
            return None if repo == DEFAULT_REPO else repo
    return None


def main() -> None:
    argv = sys.argv[1:]
    if _has_ready_label_in_argv(argv):
        print(
            "Error: Direct addition of issuesmith:draft|develop|merge-ready labels is prohibited. "
            "Use `python3 -m issuesmith.queue enqueue` instead.",
            file=sys.stderr,
        )
        sys.exit(2)
    target = _issue_create_target(argv)
    if target is not None:
        print(
            f"Error: Issue creation is allowed only in {DEFAULT_REPO} "
            f"(CLAUDE.md §6). Got --repo {target}",
            file=sys.stderr,
        )
        sys.exit(2)
    sys.exit(cli_main(argv))


if __name__ == "__main__":
    main()
