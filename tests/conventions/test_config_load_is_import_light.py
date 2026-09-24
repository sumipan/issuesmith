"""Loading a config that declares requires must work in a fresh interpreter (sumipan/nexus#3687).

The gate registry imports every gate rule; some rules call get_config() at import time. If the
loader itself imported the registry, that re-entrancy raised
``ImportError: partially initialized module 'issuesmith.gates'`` for every command as soon as
``requires:`` appeared in issuesmith.yaml. These tests run in a subprocess on purpose: inside a
long-lived pytest process the modules are already imported and the cycle is invisible.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

from issuesmith.gates import GATE_REGISTRY
from tests.conventions._util import REPO_ROOT


def _write_config(tmp_path: Path, requires: list[str], input_kind: str) -> Path:
    cfg = tmp_path / "issuesmith.yaml"
    cfg.write_text(
        yaml.safe_dump(
            {
                "repo": "example/repo",
                "steps": {
                    "s1": {"module": "issuesmith.steps.p0_worktree", "requires": requires, "input_kind": input_kind}
                },
            }
        ),
        encoding="utf-8",
    )
    return cfg


def _run(code: str, cfg: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "ISSUESMITH_CONFIG": str(cfg), "PYTHONPATH": str(REPO_ROOT / "src")}
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=120)


def test_config_with_every_registered_gate_loads_in_fresh_interpreter(tmp_path: Path):
    cfg = _write_config(tmp_path, sorted(GATE_REGISTRY), "worktree")
    proc = _run(
        "from issuesmith.config import load_config; c = load_config();"
        " from issuesmith.gates import validate_step_requires; validate_step_requires(c.steps);"
        " print(sorted(c.steps['s1'].requires))",
        cfg,
    )
    assert proc.returncode == 0, proc.stderr[-1500:]
    assert json.loads(proc.stdout.replace("'", '"')) == sorted(GATE_REGISTRY)


def test_load_config_does_not_import_the_gate_registry(tmp_path: Path):
    cfg = _write_config(tmp_path, ["deps"], "issue")
    proc = _run(
        "import sys; from issuesmith.config import load_config; load_config();"
        " print('issuesmith.gates' in sys.modules)",
        cfg,
    )
    assert proc.returncode == 0, proc.stderr[-1500:]
    assert proc.stdout.strip() == "False"


def test_doctor_reports_unknown_gate_id_instead_of_crashing(tmp_path: Path):
    cfg = _write_config(tmp_path, ["no_such_gate"], "issue")
    proc = _run(
        "from issuesmith.config import load_config; from issuesmith.ops.doctor import requires_chain_report;"
        " print(requires_chain_report(load_config().steps))",
        cfg,
    )
    assert proc.returncode == 0, proc.stderr[-1500:]
    assert "no_such_gate" in proc.stdout
