"""Public ``issuesmith.verbs.acceptance`` gate and review tier (#6582)."""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from issuesmith.ac_complete_entry import check_gate_multi_root
from issuesmith.ac_contract import GateMaterializationError
from issuesmith.config import get_config
from issuesmith.pr_scope import DIFF_LINES_FALLBACK
from issuesmith.verbs import acceptance as vac

# ASCII fixture data (multi-root contract).
_BODY_MULTI_CONTRACT = """\
## Acceptance Criteria

- [x] done

```yaml
paths_must_exist:
  - only_in_nexus.md
  - only_in_target.py
```
"""

_BODY_UNCHECKED = """\
## Acceptance Criteria

- [ ] todo
"""

_BODY_CHECKED_OUTSIDE_AC = """\
## Other
- [ ] ignore

## Acceptance Criteria

- [x] one
- [ ] two
"""


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def _init_repo(path: Path, *, origin_main: bool = True) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "t")
    (path / "README.md").write_text("base\n", encoding="utf-8")
    _git(path, "add", "README.md")
    _git(path, "commit", "-q", "-m", "init")
    if origin_main:
        subprocess.run(
            ["git", "-C", str(path), "remote", "add", "origin", str(path)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(path), "update-ref", "refs/remotes/origin/main", "HEAD"],
            check=True,
            capture_output=True,
        )
    return path


@pytest.fixture
def gate_tmpdir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Keep ``materialize_gate_root`` temp dirs under pytest ``tmp_path``."""
    counter = {"n": 0}

    def _mkdtemp(prefix: str = "", **kwargs: object) -> str:
        counter["n"] += 1
        root = tmp_path / f"{prefix}{counter['n']}"
        root.mkdir(parents=True, exist_ok=True)
        return str(root)

    monkeypatch.setattr("issuesmith.ac_contract.tempfile.mkdtemp", _mkdtemp)
    return tmp_path


def _list_gate_worktrees(repo: Path) -> list[str]:
    out = _git(repo, "worktree", "list", "--porcelain")
    paths: list[str] = []
    for line in out.splitlines():
        if line.startswith("worktree "):
            wt = line.removeprefix("worktree ").strip()
            if "gate-" in Path(wt).name or "/gate-" in wt:
                paths.append(wt)
    return paths


def test_check_acceptance_returns_retry_and_cleans_single_root(
    tmp_path: Path, gate_tmpdir: Path
) -> None:
    """Refs sumipan/nexus#6582 — unchecked AC yields retry and no leftover worktrees."""
    repo = _init_repo(tmp_path / "host")
    verdict = vac.check_acceptance(
        _BODY_UNCHECKED,
        [],
        repos={"sumipan/issuesmith": repo},
        base_branch="main",
    )
    assert verdict == vac.AcceptanceVerdict(action="retry", unchecked_count=1, contract_failures=())
    assert _list_gate_worktrees(repo) == []


def test_check_acceptance_matches_multi_root_gate(
    tmp_path: Path, gate_tmpdir: Path
) -> None:
    """Refs sumipan/nexus#6582 — 2-repo materialized gate matches check_gate_multi_root."""
    nexus = _init_repo(tmp_path / "nexus")
    target = _init_repo(tmp_path / "target")
    (nexus / "only_in_nexus.md").write_text("ok", encoding="utf-8")
    (target / "only_in_target.py").write_text("ok", encoding="utf-8")
    _git(nexus, "add", "only_in_nexus.md")
    _git(nexus, "commit", "-q", "-m", "nexus file")
    subprocess.run(
        ["git", "-C", str(nexus), "update-ref", "refs/remotes/origin/main", "HEAD"],
        check=True,
        capture_output=True,
    )
    _git(target, "add", "only_in_target.py")
    _git(target, "commit", "-q", "-m", "target file")
    subprocess.run(
        ["git", "-C", str(target), "update-ref", "refs/remotes/origin/main", "HEAD"],
        check=True,
        capture_output=True,
    )

    repos = {"sumipan/nexus": nexus, "sumipan/issuesmith": target}
    verdict = vac.check_acceptance(
        _BODY_MULTI_CONTRACT,
        [],
        repos=repos,
        base_branch="main",
    )
    expected_raw = check_gate_multi_root(
        _BODY_MULTI_CONTRACT,
        [],
        {"sumipan/nexus": nexus, "sumipan/issuesmith": target},
        contracts={
            "sumipan/nexus": {"paths_must_exist": ["only_in_nexus.md"]},
            "sumipan/issuesmith": {"paths_must_exist": ["only_in_target.py"]},
        },
    )
    assert verdict.action == expected_raw["action"]
    assert verdict.unchecked_count == expected_raw["unchecked_count"]
    assert verdict.contract_failures == tuple(expected_raw.get("contract_failures") or ())
    assert _list_gate_worktrees(nexus) == []
    assert _list_gate_worktrees(target) == []


