from issuesmith.pipeline_comments import (
    _B1_DEP_HEADER,
    _UNMERGED_LINE,
    PIPELINE_COMMENT_PATTERNS,
    extract_user_comments,
    is_pipeline_comment,
    validate_b1_dep_comment,
)


def test_pipeline_status_excluded():
    assert is_pipeline_comment("some text\nPIPELINE_STATUS: VERIFY_DONE\nmore")


def test_b1_section_excluded():
    # ASCII fixture data.
    assert is_pipeline_comment("## B1 c30D6_c30E9_c30C3_c30B7_c30E5_c30A2_c30C3_c30D7_c7D50_c679C\ncontent")


def test_cp1_section_excluded():
    # ASCII fixture data.
    assert is_pipeline_comment("## CP1 c30C1_c30A7_c30C3_c30AF_c30DD_c30A4_c30F3_c30C8_c7D50_c679C\nCP1_STATUS: PASS")


def test_cp2_section_excluded():
    # ASCII fixture data.
    assert is_pipeline_comment("## CP2 c30C1_c30A7_c30C3_c30AF_c30DD_c30A4_c30F3_c30C8_c7D50_c679C\nCP2_STATUS: PASS")


def test_p3_section_excluded():
    # ASCII fixture data.
    assert is_pipeline_comment("## P3 c5B9F_c88C5_c5831_c544A\ncontent")


def test_m1_section_excluded():
    # ASCII fixture data.
    assert is_pipeline_comment("## M1 c30DE_c30FC_c30B8_c7D50_c679C\ncontent")


def test_pipeline_branch_comment_excluded():
    assert is_pipeline_comment("<!-- pipeline-branch: feat/issue-123 -->")


def test_user_comment_not_excluded():
    assert not is_pipeline_comment("Hey, please fix this bug")


def test_normal_markdown_not_excluded():
    # ASCII fixture data.
    assert not is_pipeline_comment("## Design\nDetails here")


def test_extract_user_comments_filters_pipeline():
    comments = [
        {"body": "PIPELINE_STATUS: BRUSHUP_DONE"},
        {"body": "Please also add tests"},
        # ASCII fixture data.
        {"body": "## CP1 c30C1_c30A7_c30C3_c30AF_c30DD_c30A4_c30F3_c30C8_c7D50_c679C\nCP1_STATUS: PASS"},
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
        # ASCII fixture data.
        {"body": "## B1 c7D50_c679C"},
    ]
    assert extract_user_comments(comments) == []


def test_patterns_list_not_empty():
    assert len(PIPELINE_COMMENT_PATTERNS) > 0


def test_validate_b1_dep_comment_valid():
    body = f"{_B1_DEP_HEADER} blocked\n\n{_UNMERGED_LINE} #100, #200\n\nPIPELINE_STATUS: BRUSHUP_FAILED\n"
    assert validate_b1_dep_comment(body) == []


def test_validate_b1_dep_comment_missing_header():
    # ASCII fixture data.
    body = f"{_UNMERGED_LINE} #100\n\nPIPELINE_STATUS: BRUSHUP_FAILED\n"
    violations = validate_b1_dep_comment(body)
    assert any("missing header" in v for v in violations)


def test_validate_b1_dep_comment_missing_issue_ref():
    body = f"{_B1_DEP_HEADER} blocked\n\n{_UNMERGED_LINE} none\n\nPIPELINE_STATUS: BRUSHUP_FAILED\n"
    violations = validate_b1_dep_comment(body)
    assert any("must contain at least one" in v for v in violations)


def test_validate_b1_dep_comment_missing_pipeline_status():
    body = f"{_B1_DEP_HEADER} blocked\n\n{_UNMERGED_LINE} #100\n"
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
            {"author": "user", "body": "Constraint: X forbidden", "createdAt": "2026-01-01"},
            {"author": "bot", "body": "PIPELINE_STATUS: BRUSHUP_DONE", "createdAt": "2026-01-02"},
            # ASCII fixture data.
            {"author": "bot", "body": "## CP2 c30C1_c30A7_c30C3_c30AF_c7D50_c679C", "createdAt": "2026-01-03"},
        ],
    }
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))

    pipeline_comments.main()

    output = json.loads(capsys.readouterr().out)
    assert output["body"] == "issue body"
    assert [c["body"] for c in output["comments"]] == ["Constraint: X forbidden"]
