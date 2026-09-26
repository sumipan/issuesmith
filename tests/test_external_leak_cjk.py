"""Tests for the external_leak gate's CJK added-line check (nexus #3909)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from issuesmith.config import ExternalLeakConfig, get_config, reset_config_cache
from issuesmith.gates.worktree import (
    ExternalLeakGate,
    cjk_added_lines,
    external_target_state,
    is_external_target,
    line_has_cjk,
)

# ASCII-only fixtures: CJK is produced from code points at runtime.
_CJK_WORD = chr(0x65E5) + chr(0x672C)
_FULLWIDTH_PAREN = chr(0xFF08) + "#1" + chr(0xFF09)
_HANGUL = chr(0xD55C)
_BACKSLASH = chr(92)


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(root), capture_output=True, check=True)


def _write_config(tmp_path: Path, monkeypatch, extra: dict | None = None) -> None:
    data: dict = {"repo": "sumipan/host"}
    data.update(extra or {})
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()


@pytest.fixture()
def repo(tmp_path: Path):
    """Clone of a bare-ish origin whose main already contains one CJK line."""
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-b", "main")
    _git(origin, "config", "user.email", "test@test.com")
    _git(origin, "config", "user.name", "Test")
    (origin / "old.py").write_text(f"# {_CJK_WORD} existing\n", encoding="utf-8")
    (origin / "keep.md").write_text("hello\n", encoding="utf-8")
    _git(origin, "add", "old.py", "keep.md")
    _git(origin, "commit", "-m", "init")
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", str(origin), str(clone)], capture_output=True, check=True)
    _git(clone, "config", "user.email", "test@test.com")
    _git(clone, "config", "user.name", "Test")
    _git(clone, "checkout", "-b", "feat")
    return clone


def _commit(repo_path: Path, name: str, text: str) -> None:
    (repo_path / name).write_text(text, encoding="utf-8")
    _git(repo_path, "add", name)
    _git(repo_path, "commit", "-m", f"add {name}")


# --- line_has_cjk -----------------------------------------------------------


def test_line_has_cjk_literal_fullwidth_and_hangul() -> None:
    assert line_has_cjk("x = 1  # " + _CJK_WORD)
    assert line_has_cjk("see " + _FULLWIDTH_PAREN)
    assert line_has_cjk(_HANGUL)


def test_line_has_cjk_decodes_unicode_escapes() -> None:
    assert line_has_cjk("s = '" + _BACKSLASH + "u65e5'")
    assert line_has_cjk("s = '" + _BACKSLASH + "U0000672C'")


def test_line_has_cjk_accepts_ascii_and_latin_escapes() -> None:
    assert not line_has_cjk("plain ascii (#3909)")
    assert not line_has_cjk("s = '" + _BACKSLASH + "u00e9'")
    assert not line_has_cjk("")


# --- cjk_added_lines --------------------------------------------------------


def test_added_lines_only_reports_new_cjk(repo: Path) -> None:
    _commit(repo, "new.py", "x = 1\n# " + _CJK_WORD + "\ny = 2\n")
    hits = cjk_added_lines(repo, "main")
    assert hits == [("new.py", 2, "# " + _CJK_WORD)]


def test_existing_cjk_on_base_is_ignored(repo: Path) -> None:
    _commit(repo, "keep.md", "hello\nworld\n")
    assert cjk_added_lines(repo, "main") == []


def test_removed_cjk_is_not_reported(repo: Path) -> None:
    _commit(repo, "old.py", "# english now\n")
    assert cjk_added_lines(repo, "main") == []


def test_escaped_cjk_in_added_line_is_reported(repo: Path) -> None:
    _commit(repo, "esc.py", "WORD = '" + _BACKSLASH + "u65e5'\n")
    assert [h[:2] for h in cjk_added_lines(repo, "main")] == [("esc.py", 1)]


def test_git_failure_raises(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError):
        cjk_added_lines(tmp_path, "main")


# --- is_external_target -----------------------------------------------------


def test_is_external_target_compares_with_host_repo(tmp_path: Path, monkeypatch) -> None:
    _write_config(tmp_path, monkeypatch)
    assert is_external_target("```yaml\ntarget_repo: sumipan/other\n```\n")
    assert not is_external_target("```yaml\ntarget_repo: sumipan/host\n```\n")
    assert not is_external_target("```yaml\nbase_branch: main\n```\n")
    assert not is_external_target("no metadata")


# --- ExternalLeakGate -------------------------------------------------------

_EXTERNAL_BODY = "```yaml\ntarget_repo: sumipan/other\nbase_branch: main\n```\n\n## Overview\n"
_HOST_BODY = "```yaml\ntarget_repo: sumipan/host\nbase_branch: main\n```\n\n## Overview\n"


def test_gate_flags_cjk_for_external_target_when_enabled(repo: Path, tmp_path: Path, monkeypatch) -> None:
    _write_config(tmp_path, monkeypatch, {"external_leak": {"cjk_free_external_targets": True}})
    _commit(repo, "a.py", "# " + _CJK_WORD + "\nx = 1\n# " + _CJK_WORD + "\n")
    _commit(repo, "b.md", "ok\n" + _FULLWIDTH_PAREN + "\n")
    violations = ExternalLeakGate(repo, ["a.py", "b.md"], "main").check(_EXTERNAL_BODY, [])
    by_file = {v.location: v for v in violations}
    assert set(by_file) == {"a.py", "b.md"}
    assert all(v.rule_id == "external_leak.cjk_added_line" for v in violations)
    assert all(v.severity == "fail" and v.auto_fixable is False for v in violations)
    assert "1, 3" in by_file["a.py"].message
    assert "English" in by_file["a.py"].fix_hint


def test_gate_ignores_host_target_and_disabled_config(repo: Path, tmp_path: Path, monkeypatch) -> None:
    _commit(repo, "a.py", "# " + _CJK_WORD + "\n")
    _write_config(tmp_path, monkeypatch, {"external_leak": {"cjk_free_external_targets": True}})
    assert ExternalLeakGate(repo, ["a.py"], "main").check(_HOST_BODY, []) == []
    _write_config(tmp_path, monkeypatch)
    assert get_config().external_leak == ExternalLeakConfig()
    assert ExternalLeakGate(repo, ["a.py"], "main").check(_EXTERNAL_BODY, []) == []


def test_gate_passes_ascii_external_branch(repo: Path, tmp_path: Path, monkeypatch) -> None:
    _write_config(tmp_path, monkeypatch, {"external_leak": {"cjk_free_external_targets": True}})
    _commit(repo, "a.py", "# english only\n")
    assert ExternalLeakGate(repo, ["a.py"], "main").check(_EXTERNAL_BODY, []) == []


# --- config -----------------------------------------------------------------


def test_config_rejects_unknown_external_leak_keys(tmp_path: Path, monkeypatch) -> None:
    from issuesmith.config import ConfigError

    _write_config(tmp_path, monkeypatch, {"external_leak": {"bogus": True}})
    with pytest.raises(ConfigError):
        get_config()


def test_config_parses_external_leak(tmp_path: Path, monkeypatch) -> None:
    _write_config(tmp_path, monkeypatch, {"external_leak": {"cjk_free_external_targets": "yes"}})
    assert get_config().external_leak.cjk_free_external_targets is True


# --- external_target_state / fail-closed CJK check (nexus #4116) -------------

_ENABLED = {"external_leak": {"cjk_free_external_targets": True}}


def test_external_target_state_classifies_bodies(tmp_path: Path, monkeypatch) -> None:
    _write_config(tmp_path, monkeypatch)
    assert external_target_state("") == "unknown"
    assert external_target_state("   \n\t") == "unknown"
    assert external_target_state("no metadata") == "unknown"
    assert external_target_state("```yaml\nbase_branch: main\n```\n") == "unknown"
    assert external_target_state("```yaml\ntarget_repo: ''\n```\n") == "unknown"
    assert external_target_state(_HOST_BODY) == "host"
    assert external_target_state(_EXTERNAL_BODY) == "external"


def _assert_target_unknown(violations: list) -> None:
    assert len(violations) == 1
    v = violations[0]
    assert v.rule_id == "external_leak.target_unknown"
    assert v.severity == "fail"
    assert v.auto_fixable is False
    assert v.location == ""


@pytest.mark.parametrize(
    "body",
    [
        "",
        "```yaml\ntarget_repo: [unclosed\n```\n",
        "```yaml\nbase_branch: main\n```\n",
    ],
    ids=["empty", "broken_yaml", "no_target_repo"],
)
def test_cjk_check_is_fail_closed_when_target_unknown(
    repo: Path, tmp_path: Path, monkeypatch, capsys, body: str
) -> None:
    _write_config(tmp_path, monkeypatch, _ENABLED)
    _commit(repo, "a.py", "# " + _CJK_WORD + "\n")
    _assert_target_unknown(ExternalLeakGate(repo, ["a.py"], "main")._cjk_violations(body))
    assert "[external_leak] cjk: skipped (reason=target_unknown)" in capsys.readouterr().err


def test_cjk_check_skips_host_target_with_log(repo: Path, tmp_path: Path, monkeypatch, capsys) -> None:
    _write_config(tmp_path, monkeypatch, _ENABLED)
    _commit(repo, "a.py", "# " + _CJK_WORD + "\n")
    assert ExternalLeakGate(repo, ["a.py"], "main")._cjk_violations(_HOST_BODY) == []
    assert "[external_leak] cjk: skipped (reason=host_target)" in capsys.readouterr().err


def test_cjk_check_skips_when_disabled_with_log(repo: Path, tmp_path: Path, monkeypatch, capsys) -> None:
    _write_config(tmp_path, monkeypatch)
    assert ExternalLeakGate(repo, ["a.py"], "main")._cjk_violations("") == []
    assert "[external_leak] cjk: skipped (reason=disabled)" in capsys.readouterr().err


def test_cjk_check_logs_counts_for_external_target(repo: Path, tmp_path: Path, monkeypatch, capsys) -> None:
    _write_config(tmp_path, monkeypatch, _ENABLED)
    _commit(repo, "a.py", "# " + _CJK_WORD + "\nx = 1\n# " + _CJK_WORD + "\n")
    _commit(repo, "b.md", "ok\n" + _FULLWIDTH_PAREN + "\n")
    violations = ExternalLeakGate(repo, ["a.py", "b.md"], "main")._cjk_violations(_EXTERNAL_BODY)
    assert {v.location for v in violations} == {"a.py", "b.md"}
    assert all(v.rule_id == "external_leak.cjk_added_line" for v in violations)
    err = capsys.readouterr().err
    assert "[external_leak] cjk: checked (target=sumipan/other), 3 CJK line(s) in 2 file(s)" in err


def test_cjk_check_logs_zero_counts_for_clean_external_branch(
    repo: Path, tmp_path: Path, monkeypatch, capsys
) -> None:
    _write_config(tmp_path, monkeypatch, _ENABLED)
    _commit(repo, "a.py", "# english only\n")
    assert ExternalLeakGate(repo, ["a.py"], "main")._cjk_violations(_EXTERNAL_BODY) == []
    err = capsys.readouterr().err
    assert "[external_leak] cjk: checked (target=sumipan/other), 0 CJK line(s) in 0 file(s)" in err
