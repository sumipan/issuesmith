"""tests/test_publish_metadata.py — publish PR 本文メタデータ（#2866）"""

from issuesmith.ops.publish import _build_pr_metadata


def test_single_target_same_repo_uses_closes():
    title, body = _build_pr_metadata(42, "sumipan/nexus", "sumipan/nexus", target_count=1)
    assert "Closes #42" in body
    assert "Refs" not in body


def test_single_target_cross_repo_uses_qualified_closes():
    """cross-repo 単一ターゲットは Closes {issue_repo}#{N} で auto-close。"""
    title, body = _build_pr_metadata(99, "sumipan/issuesmith", "sumipan/nexus", target_count=1)
    assert "Closes sumipan/nexus#99" in body
    assert "Refs" not in body


def test_multi_target_cross_repo_uses_qualified_refs():
    title, body = _build_pr_metadata(42, "sumipan/issuesmith", "sumipan/nexus", target_count=2)
    assert "Refs sumipan/nexus#42" in body
    assert "Closes" not in body


def test_multi_target_same_repo_uses_refs():
    title, body = _build_pr_metadata(42, "sumipan/nexus", "sumipan/nexus", target_count=2)
    assert "Refs #42" in body
    assert "Closes" not in body
