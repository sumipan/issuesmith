"""Deterministic diagnosis proposal parsing (issue #8078)."""

from __future__ import annotations

import json

import pytest

from issuesmith.config import IssuesmithConfig, reset_config_cache
from issuesmith.diagnose import ProposalError, parse_proposal


def _minimal_config(tmp_path, monkeypatch, **steps: str) -> IssuesmithConfig:
    import yaml

    from issuesmith.config import load_config

    step_ids = list(steps) or ["p1"]
    data = {
        "repo": "org/repo",
        "supported_repos": ["sumipan/issuesmith"],
        "steps": {sid: {"module": "pkg.mod"} for sid in step_ids},
    }
    path = tmp_path / "issuesmith.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(path))
    reset_config_cache()
    return load_config(path)


def _fence(body: str) -> str:
    return f"```andon-diagnosis\n{body}```\nPIPELINE_STATUS: DIAGNOSIS_DONE\n"


def _record(fix_kinds: list[str] | None = None) -> dict:
    kinds = fix_kinds if fix_kinds is not None else ["edit_issue"]
    return {"id": "wf:1:p1:0", "fix_kinds": kinds, "facts": {}}


def test_rejects_glob_in_allow_paths_add(tmp_path, monkeypatch):
    """Refs #8078 AC7."""
    cfg = _minimal_config(tmp_path, monkeypatch)
    text = _fence("fix_kind: edit_issue\nsummary: x\nallow_paths_add:\n  - '**'\n")
    with pytest.raises(ProposalError, match="glob"):
        parse_proposal(text, andon_record=_record(), issue_body="# body", config=cfg)


def test_rejects_body_patch_hunk_mismatch(tmp_path, monkeypatch):
    """Refs #8078 AC8."""
    cfg = _minimal_config(tmp_path, monkeypatch)
    issue_body = "line one\nline two\n"
    patch = (
        "--- issue.md\n+++ issue.md\n@@ -1,2 +1,2 @@\n-wrong line\n+new line\n"
    )
    text = _fence(
        "fix_kind: edit_issue\nsummary: x\n"
        f"body_patch: {json.dumps(patch)}\n"
    )
    with pytest.raises(ProposalError, match="mismatch"):
        parse_proposal(text, andon_record=_record(), issue_body=issue_body, config=cfg)


def test_rejects_body_patch_without_hunk(tmp_path, monkeypatch):
    cfg = _minimal_config(tmp_path, monkeypatch)
    patch = "--- issue.md\n+++ issue.md\n"
    text = _fence("fix_kind: edit_issue\nsummary: x\n" f"body_patch: {json.dumps(patch)}\n")
    with pytest.raises(ProposalError, match="issue.md"):
        parse_proposal(text, andon_record=_record(), issue_body="body", config=cfg)


def test_rejects_body_patch_with_false_hunk_count(tmp_path, monkeypatch):
    cfg = _minimal_config(tmp_path, monkeypatch)
    patch = "--- issue.md\n+++ issue.md\n@@ -1,2 +1,1 @@\n-body\n+new\n"
    text = _fence("fix_kind: edit_issue\nsummary: x\n" f"body_patch: {json.dumps(patch)}\n")
    with pytest.raises(ProposalError, match="count mismatch"):
        parse_proposal(text, andon_record=_record(), issue_body="body", config=cfg)


@pytest.mark.parametrize(
    "field,value,message",
    [
        ("allow_paths_add", "null", "allow_paths_add"),
        ("body_patch", "1", "body_patch"),
    ],
)
def test_rejects_wrong_edit_issue_payload_types(tmp_path, monkeypatch, field, value, message):
    cfg = _minimal_config(tmp_path, monkeypatch)
    text = _fence(
        f"fix_kind: edit_issue\nsummary: x\nallow_paths_remove: [src/old.py]\n{field}: {value}\n"
    )
    with pytest.raises(ProposalError, match=message):
        parse_proposal(text, andon_record=_record(), issue_body="body", config=cfg)


