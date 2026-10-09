#!/usr/bin/env python3
"""**Runtime environment** health check to run before driving the issuesmith pipeline.

A **runtime check**, as opposed to the (static) template render tests. Exits 1
unless all of the following hold. Independently of template / yml changes, it
verifies that the environment where ghdag actually runs (SHR / dag-runner /
ghdag_runner) is not broken.

Checks:

  1. pyproject.toml has no direct ghdag dependency (confirms it is transitive)
  2. `import ghdag` succeeds and `get_adapter('shell')` resolves correctly

ghdag is obtained as a transitive dependency via mltgnt (#1148). A direct
dependency would mean double bookkeeping, with diary and mltgnt pinning
different versions.

Past incident: pip resolve read a stale egg-info and kept downgrading ghdag to
v0.15.0; ShellAdapter disappeared and every SHR dispatch failed. Template render
tests could never have caught it.

CLAUDE.md rule: a PR that changes workflows/issuesmith/* / pyproject.toml /
scripts/diary_hooks.py / scripts/dag-runner.py must run this script **in the
real Python environment** and confirm exit 0 before merging.
"""
from __future__ import annotations

import importlib
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

import yaml

from issuesmith.config import get_config

REPO_ROOT = get_config().root
PYPROJECT = REPO_ROOT / "pyproject.toml"

_GHDAG_PIN_RE = re.compile(
    r"ghdag\s*@\s*git\+https://github\.com/sumipan/ghdag\.git@v?(\d+\.\d+\.\d+)"
)


def _check_pyproject_no_direct_pin() -> tuple[bool, str]:
    """Confirm pyproject.toml has no direct ghdag dependency (it should be transitive)."""
    text = PYPROJECT.read_text(encoding="utf-8")
    m = _GHDAG_PIN_RE.search(text)
    if m:
        return False, (
            f"pyproject.toml has a direct ghdag pin: v{m.group(1)}\n"
            "Obtain ghdag as a transitive dependency via mltgnt. Remove the direct pin."
        )
    return True, "pyproject.toml: no direct ghdag dependency (obtained transitively)"


# ghdag names issuesmith reaches at runtime through getattr(..., None) or late imports, so a
# missing name would not fail at import time. Kept in sync with
# tests/conventions/test_required_upstream_apis_exist.py (sumipan/nexus#3571).
REQUIRED_UPSTREAM_APIS: tuple[tuple[str, str, str | None], ...] = (
    ("ghdag.quota", "QuotaGate", "defer"),
    ("ghdag.quota", "QuotaGate", "release_ready"),
    ("ghdag.core.vocabulary", "DONE_DEFERRED", None),
    ("ghdag.forge", "get_forge", None),
)


def missing_upstream_apis(
    required: tuple[tuple[str, str, str | None], ...] = REQUIRED_UPSTREAM_APIS,
    *,
    resolver: Callable[[str], Any] = importlib.import_module,
) -> list[str]:
    """Return dotted names from *required* that the installed ghdag does not provide."""
    missing: list[str] = []
    for module, name, attr in required:
        dotted = f"{module}.{name}" + (f".{attr}" if attr else "")
        try:
            obj = getattr(resolver(module), name)
        except (ImportError, AttributeError):
            missing.append(dotted)
            continue
        if attr and not hasattr(obj, attr):
            missing.append(dotted)
    return missing


def _check_upstream_apis() -> tuple[bool, str]:
    missing = missing_upstream_apis()
    if missing:
        return False, f"upstream_apis: missing: {', '.join(missing)} (bump the ghdag pin)"
    return True, "upstream_apis: ok"


def _check_installed_ghdag() -> tuple[bool, str]:
    try:
        import ghdag  # noqa: F401
    except ImportError as e:
        return False, f"cannot import ghdag: {e}"

    try:
        from importlib.metadata import version as _pkg_version
        actual = _pkg_version("ghdag")
    except Exception as e:
        return False, f"importlib.metadata.version('ghdag') failed: {e}"

    return True, f"installed ghdag: v{actual} (transitive dependency via mltgnt)"


