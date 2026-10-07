"""Develop-phase pins_landed precondition tests."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import yaml

from issuesmith.config import load_config, reset_config_cache
from issuesmith.queue import phase_preconditions


@pytest.fixture(autouse=True)
def _clear_cache():
    reset_config_cache()
    yield
    reset_config_cache()


def _body(**extra):
    meta = {
        "target_repo": "sumipan/nexus",
        "base_branch": "main",
        "allow_paths": ["src/**"],
    }
    meta.update(extra)
    return "```yaml\n" + yaml.safe_dump(meta) + "```\n\n## Design\n\nwork\n"


def _develop_issue(body: str) -> dict:
    return {
        "state": "OPEN",
        "labels": [
            {"name": "issuesmith:draft-done"},
        ],
        "body": body,
        "title": "t",
    }


def test_develop_without_requires_pins_unchanged(tmp_path, monkeypatch):
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(yaml.safe_dump({"repo": "sumipan/nexus"}), encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    load_config()
    ok, why = phase_preconditions("develop", _develop_issue(_body()), MagicMock(), 1)
    assert ok is True
    assert why == "ok"


def test_draft_ignores_unlanded_pins(tmp_path, monkeypatch):
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(yaml.safe_dump({"repo": "sumipan/nexus"}), encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    load_config()
    body = _body(requires_pins={"issuesmith": "v0.133.1"})
    issue = {"state": "OPEN", "labels": [], "body": body, "title": "t"}
    ok, why = phase_preconditions("draft", issue, MagicMock(), 1)
    assert ok is True
    assert why == "ok"


def test_develop_blocks_unlanded_pins(tmp_path, monkeypatch):
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump(
            {
                "repo": "sumipan/nexus",
                "supported_repos": ["sumipan/nexus"],
                "installs": {"issuesmith": "/var/tmp/issuesmith"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    cfg = load_config()
    install_dir = str(cfg.installs["issuesmith"])

    pyproject = (
        'issuesmith = "issuesmith @ git+https://github.com/sumipan/issuesmith.git@v0.133.0"\n'
    )

    def fake_run(cmd, **kwargs):
        if len(cmd) >= 5 and cmd[0:2] == ["git", "-C"] and cmd[2] == str(tmp_path) and cmd[3] == "show":
            return MagicMock(returncode=0, stdout=pyproject)
        if len(cmd) >= 3 and cmd[0:2] == ["git", "-C"] and cmd[2] == install_dir:
            return MagicMock(returncode=0, stdout="v0.133.0\n")
        return MagicMock(returncode=1, stdout="")

    monkeypatch.setattr("issuesmith.preconditions.subprocess.run", fake_run)
    monkeypatch.setattr("issuesmith.pins.subprocess.run", fake_run)
    monkeypatch.setattr(
        "issuesmith.scope_gate.resolve_scope_root",
        lambda metadata, cfg: tmp_path,
    )

    body = _body(requires_pins={"issuesmith": "v0.133.1"})
    ok, why = phase_preconditions("develop", _develop_issue(body), MagicMock(), 1)
    assert ok is False
    assert why.startswith("dependencies not satisfied: pin issuesmith v0.133.1 not landed")
    assert "base=v0.133.0" in why
    assert "installed=v0.133.0" in why


def test_develop_passes_when_pins_landed(tmp_path, monkeypatch):
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump(
            {
                "repo": "sumipan/nexus",
                "supported_repos": ["sumipan/nexus"],
                "installs": {"issuesmith": "/var/tmp/issuesmith"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    cfg = load_config()
    install_dir = str(cfg.installs["issuesmith"])

    pyproject = (
        'issuesmith = "issuesmith @ git+https://github.com/sumipan/issuesmith.git@v0.133.1"\n'
    )

    def fake_run(cmd, **kwargs):
        if len(cmd) >= 5 and cmd[0:2] == ["git", "-C"] and cmd[2] == str(tmp_path) and cmd[3] == "show":
            return MagicMock(returncode=0, stdout=pyproject)
        if len(cmd) >= 3 and cmd[0:2] == ["git", "-C"] and cmd[2] == install_dir:
            return MagicMock(returncode=0, stdout="v0.133.1\n")
        return MagicMock(returncode=1, stdout="")

    monkeypatch.setattr("issuesmith.preconditions.subprocess.run", fake_run)
    monkeypatch.setattr("issuesmith.pins.subprocess.run", fake_run)
    monkeypatch.setattr(
        "issuesmith.scope_gate.resolve_scope_root",
        lambda metadata, cfg: tmp_path,
    )

    body = _body(requires_pins={"issuesmith": "v0.133.1"})
    ok, why = phase_preconditions("develop", _develop_issue(body), MagicMock(), 1)
    assert ok is True
    assert why == "ok"


def test_develop_invalid_requires_pins(tmp_path, monkeypatch):
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(yaml.safe_dump({"repo": "sumipan/nexus"}), encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    load_config()
    body = _body(requires_pins={"issuesmith": "banana"})
    ok, why = phase_preconditions("develop", _develop_issue(body), MagicMock(), 1)
    assert ok is False
    assert why.startswith("invalid requires_pins:")
