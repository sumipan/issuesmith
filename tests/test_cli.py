"""tests for unified issuesmith CLI, packaging, and aliases (#2827 / #2821)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = REPO_ROOT / "src"


def _pythonpath() -> str:
    existing = os.environ.get("PYTHONPATH", "")
    return str(SRC_PATH) + (f":{existing}" if existing else "")


def _run_module(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "issuesmith", *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=check,
        env={**os.environ, "PYTHONPATH": _pythonpath()},
    )


def test_wheel_contains_cli_and_entry_point(tmp_path: Path) -> None:
    out = tmp_path / "dist"
    out.mkdir()
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            str(REPO_ROOT),
            "-w",
            str(out),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    wheels = list(out.glob("issuesmith-*.whl"))
    assert len(wheels) == 1, wheels
    with zipfile.ZipFile(wheels[0]) as zf:
        names = zf.namelist()
        assert any(n.endswith("issuesmith/cli.py") for n in names), names
        entry = next(n for n in names if n.endswith("entry_points.txt"))
        text = zf.read(entry).decode("utf-8")
        assert "issuesmith" in text
        assert "issuesmith.cli:main" in text


def test_pyproject_uses_src_layout() -> None:
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover
        import tomli as tomllib  # type: ignore

    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    find = data["tool"]["setuptools"]["packages"]["find"]
    assert find["where"] == ["src"]
    assert data["project"]["name"] == "issuesmith"
    assert data["project"]["version"] == "0.3.0"


def test_gate_alias_matches_gate_preflight(tmp_path: Path) -> None:
    body = tmp_path / "body.md"
    body.write_text("## 受け入れ条件\n\n```yaml\npaths_must_exist: []\n```\n", encoding="utf-8")

    old = _run_module("gate-preflight", "--gate", "cp1", "--body-file", str(body))
    new = _run_module("gate", "cp1", "--body-file", str(body))
    assert old.returncode == 0, old.stderr
    assert new.returncode == 0, new.stderr
    assert json.loads(old.stdout) == json.loads(new.stdout)


def test_engine_show_runs() -> None:
    via_cli = _run_module("engine", "show")
    assert via_cli.returncode == 0, via_cli.stderr
    assert via_cli.stdout.strip()


def test_apply_moved_exits_2() -> None:
    proc = _run_module("apply", "stash/x.md", check=False)
    assert proc.returncode == 2
    assert "tools/stash/" in proc.stderr


def test_no_sys_path_in_package() -> None:
    pkg = SRC_PATH / "issuesmith"
    hits: list[str] = []
    for path in pkg.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            if "sys.path" in line:
                hits.append(f"{path.relative_to(REPO_ROOT)}:{i}:{line.strip()}")
    assert hits == []