def _check_shell_adapter() -> tuple[bool, str]:
    try:
        from ghdag.workflow import engine as workflow_engine
    except ImportError as e:
        return False, f"failed to import ghdag.workflow.engine: {e}"

    get_adapter = getattr(workflow_engine, "get_adapter", None)
    if get_adapter is None:
        return False, "ghdag.workflow.engine.get_adapter not found"
    adapter_not_found_error = getattr(
        workflow_engine,
        "AdapterNotFoundError",
        ValueError,
    )

    try:
        adapter = get_adapter("shell")
    except (adapter_not_found_error, ValueError) as e:
        return False, (
            f"get_adapter('shell') failed: {e}\n"
            "  Fix: reinstall ghdag v0.34.0 or later"
        )
    return True, f"shell adapter OK: {type(adapter).__name__}"


def _parse_skill_frontmatter(text: str) -> dict | None:
    """Parse the ---delimited frontmatter at the top of SKILL.md; None if malformed."""
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---", 4)
    if end < 0:
        return None
    try:
        data = yaml.safe_load(text[4:end])
    except yaml.YAMLError:
        return None
    return data if isinstance(data, dict) else None


def _check_agent_skill_manifests() -> tuple[bool, str]:
    """Validate the agent skills manifests Codex scans at session initialization.

    A single malformed SKILL.md (missing frontmatter, empty name/description) makes
    the whole design role Codex session fail to start, even for unrelated Issues (#2523).
    """
    skills_dir = Path(
        os.environ.get("AGENT_SKILLS_DIR", str(Path.home() / ".agents" / "skills"))
    )
    if not skills_dir.is_dir():
        return True, f"agent skills: no directory (skipped): {skills_dir}"

    bad: list[str] = []
    for manifest in sorted(skills_dir.glob("*/SKILL.md")):
        try:
            text = manifest.read_text(encoding="utf-8")
        except OSError as exc:
            bad.append(f"{manifest}: read failed: {exc}")
            continue
        fm = _parse_skill_frontmatter(text)
        if fm is None:
            bad.append(f"{manifest}: frontmatter missing or invalid YAML")
            continue
        name = fm.get("name")
        description = fm.get("description")
        if not (isinstance(name, str) and name.strip()):
            bad.append(f"{manifest}: name is empty")
        if not (isinstance(description, str) and description.strip()):
            bad.append(f"{manifest}: description is empty")
    if bad:
        return False, "invalid agent skills manifest (the Codex design role fails to start): " + "; ".join(bad)
    return True, f"agent skills: every SKILL.md manifest in {skills_dir} OK"


def _repo_to_pkg_name(repo: str) -> str:
    return repo.rsplit("/", 1)[-1]