def test_rejects_non_string_top_level_key(tmp_path, monkeypatch):
    cfg = _minimal_config(tmp_path, monkeypatch)
    text = _fence("fix_kind: edit_issue\nsummary: x\nallow_paths_add: [src/new.py]\n1: bad\n")
    with pytest.raises(ProposalError, match="keys"):
        parse_proposal(text, andon_record=_record(), issue_body="body", config=cfg)


def test_rejects_unsupported_target_repo_for_fix_line(tmp_path, monkeypatch):
    """Refs #8078 AC9."""
    cfg = _minimal_config(tmp_path, monkeypatch)
    text = _fence(
        "fix_kind: fix_line\nsummary: line\n"
        "target_repo: unknown/repo\ntitle: t\nbody: b\n"
    )
    with pytest.raises(ProposalError, match="supported_repos"):
        parse_proposal(text, andon_record=_record(fix_kinds=["fix_line"]), issue_body="", config=cfg)


def test_rejects_empty_unknowns_for_diagnose_kind(tmp_path, monkeypatch):
    """Refs #8078 AC10."""
    cfg = _minimal_config(tmp_path, monkeypatch)
    text = _fence("fix_kind: diagnose\nsummary: need data\nunknowns: []\n")
    with pytest.raises(ProposalError, match="unknowns"):
        parse_proposal(text, andon_record=_record(fix_kinds=["diagnose"]), issue_body="", config=cfg)


def test_rejects_non_string_unknown(tmp_path, monkeypatch):
    cfg = _minimal_config(tmp_path, monkeypatch)
    text = _fence("fix_kind: diagnose\nsummary: need data\nunknowns: [valid, 1]\n")
    with pytest.raises(ProposalError, match="unknowns"):
        parse_proposal(text, andon_record=_record(fix_kinds=["diagnose"]), issue_body="", config=cfg)


def test_fix_line_normalizes_signature_to_one_trailing_marker(tmp_path, monkeypatch):
    cfg = _minimal_config(tmp_path, monkeypatch)
    record = _record(fix_kinds=["fix_line"])
    from issuesmith.andon import diagnosis_signature

    marker = f"<!-- issuesmith-diagnosis: {diagnosis_signature(record)} -->"
    text = _fence(
        "fix_kind: fix_line\nsummary: line\ntarget_repo: sumipan/issuesmith\n"
        f"title: t\nbody: |\n  {marker}\n  body\n  {marker}\n"
    )
    proposal = parse_proposal(text, andon_record=record, issue_body="", config=cfg)
    assert proposal.payload["body"].count(marker) == 1
    assert proposal.payload["body"].endswith(marker)


def test_accepts_clean_env_with_steps_and_rerun(tmp_path, monkeypatch):
    """Refs #8078 AC11."""
    cfg = _minimal_config(tmp_path, monkeypatch, p1="x", verify="x")
    text = _fence(
        "fix_kind: clean_env\nsummary: rebuild\n"
        "steps:\n  - wipe\nrerun_from: p1\n"
    )
    proposal = parse_proposal(
        text,
        andon_record=_record(fix_kinds=["clean_env"]),
        issue_body="",
        config=cfg,
    )
    assert proposal.payload["steps"] == ["wipe"]
    assert proposal.payload["rerun_from"] == "p1"


def test_accepts_review_override(tmp_path, monkeypatch):
    """Refs #8078 AC12."""
    cfg = _minimal_config(tmp_path, monkeypatch, p1="x", review="x")
    text = _fence(
        "fix_kind: review_override\nsummary: override\n"
        "assessment: acceptable risk\nresume_from: p1\n"
    )
    proposal = parse_proposal(
        text,
        andon_record=_record(fix_kinds=["review_override"]),
        issue_body="",
        config=cfg,
    )
    assert proposal.payload["resume_from"] == "p1"
