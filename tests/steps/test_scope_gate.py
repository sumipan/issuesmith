"""Unit tests for issuesmith.scope_gate (#3349 / #4276)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from issuesmith.config import ScopeGateConfig
from issuesmith.scope_gate import (
    ScopeMeasure,
    ScopeVerdict,
    evaluate,
    format_comment,
    measure_scope,
)


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

    measure = measure_scope(
        repo,
        ["tests/**", "src/x/*.py", "README.md", "blob.bin"],
    )
    assert measure.files == 6
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

    measure = measure_scope(repo, ["tests/**"])
    assert measure.files == 1
    assert measure.lines == 1
    assert "workflows/" not in measure.by_dir


def test_evaluate_exceeds_on_files_or_lines() -> None:
    measure = ScopeMeasure(
        files=81,
        lines=100,
        by_dir={"tests/": 81},
        skipped_binary=0,
        skipped_jsonl=0,
    )
    verdict = evaluate(measure, ScopeGateConfig())
    assert verdict.exceeded is True
    assert "files: 81 > 80" in verdict.reason

    measure_lines = ScopeMeasure(
        files=10,
        lines=20_001,
        by_dir={"src/": 10},
        skipped_binary=0,
        skipped_jsonl=0,
    )
    verdict2 = evaluate(measure_lines, ScopeGateConfig())
    assert verdict2.exceeded is True
    assert "lines: 20001 > 20000" in verdict2.reason


def test_evaluate_override_raises_threshold() -> None:
    measure = ScopeMeasure(
        files=100,
        lines=100,
        by_dir={"tests/": 100},
        skipped_binary=0,
        skipped_jsonl=0,
    )
    base = ScopeGateConfig()
    override = ScopeGateConfig(max_files=120, max_lines=base.max_lines)
    verdict = evaluate(measure, base, override=override)
    assert verdict.exceeded is False
    assert verdict.reason == ""


def test_evaluate_within_defaults() -> None:
    measure = ScopeMeasure(
        files=80,
        lines=20_000,
        by_dir={"src/": 80},
        skipped_binary=0,
        skipped_jsonl=0,
    )
    assert evaluate(measure, ScopeGateConfig()).exceeded is False


def test_format_comment_includes_table_and_split_hint() -> None:
    measure = ScopeMeasure(
        files=134,
        lines=5000,
        by_dir={"tests/": 125, "docs/": 5, "workflows/": 4},
        skipped_binary=0,
        skipped_jsonl=9,
    )
    verdict = ScopeVerdict(
        exceeded=True,
        reason="files: 134 > 80",
        measure=measure,
    )
    text = format_comment(verdict)
    assert "134" in text
    assert "5000" in text
    assert "files: 134 > 80" in text
    assert "tests/" in text
    assert "allow_paths" in text


def test_ac4_issue_3339_regression_134_files_exceeds(tmp_path: Path) -> None:
    repo = tmp_path / "mltgnt-like"
    _git_init(repo)
    (repo / "tests" / "fixtures").mkdir(parents=True)

    for i in range(125):
        (repo / "tests" / f"test_{i:03d}.py").write_text(f"# t{i}\n", encoding="utf-8")
    for i in range(9):
        (repo / "tests" / "fixtures" / f"f{i}.jsonl").write_text("{}\n", encoding="utf-8")
    _commit_all(repo)

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
    measure = measure_scope(repo, allow_paths)
    assert measure.files == 134
    assert measure.skipped_jsonl == 9
    verdict = evaluate(measure, ScopeGateConfig())
    assert verdict.exceeded is True
    assert verdict.reason == "files: 134 > 80"


def _verdict() -> ScopeVerdict:
    measure = ScopeMeasure(
        files=134, lines=5000, by_dir={"tests/": 125}, skipped_binary=1, skipped_jsonl=9
    )
    return ScopeVerdict(exceeded=True, reason="files: 134 > 80", measure=measure)


def test_format_comment_uses_language_pack(tmp_path: Path, monkeypatch) -> None:
    from tests.test_queue_language_pack import install_custom_pack

    pack = install_custom_pack(tmp_path, monkeypatch)
    text = format_comment(_verdict(), preflight_contradiction=True)

    expected = pack.message(
        "scope_gate.too_large",
        contradiction_note=pack.message("scope_gate.preflight_contradiction"),
        reason="files: 134 > 80",
        files=134,
        lines=5000,
        skipped_binary=1,
        skipped_jsonl=9,
        rows="| `tests/` | 125 |",
    )
    assert text == f"{expected}\nPIPELINE_STATUS: SCOPE_TOO_LARGE\n"
    assert text.startswith("[custom-pack]")


def test_format_comment_en_default_keeps_marker_and_no_rows() -> None:
    measure = ScopeMeasure(files=0, lines=0, by_dir={}, skipped_binary=0, skipped_jsonl=0)
    text = format_comment(ScopeVerdict(exceeded=True, reason="r", measure=measure))
    assert text.startswith("## P0 stopped: allow_paths scope is too large\n")
    assert "| (none) | 0 |" in text
    assert "pre-gate" not in text
    assert text.endswith("\n\nPIPELINE_STATUS: SCOPE_TOO_LARGE\n")


def test_parse_allow_paths_treats_fullwidth_placeholder_as_unrestricted() -> None:
    from issuesmith.scope_gate import parse_allow_paths_from_ctx

    # context_hook's "no restriction" placeholder: a word in full-width parentheses.
    placeholder = chr(0xFF08) + "none" + chr(0xFF09)
    assert parse_allow_paths_from_ctx(placeholder) == []
    assert parse_allow_paths_from_ctx(f"  {placeholder}\n") == []
    assert parse_allow_paths_from_ctx("") == []
    assert parse_allow_paths_from_ctx("- src/**\n- tests/**") == ["src/**", "tests/**"]
