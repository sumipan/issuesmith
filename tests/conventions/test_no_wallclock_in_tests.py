"""Convention: docs/TEST.md T13 — tests must not depend on real wall-clock time.

Line scan of ``tests/**/*.py`` (excluding ``tests/conventions/``). Known paths
live in ``known_wallclock_tests.txt``. The ledger may only shrink when
wall-clock usage is removed, never grow.
"""
from __future__ import annotations

import re
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib

from tests.conventions._util import REPO_ROOT

_LEDGER_FILE = Path(__file__).resolve().parent / "known_wallclock_tests.txt"
_CONVENTIONS_PREFIX = "tests/conventions/"
_POSITIVE_NUM = re.compile(r"(?<![\w.])(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?(?![\w.])")

_RE_TIME_SLEEP = re.compile(r"\btime\.sleep\s*\(")
_RE_ASYNCIO_SLEEP = re.compile(r"\basyncio\.sleep\s*\(\s*([^)]*)\)")
_RE_TIMEOUT_CALL = re.compile(
    r"\.(?:communicate|join|wait|result|get)\([^)]*\btimeout\s*=\s*([^,\s)]+)"
)
_RE_TIME_CLOCK = re.compile(r"\btime\.(?:monotonic|perf_counter|time)\s*\(")
_RE_PERF_COUNTER = re.compile(r"(?<![\w.])perf_counter\s*\(")
_RE_DATETIME_NOW = re.compile(r"\bdatetime\.(?:now|utcnow)\s*\(")


def _literal_is_positive(fragment: str) -> bool:
    m = _POSITIVE_NUM.search(fragment.strip())
    if not m:
        return False
    token = m.group(0)
    try:
        if "." in token or "e" in token.lower():
            return float(token) > 0
        return int(token) > 0
    except ValueError:
        return False


def _line_has_mock_clock_patch(line: str) -> bool:
    return "monkeypatch" in line and "setattr" in line


def _rules_for_line(line: str) -> list[str]:
    if _line_has_mock_clock_patch(line):
        return []
    rules: list[str] = []
    if _RE_TIME_SLEEP.search(line):
        rules.append("time.sleep")
    m = _RE_ASYNCIO_SLEEP.search(line)
    if m and _literal_is_positive(m.group(1)):
        rules.append("asyncio.sleep")
    m = _RE_TIMEOUT_CALL.search(line)
    if m and _literal_is_positive(m.group(1)):
        rules.append("blocking-timeout")
    if _RE_TIME_CLOCK.search(line):
        rules.append("time-clock")
    if _RE_PERF_COUNTER.search(line):
        rules.append("perf_counter")
    if _RE_DATETIME_NOW.search(line):
        rules.append("datetime.now")
    return rules