def test_check_acceptance_cleans_first_root_when_second_materialize_fails(
    tmp_path: Path,
) -> None:
    """Refs sumipan/nexus#6582 — second materialize failure still cleans the first root."""
    first = _init_repo(tmp_path / "first")
    second = tmp_path / "second"
    second.mkdir()
    _init_repo(second, origin_main=False)

    with patch(
        "issuesmith.verbs.acceptance.materialize_gate_root",
        side_effect=[tmp_path / "mat1", GateMaterializationError("boom")],
    ) as mock_mat:
        with patch("issuesmith.verbs.acceptance.cleanup_gate_root") as mock_cleanup:
            with pytest.raises(vac.AcceptanceError, match="boom"):
                vac.check_acceptance(
                    _BODY_UNCHECKED,
                    [],
                    repos={"sumipan/nexus": first, "sumipan/issuesmith": second},
                    base_branch="main",
                )
    assert mock_mat.call_count == 2
    mock_cleanup.assert_called_once_with(first, tmp_path / "mat1")


def test_check_acceptance_raises_when_base_ref_missing(
    tmp_path: Path, gate_tmpdir: Path
) -> None:
    """Refs sumipan/nexus#6582 — missing origin/base raises without leaving worktrees."""
    repo = _init_repo(tmp_path / "bare", origin_main=False)
    with pytest.raises(vac.AcceptanceError):
        vac.check_acceptance(
            _BODY_UNCHECKED,
            [],
            repos={"sumipan/issuesmith": repo},
            base_branch="main",
        )
    assert _list_gate_worktrees(repo) == []


def test_unchecked_acceptance_count_counts_only_acceptance_section() -> None:
    """Refs sumipan/nexus#6582 — unchecked items outside AC section are ignored."""
    assert vac.unchecked_acceptance_count(_BODY_CHECKED_OUTSIDE_AC) == 1


def test_review_tier_uses_fallback_when_pr_is_missing() -> None:
    """Refs sumipan/nexus#6582 — no PR uses diff fallback and heavy tier."""
    client = MagicMock()
    client.api_request.return_value = []
    ac_heading = get_config().sections["acceptance_criteria"]
    body = f"## {ac_heading}\n- [x] done\n"
    facts = vac.review_tier(
        client,
        "sumipan/nexus",
        "feat/none",
        issue_number=99,
        issue_body=body,
        gates_pass=True,
    )
    assert facts.pr_number is None
    assert facts.head_sha is None
    assert facts.diff_lines == DIFF_LINES_FALLBACK
    assert facts.unchecked == 0
    assert facts.tier == "heavy"


def test_review_tier_returns_pr_head_and_light_tier() -> None:
    """Refs sumipan/nexus#6582 — small diff with gates pass yields light tier and head sha."""
    client = MagicMock()
    client.api_request.return_value = [
        {
            "number": 7,
            "head": {"ref": "feat/small", "sha": "abc123deadbeef"},
            "body": "",
        }
    ]
    client.pr_get.return_value = {
        "number": 7,
        "additions": 3,
        "deletions": 1,
        "head": {"sha": "abc123deadbeef", "ref": "feat/small"},
    }
    ac_heading = get_config().sections["acceptance_criteria"]
    body = f"## {ac_heading}\n- [x] ok\n"
    facts = vac.review_tier(
        client,
        "sumipan/nexus",
        "feat/small",
        issue_number=None,
        issue_body=body,
        gates_pass=True,
    )
    assert facts.pr_number == 7
    assert facts.head_sha == "abc123deadbeef"
    assert facts.diff_lines == 4
    assert facts.unchecked == 0
    assert facts.tier == "light"


def test_extract_and_check_contract_preserve_record_schema(tmp_path: Path) -> None:
    """Refs sumipan/nexus#6582 — extract/check contract keeps run_checks record shape."""
    body = """\
## Acceptance Criteria

```yaml
paths_must_exist:
  - src/keep.py
  - src/miss.py
```

- [x] done
"""
    contract = vac.extract_contract(body)
    assert contract is not None
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "keep.py").write_text("", encoding="utf-8")
    records = vac.check_contract(contract, tmp_path)
    keys = {"check", "path", "result", "detail"}
    assert all(keys <= set(r) for r in records)
    by_path = {r["path"]: r for r in records}
    assert by_path["src/keep.py"]["result"] == "PASS"
    assert by_path["src/miss.py"]["result"] == "FAIL"
