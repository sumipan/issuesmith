"""AC-1 / AC-2: write-guard harness smoke tests."""
from __future__ import annotations

import os
from pathlib import Path

import pytest


def test_write_text_outside_tmp_is_blocked():
    with pytest.raises(AssertionError, match="side effect outside tmp"):
        Path("jobs/x.jsonl").write_text("x", encoding="utf-8")


def test_os_makedirs_outside_tmp_is_blocked():
    with pytest.raises(AssertionError, match="side effect outside tmp"):
        os.makedirs("logs")


def test_open_write_mode_outside_tmp_is_blocked():
    with pytest.raises(AssertionError, match="side effect outside tmp"):
        open("jobs/y", "w")  # noqa: SIM115


def test_write_text_inside_tmp_succeeds(tmp_path):
    (tmp_path / "x.txt").write_text("ok", encoding="utf-8")
    assert (tmp_path / "x.txt").read_text(encoding="utf-8") == "ok"


def test_open_write_mode_inside_tmp_succeeds(tmp_path):
    with open(tmp_path / "y.txt", "w", encoding="utf-8") as fh:
        fh.write("ok")
    assert (tmp_path / "y.txt").read_text(encoding="utf-8") == "ok"


def test_os_makedirs_inside_tmp_succeeds(tmp_path):
    os.makedirs(str(tmp_path / "subdir"), exist_ok=True)
    assert (tmp_path / "subdir").is_dir()


def test_no_runtime_dirs_at_repo_root():
    repo_root = Path(__file__).parent.parent
    for d in ("jobs", "logs", ".pipeline-state"):
        assert not (repo_root / d).exists(), (
            f"side effect: {d}/ exists at repo root — a test created it outside tmp"
        )
