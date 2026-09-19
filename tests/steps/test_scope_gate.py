"""Unit tests for issuesmith.steps.scope_gate (#3349)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from issuesmith.config import ScopeGateConfig
from issuesmith.steps import scope_gate as sg


def _git_init(repo: Path) -> None:
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "t@t"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "t"],
        check=True,
        capture_output=True,
    )


def _commit_all(repo: Path, message: str = "init") -> None:
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", message],
        check=True,
        capture_output=True,
    )


def test_measure_scope_globs_counts_and_excludes_jsonl_binary(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _git_init(repo)
    (repo / "tests").mkdir()
    (repo / "tests" / "fixtures").mkdir(parents=True)
    (repo / "src" / "x").mkdir(parents=True)
    (repo / "tests" / "a.py").write_text("one\ntwo\n", encoding="utf-8")
    (repo / "tests" / "b.py").write_text("three\n", encoding="utf-8")
    (repo / "tests" / "fixtures" / "data.jsonl").write_text('{"a":1}\n', encoding="utf-8")
    (repo / "src" / "x" / "m.py").write_text("x\n", encoding="utf-8")
    (repo / "src" / "x" / "n.txt").write_text("y\n", encoding="utf-8")
    (repo / "README.md").write_text("readme\n", encoding="utf-8")
    (repo / "blob.bin").write_bytes(b"\x00\x01\x02\x03")
    _commit_all(repo)

    measure = sg.measure_scope(
        repo,
        ["tests/**", "src/x/*.py", "README.md", "blob.bin"],
    )
    # tests/a.py, tests/b.py, tests/fixtures/data.jsonl, src/x/m.py, README.md, blob.bin
    assert measure.files == 6
    # lines: a(2)+b(1)+m(1)+README(1) = 5; jsonl and binary excluded from lines
    assert measure.lines == 5
    assert measure.skipped_jsonl == 1
    assert measure.skipped_binary == 1
    assert measure.by_dir.get("tests/") == 3
    assert list(measure.by_dir.keys()) == list(measure.by_dir.keys())[:5]


def test_measure_scope_ignores_unmatched_and_diary_paths(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _git_init(repo)
    (repo / "tests").mkdir()
    (repo / "workflows").mkdir()
    (repo / "tests" / "a.py").write_text("a\n", encoding="utf-8")
    (repo / "workflows" / "x.yml").write_text("x\n", encoding="utf-8")
    _commit_all(repo)

    measure = sg.measure_scope(repo, ["tests/**"])
    assert measure.files == 1
    assert measure.lines == 1
    assert "workflows/" not in measure.by_dir


def test_evaluate_exceeds_on_files_or_lines() -> None:
    measure = sg.ScopeMeasure(
        files=81,
        lines=100,
        by_dir={"tests/": 81},
        skipped_binary=0,
        skipped_jsonl=0,
    )
    verdict = sg.evaluate(measure, ScopeGateConfig())
    assert verdict.exceeded is True
    assert "files: 81 > 80" in verdict.reason

    measure_lines = sg.ScopeMeasure(
        files=10,
        lines=20_001,
        by_dir={"src/": 10},
        skipped_binary=0,
        skipped_jsonl=0,
    )
    verdict2 = sg.evaluate(measure_lines, ScopeGateConfig())
    assert verdict2.exceeded is True
    assert "lines: 20001 > 20000" in verdict2.reason


def test_evaluate_override_raises_threshold() -> None:
    measure = sg.ScopeMeasure(
        files=100,
        lines=100,
        by_dir={"tests/": 100},
        skipped_binary=0,
        skipped_jsonl=0,
    )
    base = ScopeGateConfig()
    override = ScopeGateConfig(max_files=120, max_lines=base.max_lines)
    verdict = sg.evaluate(measure, base, override=override)
    assert verdict.exceeded is False
    assert verdict.reason == ""


def test_evaluate_within_defaults() -> None:
    measure = sg.ScopeMeasure(
        files=80,
        lines=20_000,
        by_dir={"src/": 80},
        skipped_binary=0,
        skipped_jsonl=0,
    )
    assert sg.evaluate(measure, ScopeGateConfig()).exceeded is False


def test_format_comment_includes_table_and_split_hint() -> None:
    measure = sg.ScopeMeasure(
        files=134,
        lines=5000,
        by_dir={"tests/": 125, "docs/": 5, "workflows/": 4},
        skipped_binary=0,
        skipped_jsonl=9,
    )
    verdict = sg.ScopeVerdict(
        exceeded=True,
        reason="files: 134 > 80",
        measure=measure,
    )
    text = sg.format_comment(verdict)
    assert "134" in text
    assert "5000" in text
    assert "files: 134 > 80" in text
    assert "tests/" in text
    assert "allow_paths" in text


def test_ac4_issue_3339_regression_134_files_exceeds(tmp_path: Path) -> None:
    """#3339-sized scope (125 tests + 9 fixtures = 134) exceeds default max_files=80."""
    repo = tmp_path / "mltgnt-like"
    _git_init(repo)
    (repo / "tests" / "fixtures").mkdir(parents=True)

    # Frozen regression size from #3348 / #3339 analysis.
    for i in range(125):
        (repo / "tests" / f"test_{i:03d}.py").write_text(f"# t{i}\n", encoding="utf-8")
    for i in range(9):
        (repo / "tests" / "fixtures" / f"f{i}.jsonl").write_text("{}\n", encoding="utf-8")
    _commit_all(repo)

    # #3339 allow_paths (other patterns match nothing in this fixture).
    allow_paths = [
        "AGENTS.md",
        "CLAUDE.md",
        "scripts/check-external-leak.py",
        "workflows/issuesmith.yml",
        "workflows/issuesmith/**",
        "tests/**",
        "docs/GHDAG-MLTGNT-NEXUS.md",
        "docs/OSS_QUALITY.md",
    ]
    measure = sg.measure_scope(repo, allow_paths)
    assert measure.files == 134
    assert measure.skipped_jsonl == 9
    verdict = sg.evaluate(measure, ScopeGateConfig())
    assert verdict.exceeded is True
    assert verdict.reason == "files: 134 > 80"
