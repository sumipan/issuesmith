import re
import subprocess
import sys

from issuesmith.config import get_config

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


def _explicit_issue_create_repo(argv: list[str]) -> str | None:
    """Return the --repo value on `issue create`, or None when omitted."""
    if len(argv) < 2 or argv[0] != "issue" or argv[1] != "create":
        return None
    for i, arg in enumerate(argv):
        if arg == "--repo" and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith("--repo="):
            return arg[len("--repo=") :]
    return None


def _issue_create_target(argv: list[str], configured_repo: str) -> str | None:
    """Return a disallowed --repo value for `issue create`, else None."""
    explicit = _explicit_issue_create_repo(argv)
    if explicit is None:
        return None
    return None if explicit == configured_repo else explicit


def _inject_issue_create_repo(argv: list[str], configured_repo: str) -> list[str]:
    """Ensure `issue create` passes --repo configured_repo when --repo is omitted."""
    if len(argv) < 2 or argv[0] != "issue" or argv[1] != "create":
        return argv
    if _explicit_issue_create_repo(argv) is not None:
        return argv
    return [*argv, "--repo", configured_repo]


def main() -> None:
    configured_repo = get_config().repo
    argv = sys.argv[1:]
    if _has_ready_label_in_argv(argv):
        print(
            "Error: Direct addition of issuesmith:draft|develop|merge-ready labels is prohibited. "
            "Use `python3 -m issuesmith queue enqueue` instead.",
            file=sys.stderr,
        )
        sys.exit(2)
    target = _issue_create_target(argv, configured_repo)
    if target is not None:
        print(
            f"Error: Issue creation is allowed only in {configured_repo} "
            f"(CLAUDE.md §6). Got --repo {target}",
            file=sys.stderr,
        )
        sys.exit(2)
    argv = _inject_issue_create_repo(argv, configured_repo)
    completed = subprocess.run(
        [sys.executable, "-m", "ghdag.github_cli", *argv],
        check=False,
    )
    sys.exit(completed.returncode)


if __name__ == "__main__":
    main()
