"""tests/test_targets.py — targets_from_issue() のユニットテスト"""

import pytest

from issuesmith.targets import Target, targets_from_issue

ISSUE_REPO = "sumipan/nexus"


def _ac_body(*, paths_must_exist=None, targets=None) -> str:
    lines = ["## 受け入れ条件", "", "```yaml"]
    if targets is not None:
        lines.append("targets:")
        for t in targets:
            lines.append(f"  - repo: {t['repo']}")
            if "contract" in t:
                lines.append("    contract:")
                for key, vals in t["contract"].items():
                    lines.append(f"      {key}:")
                    for v in vals:
                        lines.append(f"        - {v}")
    elif paths_must_exist is not None:
        lines.append("paths_must_exist:")
        for p in paths_must_exist:
            lines.append(f"  - {p}")
    lines.append("```")
    return "\n".join(lines)


def test_old_format_target_repo_with_diary_allow_paths():
    metadata = {
        "target_repo": "sumipan/issuesmith",
        "allow_paths": ["src/**"],
        "diary_allow_paths": ["workflows/**"],
        "base_branch": "main",
    }
    targets = targets_from_issue(metadata, issue_repo=ISSUE_REPO)
    assert len(targets) == 2
    assert targets[0] == Target(
        repo="sumipan/issuesmith",
        base="main",
        allow_paths=("src/**",),
        contract={},
        primary=True,
    )
    assert targets[1] == Target(
        repo=ISSUE_REPO,
        base="main",
        allow_paths=("workflows/**",),
        contract={},
        primary=False,
    )


def test_old_format_target_repo_only():
    metadata = {
        "target_repo": "sumipan/issuesmith",
        "allow_paths": ["src/**"],
        "base_branch": "main",
    }
    targets = targets_from_issue(metadata, issue_repo=ISSUE_REPO)
    assert len(targets) == 1
    assert targets[0].repo == "sumipan/issuesmith"
    assert targets[0].primary is True


def test_old_format_no_target_repo():
    metadata = {"allow_paths": ["tools/**"], "base_branch": "main"}
    targets = targets_from_issue(metadata, issue_repo=ISSUE_REPO)
    assert len(targets) == 1
    assert targets[0].repo == ISSUE_REPO
    assert targets[0].primary is True
    assert targets[0].allow_paths == ("tools/**",)


def test_new_format_ac_targets_distribute_contract():
    metadata = {
        "target_repo": "sumipan/issuesmith",
        "allow_paths": ["src/**"],
        "diary_allow_paths": ["workflows/**"],
        "base_branch": "main",
    }
    body = _ac_body(
        targets=[
            {
                "repo": "sumipan/issuesmith",
                "contract": {"paths_must_exist": ["src/issuesmith/foo.py"]},
            },
            {
                "repo": ISSUE_REPO,
                "contract": {"paths_must_exist": ["workflows/issuesmith/bar.md"]},
            },
        ]
    )
    targets = targets_from_issue(metadata, issue_repo=ISSUE_REPO, body=body)
    assert targets[0].contract == {"paths_must_exist": ["src/issuesmith/foo.py"]}
    assert targets[1].contract == {"paths_must_exist": ["workflows/issuesmith/bar.md"]}


def test_old_format_legacy_contract_on_primary_with_diary():
    metadata = {
        "target_repo": "sumipan/issuesmith",
        "diary_allow_paths": ["workflows/**"],
        "base_branch": "main",
    }
    body = _ac_body(paths_must_exist=["src/issuesmith/foo.py"])
    targets = targets_from_issue(metadata, issue_repo=ISSUE_REPO, body=body)
    assert targets[0].contract == {"paths_must_exist": ["src/issuesmith/foo.py"]}
    assert targets[1].contract == {}


def test_old_format_legacy_contract_primary_only_single_target():
    metadata = {"target_repo": "sumipan/issuesmith", "base_branch": "main"}
    body = _ac_body(paths_must_exist=["src/issuesmith/foo.py"])
    targets = targets_from_issue(metadata, issue_repo=ISSUE_REPO, body=body)
    assert len(targets) == 1
    assert targets[0].contract == {"paths_must_exist": ["src/issuesmith/foo.py"]}


def test_metadata_targets_new_format():
    metadata = {
        "base_branch": "main",
        "targets": [
            {
                "repo": "sumipan/issuesmith",
                "allow_paths": ["src/**"],
                "primary": True,
            },
            {
                "repo": ISSUE_REPO,
                "allow_paths": ["workflows/**"],
                "primary": False,
            },
        ],
    }
    targets = targets_from_issue(metadata, issue_repo=ISSUE_REPO)
    assert len(targets) == 2
    assert targets[0].primary is True
    assert targets[0].repo == "sumipan/issuesmith"
    assert targets[1].primary is False


def test_multiple_primary_raises():
    metadata = {
        "targets": [
            {"repo": "sumipan/issuesmith", "primary": True},
            {"repo": ISSUE_REPO, "primary": True},
        ],
    }
    with pytest.raises(ValueError, match="primary"):
        targets_from_issue(metadata, issue_repo=ISSUE_REPO)


def test_nexus_target_repo_normalized_to_issue_repo():
    """target_repo == issue_repo はネイティブ単一ターゲットに正規化（#2567）。"""
    metadata = {"target_repo": ISSUE_REPO, "allow_paths": ["tools/**"]}
    targets = targets_from_issue(metadata, issue_repo=ISSUE_REPO)
    assert len(targets) == 1
    assert targets[0].repo == ISSUE_REPO
    assert targets[0].primary is True