def _check_post_merge_stable_install(item: dict) -> tuple[bool, str]:
    repo = item.get("repo", "")
    path = item.get("path", "")
    pkg = _repo_to_pkg_name(str(repo))
    try:
        result = subprocess.run(
            ["pip", "show", pkg],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return False, f"pip show {pkg} failed: {exc}"
    if result.returncode != 0:
        recovery = f'pip install -e "{path}/[dev]" --no-deps'
        return False, f"stable_install: {pkg} not installed — {recovery}"
    editable = ""
    for line in (result.stdout or "").splitlines():
        if line.startswith("Editable project location:"):
            editable = line.split(":", 1)[1].strip()
            break
    if editable == str(path):
        return True, f"stable_install: {pkg} editable at {path}"
    recovery = f'pip install -e "{path}/[dev]" --no-deps'
    return (
        False,
        f"stable_install: editable location {editable!r} != {path!r} — {recovery}",
    )


def _check_post_merge_tag(item: dict) -> tuple[bool, str]:
    repo = str(item.get("repo", ""))
    tag = str(item.get("tag", ""))
    url = f"https://github.com/{repo}.git"
    try:
        result = subprocess.run(
            ["git", "ls-remote", "--tags", url, tag],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return False, f"git ls-remote failed: {exc}"
    lines = [line for line in (result.stdout or "").strip().splitlines() if line]
    if lines:
        return True, f"tag: {tag} exists on {repo}"
    path = item.get("path", f"/var/tmp/{_repo_to_pkg_name(repo)}")
    recovery = f"cd {path} && git tag {tag} && git push origin {tag}"
    return False, f"tag {tag} not found on {repo} — {recovery}"


def _check_post_merge_restart(item: dict) -> tuple[bool, str]:
    processes = item.get("processes", [])
    if not isinstance(processes, list) or not processes:
        return False, "restart: processes must be a non-empty list"
    try:
        result = subprocess.run(
            ["overmind", "status"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        recovery = f"overmind restart {' '.join(str(p) for p in processes)}"
        return False, f"overmind status failed: {exc} — {recovery}"
    output = result.stdout or ""
    missing = [p for p in processes if p not in output or "running" not in output]
    if missing and result.returncode != 0:
        recovery = f"overmind restart {' '.join(str(p) for p in processes)}"
        return False, f"restart: processes not running: {', '.join(missing)} — {recovery}"
    for proc in processes:
        proc_str = str(proc)
        if proc_str not in output:
            recovery = f"overmind restart {' '.join(str(p) for p in processes)}"
            return False, f"restart: {proc_str} not found in overmind status — {recovery}"
    return True, f"restart: {', '.join(str(p) for p in processes)} running"


def check_post_merge(items: list[dict]) -> list[tuple[bool, str]]:
    """Runtime verification of post_merge contract items; returns (ok, message) per item."""
    handlers = {
        "stable_install": _check_post_merge_stable_install,
        "tag": _check_post_merge_tag,
        "restart": _check_post_merge_restart,
    }
    results: list[tuple[bool, str]] = []
    for item in items:
        kind = item.get("kind")
        handler = handlers.get(kind) if isinstance(kind, str) else None
        if handler is None:
            results.append((False, f"unknown post_merge kind: {kind}"))
            continue
        results.append(handler(item))
    return results


def _check_advance_when() -> tuple[bool, str]:
    """Check that all phase advance_when predicates resolve."""
    try:
        from issuesmith.config import get_config
        from issuesmith.ops.doctor import advance_when_report

        cfg = get_config()
        report = advance_when_report(cfg.phases)
        ok = report == "advance_when: ok"
        return ok, report
    except Exception as exc:
        return False, f"advance_when: FAIL ({exc})"


def _check_requires_chain() -> tuple[bool, str]:
    """Check that all step requires declarations reference known gate ids."""
    from issuesmith.config import ConfigError

    try:
        from issuesmith.config import get_config
        from issuesmith.ops.doctor import requires_chain_report
        cfg = get_config()
        report = requires_chain_report(cfg.steps)
        ok = report == "requires_chain: ok"
        return ok, report
    except ConfigError as exc:
        missing = str(exc)
        return False, f"requires_chain: FAIL {missing}"
    except Exception as exc:
        return False, f"requires_chain: FAIL (error: {exc})"


def main() -> int:
    print("=== issuesmith preflight ===")
    failures: list[str] = []

    for fn in (
        _check_pyproject_no_direct_pin,
        _check_installed_ghdag,
        _check_upstream_apis,
        _check_shell_adapter,
        _check_agent_skill_manifests,
        _check_requires_chain,
        _check_advance_when,
    ):
        ok, msg = fn()
        print(("OK " if ok else "FAIL ") + msg)
        if not ok:
            failures.append(msg)

    print()
    if failures:
        print(f"=== PREFLIGHT FAILED ({len(failures)} item(s)) ===")
        return 1
    print("=== PREFLIGHT PASSED ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
