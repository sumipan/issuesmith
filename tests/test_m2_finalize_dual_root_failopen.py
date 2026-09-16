"""tests/test_m2_finalize_dual_root_failopen.py — _evaluate_dual_root は契約実行エラーで落ちない（#3290）。"""

from __future__ import annotations

from pathlib import Path

from issuesmith.steps import m2_finalize


def test_evaluate_dual_root_fail_open_on_run_checks_error(monkeypatch, tmp_path: Path, capsys):
    base = {"action": "proceed", "contract_failures": []}
    monkeypatch.setattr(m2_finalize, "check_gate", lambda body, labels, repo_root=None: dict(base))
    monkeypatch.setattr(
        m2_finalize, "extract_contract_from_body", lambda body: {"references_must_resolve": ["x"]}
    )

    def _boom(contract, repo_root, **_kw):
        raise TypeError("string indices must be integers, not 'str'")

    monkeypatch.setattr(m2_finalize, "run_checks", _boom)

    result = m2_finalize._evaluate_dual_root("body", [], tmp_path, tmp_path)

    assert result == base
    assert "fail-open" in capsys.readouterr().err
