"""Suite-level isolation from a host GitHub token (sumipan/nexus#7210)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from ghdag.forge import LocalForge, get_forge

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_suite_defaults_to_local_forge_without_tokens() -> None:
    """Refs: sumipan/nexus#7210 — conftest forces local forge and strips host tokens."""
    forge = get_forge()
    assert isinstance(forge, LocalForge)
    assert "GITHUB_TOKEN" not in os.environ
    assert "GH_TOKEN" not in os.environ


def test_host_token_does_not_reach_suite() -> None:
    """Refs: sumipan/nexus#7210 — host forge env vars must not survive conftest import."""
    env = os.environ.copy()
    env["GITHUB_TOKEN"] = "fake-host-token"
    env["GH_TOKEN"] = "fake-host-token"
    env["GHDAG_FORGE"] = "github"
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_suite_forge_isolation.py",
            "-k",
            "test_suite_defaults_to_local_forge_without_tokens",
            "-q",
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
