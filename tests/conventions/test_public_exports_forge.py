"""Public export contract for issuesmith.forge (sumipan/nexus#6481)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import ghdag.forge as ghdag_forge

import issuesmith.forge as forge_mod
from issuesmith import forge as issuesmith_forge
from tests.conventions._util import REPO_ROOT

FORGE_PUBLIC_NAMES = frozenset(
    {
        "ForgePort",
        "GitHubApiError",
        "GitHubClient",
        "LocalForge",
        "get_forge",
    }
)


def test_forge_public_exports():
    """issuesmith.forge __all__ matches the contract; each name is ghdag.forge."""
    assert frozenset(forge_mod.__all__) == FORGE_PUBLIC_NAMES
    for name in FORGE_PUBLIC_NAMES:
        assert hasattr(issuesmith_forge, name), f"forge missing attribute {name}"
    assert issuesmith_forge.ForgePort is ghdag_forge.ForgePort
    assert issuesmith_forge.GitHubApiError is ghdag_forge.GitHubApiError
    assert issuesmith_forge.GitHubClient is ghdag_forge.GitHubClient
    assert issuesmith_forge.LocalForge is ghdag_forge.LocalForge
    assert issuesmith_forge.get_forge is ghdag_forge.get_forge


def test_forge_import_does_not_require_config(tmp_path: Path):
    """Fresh interpreter imports issuesmith.forge without config file or forge env vars."""
    env = {
        k: v
        for k, v in os.environ.items()
        if k
        not in (
            "GHDAG_FORGE",
            "GHDAG_FORGE_ROOT",
            "GITHUB_TOKEN",
            "GITHUB_REPOSITORIES",
            "ISSUESMITH_CONFIG",
        )
    }
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    proc = subprocess.run(
        [sys.executable, "-c", "import issuesmith.forge"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=env,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
