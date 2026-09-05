"""tests/test_publish_metadata.py — publish PR 本文メタデータ（#2866）"""

from issuesmith.ops.publish import _build_pr_metadata


def test_single_target_uses_closes():
    title, body = _build_pr_metadata(42, "sumipan/issuesmith", "sumipan/nexus", target_count=1)
    assert "Closes #42" in body
    assert "Refs" not in body


def test_multi_target_uses_refs_for_primary():
    title, body = _build_pr_metadata(42, "sumipan/issuesmith", "sumipan/nexus", target_count=2)
    assert "Refs #42" in body
    assert "Closes" not in body


def test_multi_target_secondary_uses_refs():
    title, body = _build_pr_metadata(42, "sumipan/nexus", "sumipan/nexus", target_count=2)
    assert "Refs #42" in body
    assert "Closes" not in body


def test_cross_repo_single_target_still_closes():
    """単一ターゲット cross-repo は Closes（旧 Related ではない）。"""
    title, body = _build_pr_metadata(99, "sumipan/issuesmith", "sumipan/nexus", target_count=1)
    assert "Closes #99" in body
