"""Second consumer (corklab-style) declaration drives queue without OSS literals (#4790)."""

from __future__ import annotations

import yaml

from issuesmith.config import get_config, reset_config_cache
from issuesmith.queue import redispatch_label_plan

CORKLAB_PHASES = [
    {
        "name": "develop",
        "role": "implementation",
        "entry_step": "p0",
        "handler": "impl",
        "steps": ["p0", "p1"],
        "advance_when": ["deps_terminal"],
    },
    {
        "name": "deploy",
        "role": "implementation",
        "entry_step": "d1",
        "handler": "deploy",
        "steps": ["d1"],
        "preconditions": ["develop-done"],
        "advance_when": ["deps_terminal"],
    },
    {
        "name": "rollback",
        "role": "implementation",
        "entry_step": "r1",
        "handler": "rollback",
        "steps": ["r1"],
        "preconditions": ["deploy-done"],
        "advance_when": ["deps_terminal"],
    },
]


def _write_corklab(tmp_path, monkeypatch) -> None:
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump({"repo": "example/corklab", "phases": CORKLAB_PHASES}),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()


def test_redispatch_label_plan_from_declaration(tmp_path, monkeypatch):
    _write_corklab(tmp_path, monkeypatch)
    cfg = get_config()
    required, to_remove = redispatch_label_plan("deploy")
    assert required == cfg.required_labels("deploy")
    ready, running, done = cfg.phase_labels("deploy")
    assert to_remove == frozenset({ready, running, done})






