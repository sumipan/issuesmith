"""PrScopeGate untracked forbidden paths (#6975)."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from issuesmith.config import reset_config_cache
from issuesmith.gates.base import ContractInput
from issuesmith.gates.pr_scope import PrScopeGate
from tests.test_derived_allow_paths import _commit_all, _make_repo, _write


def _lock_forbidden_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "issuesmith.yaml"
    cfg.write_text(
        yaml.safe_dump({
            "repo": "example/repo",
            "forbidden_pr_paths": ["*.lock"],
            "derived_allow": {"enabled": False},
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg))
    reset_config_cache()


def test_fix_deletes_untracked_forbidden_file_and_keeps_other_untracked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Untracked forbidden lock is auto-fixable; other untracked out-of-scope files stay. Ref #6975."""
    _lock_forbidden_config(tmp_path, monkeypatch)
    clone = _make_repo(tmp_path, {"src/allowed.py": "x = 1\n"})
    _write(clone, {"src/allowed.py": "x = 2\n"})
    _commit_all(clone, "change allowed")
    _write(clone, {"quota-gate.json.lock": "", "notes.txt": "notes\n"})

    gate = PrScopeGate(clone, ["src/allowed.py"], "main")
    before = gate.check("", [])
    lock_v = [v for v in before if v.location == "quota-gate.json.lock"]
    assert len(lock_v) == 1
    assert lock_v[0].rule_id == "pr_scope.untracked_forbidden"
    assert lock_v[0].auto_fixable is True
    assert lock_v[0].fix_hint == "rm -- quota-gate.json.lock"
    assert lock_v[0].message.endswith("(untracked; removed by pr_scope fix)")

    gate.fix(ContractInput(body=""))
    assert not (clone / "quota-gate.json.lock").exists()
    assert (clone / "notes.txt").is_file()

    after = gate.check("", [])
    assert not any(v.rule_id == "pr_scope.untracked_forbidden" for v in after)
    notes_v = [v for v in after if v.location == "notes.txt"]
    assert len(notes_v) == 1
    assert notes_v[0].rule_id == "pr_scope.out_of_allow"


def test_committed_forbidden_file_stays_non_repairable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Committed forbidden files keep pr_diff_scope.forbidden_path; fix does not unlink them. Ref #6975."""
    _lock_forbidden_config(tmp_path, monkeypatch)
    clone = _make_repo(tmp_path, {
        "src/allowed.py": "x = 1\n",
        "state.lock": "locked\n",
    })
    _write(clone, {"src/allowed.py": "x = 2\n", "state.lock": "changed\n"})
    _commit_all(clone, "change both")

    gate = PrScopeGate(clone, ["src/allowed.py", "state.lock"], "main")
    violations = gate.check("", [])
    lock_v = [v for v in violations if v.location == "state.lock"]
    assert len(lock_v) == 1
    assert lock_v[0].rule_id == "pr_diff_scope.forbidden_path"

    gate.fix(ContractInput(body=""))
    assert (clone / "state.lock").is_file()
