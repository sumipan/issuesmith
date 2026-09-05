from issuesmith.pipeline_comments import (
    PIPELINE_COMMENT_PATTERNS,
    extract_user_comments,
    is_pipeline_comment,
    validate_b1_dep_comment,
)


def test_pipeline_status_excluded():
    assert is_pipeline_comment("some text\nPIPELINE_STATUS: VERIFY_DONE\nmore")


def test_b1_section_excluded():
    assert is_pipeline_comment("## B1 ブラッシュアップ結果\ncontent")


def test_cp1_section_excluded():
    assert is_pipeline_comment("## CP1 チェックポイント結果\nCP1_STATUS: PASS")


def test_cp2_section_excluded():
    assert is_pipeline_comment("## CP2 チェックポイント結果\nCP2_STATUS: PASS")


def test_p3_section_excluded():
    assert is_pipeline_comment("## P3 実装報告\ncontent")


def test_m1_section_excluded():
    assert is_pipeline_comment("## M1 マージ結果\ncontent")


def test_pipeline_branch_comment_excluded():
    assert is_pipeline_comment("<!-- pipeline-branch: feat/issue-123 -->")


def test_user_comment_not_excluded():
    assert not is_pipeline_comment("Hey, please fix this bug")


def test_normal_markdown_not_excluded():
    assert not is_pipeline_comment("## 設計\n詳細はこちら")


def test_extract_user_comments_filters_pipeline():
    comments = [
        {"body": "PIPELINE_STATUS: BRUSHUP_DONE"},
        {"body": "Please also add tests"},
        {"body": "## CP1 チェックポイント結果\nCP1_STATUS: PASS"},
        {"body": "Thanks for the fix"},
    ]
    result = extract_user_comments(comments)
    assert len(result) == 2
    assert result[0]["body"] == "Please also add tests"
    assert result[1]["body"] == "Thanks for the fix"


def test_extract_user_comments_empty():
    assert extract_user_comments([]) == []


def test_extract_user_comments_all_pipeline():
    comments = [
        {"body": "PIPELINE_STATUS: DONE"},
        {"body": "## B1 結果"},
    ]
    assert extract_user_comments(comments) == []


def test_patterns_list_not_empty():
    assert len(PIPELINE_COMMENT_PATTERNS) > 0


def test_validate_b1_dep_comment_valid():
    body = """## B1 事前検証失敗: 未マージ依存

未マージ: #100, #200

PIPELINE_STATUS: BRUSHUP_FAILED
"""
    assert validate_b1_dep_comment(body) == []


def test_validate_b1_dep_comment_missing_header():
    body = """未マージ: #100

PIPELINE_STATUS: BRUSHUP_FAILED
"""
    violations = validate_b1_dep_comment(body)
    assert any("missing header" in v for v in violations)


def test_validate_b1_dep_comment_missing_issue_ref():
    body = """## B1 事前検証失敗: 未マージ依存

未マージ: none

PIPELINE_STATUS: BRUSHUP_FAILED
"""
    violations = validate_b1_dep_comment(body)
    assert any("must contain at least one" in v for v in violations)


def test_validate_b1_dep_comment_missing_pipeline_status():
    body = """## B1 事前検証失敗: 未マージ依存

未マージ: #100
"""
    violations = validate_b1_dep_comment(body)
    assert any("PIPELINE_STATUS" in v for v in violations)


def test_main_filters_pipeline_comments_from_stdin_json(monkeypatch, capsys):
    import io
    import json
    import sys

    from issuesmith import pipeline_comments

    payload = {
        "body": "issue body",
        "comments": [
            {"author": "user", "body": "制約: X 禁止", "createdAt": "2026-01-01"},
            {"author": "bot", "body": "PIPELINE_STATUS: BRUSHUP_DONE", "createdAt": "2026-01-02"},
            {"author": "bot", "body": "## CP2 チェック結果", "createdAt": "2026-01-03"},
        ],
    }
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))

    pipeline_comments.main()

    output = json.loads(capsys.readouterr().out)
    assert output["body"] == "issue body"
    assert [c["body"] for c in output["comments"]] == ["制約: X 禁止"]