def _find_wallclock_hits(root: Path) -> dict[str, tuple[str, ...]]:
    hits: dict[str, tuple[str, ...]] = {}
    tests_root = root / "tests"
    if not tests_root.is_dir():
        return hits
    for path in sorted(tests_root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if rel.startswith(_CONVENTIONS_PREFIX):
            continue
        diagnostics: list[str] = []
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            for rule in _rules_for_line(line):
                diagnostics.append(f"{rel}:{lineno}: {rule}")
        if diagnostics:
            hits[rel] = tuple(diagnostics)
    return hits


def _read_ledger(path: Path) -> set[str]:
    rows: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        rows.add(line)
    return rows


def test_wallclock_detector_reports_forbidden_calls(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    sample = root / "tests" / "sample_wallclock.py"
    sample.parent.mkdir(parents=True)
    sample.write_text(
        "import asyncio\n"
        "import subprocess\n"
        "import threading\n"
        "import time\n"
        "from datetime import datetime\n"
        "from time import perf_counter\n\n"
        "def test_forbidden():\n"
        "    time.sleep(0.1)\n"
        "    asyncio.sleep(1)\n"
        "    subprocess.Popen(['true']).communicate(timeout=5)\n"
        "    t = threading.Thread(target=lambda: None)\n"
        "    t.start()\n"
        "    t.join(timeout=2)\n"
        "    time.monotonic()\n"
        "    time.perf_counter()\n"
        "    perf_counter()\n"
        "    time.time()\n"
        "    datetime.now()\n"
        "    datetime.utcnow()\n",
        encoding="utf-8",
    )
    rel = "tests/sample_wallclock.py"
    found = _find_wallclock_hits(root)
    assert rel in found
    diags = found[rel]
    assert f"{rel}:9: time.sleep" in diags
    assert f"{rel}:10: asyncio.sleep" in diags
    assert f"{rel}:11: blocking-timeout" in diags
    assert f"{rel}:14: blocking-timeout" in diags
    assert f"{rel}:15: time-clock" in diags
    assert f"{rel}:16: time-clock" in diags
    assert f"{rel}:17: perf_counter" in diags
    assert f"{rel}:18: time-clock" in diags
    assert f"{rel}:19: datetime.now" in diags
    assert f"{rel}:20: datetime.now" in diags


def test_wallclock_detector_ignores_same_line_clock_replacement(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    sample = root / "tests" / "patched.py"
    sample.parent.mkdir(parents=True)
    patch = "monkeypatch" + ".setattr"
    sample.write_text(
        "import asyncio\n"
        "import time\n\n"
        f"def test_ok({patch.split('.')[0]}):\n"
        "    asyncio.sleep(0)\n"
        f"    {patch}(time, 'sleep', lambda *a, **k: None)\n"
        f"    {patch}(time, 'monotonic', lambda: 0.0)\n",
        encoding="utf-8",
    )
    rel = "tests/patched.py"
    assert _find_wallclock_hits(root).get(rel) is None


def test_no_unregistered_wallclock_files(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    offender = root / "tests" / "test_new.py"
    offender.parent.mkdir(parents=True)
    offender.write_text("import time\n\ndef test_x():\n    time.sleep(0.1)\n", encoding="utf-8")
    ledger = root / "tests" / "conventions" / "known_wallclock_tests.txt"
    ledger.parent.mkdir(parents=True)
    ledger.write_text("# empty ledger\n", encoding="utf-8")

    actual = set(_find_wallclock_hits(root))
    known = _read_ledger(ledger)
    unlisted = sorted(actual - known)
    assert unlisted == ["tests/test_new.py"]

    measured = set(_find_wallclock_hits(REPO_ROOT))
    ledger_paths = _read_ledger(_LEDGER_FILE)
    repo_unlisted = sorted(measured - ledger_paths)
    assert not repo_unlisted, (
        "Unregistered wall-clock test files (fix tests; do not grow the ledger):\n"
        + "\n".join(f"  {p}" for p in repo_unlisted)
    )


def test_wallclock_ledger_has_no_stale_paths(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    clean = root / "tests" / "test_clean.py"
    clean.parent.mkdir(parents=True)
    clean.write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    rel = "tests/test_clean.py"
    ledger = root / "tests" / "conventions" / "known_wallclock_tests.txt"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(f"{rel}\n", encoding="utf-8")

    actual = set(_find_wallclock_hits(root))
    known = _read_ledger(ledger)
    stale = sorted(known - actual)
    assert stale == [rel]

    measured = set(_find_wallclock_hits(REPO_ROOT))
    ledger_paths = _read_ledger(_LEDGER_FILE)
    repo_stale = sorted(ledger_paths - measured)
    assert not repo_stale, (
        "Stale paths in known_wallclock_tests.txt (remove after fixing tests):\n"
        + "\n".join(f"  {p}" for p in repo_stale)
    )


def test_pytest_suite_timeout_is_configured() -> None:
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dev = data["project"]["optional-dependencies"]["dev"]
    assert any(str(dep).startswith("pytest-timeout") for dep in dev)
    ini = data["tool"]["pytest"]["ini_options"]
    timeout = ini.get("timeout")
    assert isinstance(timeout, int) and timeout == 300, f"expected integer suite timeout 300, got {timeout!r}"
