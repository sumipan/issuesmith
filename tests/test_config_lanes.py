"""Config loading for andon.auto_answer and paths.lanes (#4792)."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from issuesmith.config import ConfigError, load_config, reset_config_cache


@pytest.fixture(autouse=True)
def _clear_cache():
    reset_config_cache()
    yield
    reset_config_cache()


def test_andon_auto_answer_loaded(tmp_path: Path, monkeypatch):
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump(
            {
                "repo": "org/repo",
                "andon": {
                    "auto_answer": [
                        {
                            "name": "cp2_review_fail",
                            "match": {"step": "cp2", "kind": "decision"},
                            "action": "resume_from_p1",
                            "max_per_issue": 2,
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    cfg = load_config(cfg_path)
    assert len(cfg.andon.auto_answer) == 1
    rule = cfg.andon.auto_answer[0]
    assert rule.name == "cp2_review_fail"
    assert rule.match["step"] == "cp2"
    assert rule.max_per_issue == 2


def test_paths_lanes_resolved(tmp_path: Path, monkeypatch):
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump({"repo": "org/repo", "paths": {"lanes": "configs/lanes.yaml"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    cfg = load_config(cfg_path)
    assert cfg.paths.lanes == (tmp_path / "configs/lanes.yaml").resolve()


def test_duplicate_auto_answer_name_raises(tmp_path: Path):
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump(
            {
                "repo": "org/repo",
                "andon": {
                    "auto_answer": [
                        {"name": "x", "match": {"step": "a"}, "action": "resume"},
                        {"name": "x", "match": {"step": "b"}, "action": "resume"},
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_config(cfg_path)
