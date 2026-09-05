"""tests/test_publish_metadata.py — publish PR 本文メタデータ（#2866）"""

from issuesmith.ops.publish import _build_pr_metadata


def test_single_target_same_repo_uses_closes():
    title, body = _build_pr_metadata(42, "sumipan/nexus", "sumipan/nexus", target_count=1)
    assert "Closes #42" in body
    assert "Refs" not in body


def test_single_target_cross_repo_never_closes():
    """cross-repo は target_count に関わらず Closes を使わない（2026-09-06、#2873 実測の回帰）。

    cross-repo issue は必ず M2 finalize（issue_repo 側での merge-done 遷移 + close）を
    経る設計であり、target repo 側 PR のマージだけで issue を閉じると finalize が
    走らないまま孤立する。過去に ref を未修飾（`#{N}`）で auto-close が機能していな
    かった時期はこの分岐で実害がなかったが、qualified ref（`{issue_repo}#{N}`）が
    実際に機能するようになった結果、premature close が発生した。
    """
    title, body = _build_pr_metadata(99, "sumipan/issuesmith", "sumipan/nexus", target_count=1)
    assert "Refs sumipan/nexus#99" in body
    assert "Closes" not in body


def test_multi_target_cross_repo_uses_qualified_refs():
    title, body = _build_pr_metadata(42, "sumipan/issuesmith", "sumipan/nexus", target_count=2)
    assert "Refs sumipan/nexus#42" in body
    assert "Closes" not in body


def test_multi_target_same_repo_uses_refs():
    title, body = _build_pr_metadata(42, "sumipan/nexus", "sumipan/nexus", target_count=2)
    assert "Refs #42" in body
    assert "Closes" not in body
