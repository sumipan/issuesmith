"""Develop line publish step (refs sumipan/nexus#8011)."""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from issuesmith.config import StepConfig, get_config, reset_config_cache
from issuesmith.contract import StepContext
from issuesmith.gates.worktree import line_has_cjk
from issuesmith.lines.develop import publish as develop_publish
from issuesmith.ops.publish import PublishResult

_REPO_ROOT = Path(__file__).resolve().parents[2]
_LINE_PUB = _REPO_ROOT / "src" / "issuesmith" / "lines" / "develop" / "publish.py"
_VERBS_PUB = _REPO_ROOT / "src" / "issuesmith" / "verbs" / "publish.py"
_TEST_VERBS = _REPO_ROOT / "tests" / "test_verbs_publish.py"


def _git(worktree: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(worktree), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _bare_origin_with_worktree(tmp_path: Path, *, branch: str = "feat/x") -> tuple[Path, Path]:
    seed = tmp_path / "seed"
    origin = tmp_path / "origin.git"
    seed.mkdir()
    _git(seed, "init", "-q", "-b", "main")
    _git(seed, "config", "user.email", "t@example.com")
    _git(seed, "config", "user.name", "T")
    (seed / "README").write_text("base\n", encoding="utf-8")
    _git(seed, "add", "README")
    _git(seed, "commit", "-m", "init")
    subprocess.run(["git", "clone", "--bare", str(seed), str(origin)], check=True, capture_output=True)
    worktree = tmp_path / "worktree"
    subprocess.run(["git", "clone", str(origin), str(worktree)], check=True, capture_output=True)
    _git(worktree, "config", "user.email", "t@example.com")
    _git(worktree, "config", "user.name", "T")
    _git(worktree, "checkout", "-b", branch)
    return origin, worktree


def _init_git_worktree(tmp_path: Path) -> Path:
    wt = tmp_path / "wt"
    subprocess.run(["git", "init", "-q", "-b", "main", str(wt)], check=True, capture_output=True)
    _git(wt, "config", "user.email", "t@example.com")
    _git(wt, "config", "user.name", "T")
    return wt


def _publish_ctx(
    worktree: Path,
    *,
    branch: str = "feat/x",
    target_repo: str = "o/r",
    issue_repo: str = "o/r",
    is_cross_repo: str = "false",
) -> StepContext:
    return StepContext(
        issue_number="42",
        base_branch="main",
        handler_name="develop",
        is_cross_repo=is_cross_repo,
        target_clone_path="",
        target_repo=target_repo,
        issue_repo=issue_repo,
        source="",
        workflow_name="issuesmith",
        m1_result_filename="",
        m1r_result_filename="",
        worktree_path=str(worktree),
        branch=branch,
    )


def test_no_pr_target_is_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    worktree = tmp_path / "wt"
    worktree.mkdir()
    publish_calls: list[int] = []
    monkeypatch.setattr(
        develop_publish,
        "publish_pr",
        lambda *_a, **_k: publish_calls.append(1) or PublishResult(status="OK", pr_url="x"),
    )
    lang = get_config().language
    monkeypatch.setattr(
        develop_publish,
        "get_config",
        lambda: SimpleNamespace(
            merges_without_pr=lambda _repo: True,
            host_is_repo_checkout=False,
            root=Path("."),
            language=lang,
        ),
    )
    ctx = _publish_ctx(worktree, target_repo="example/no-pr")
    result = develop_publish.run(ctx, StepConfig())
    assert result.status == "done"
    assert result.markers == ["PUBLISH_DONE"]
    assert publish_calls == []
    assert "PR_URL:" not in capsys.readouterr().out


@pytest.fixture
def publish_push_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from ghdag.forge import LocalForge

    forge = LocalForge(tmp_path / "forge")
    _origin, worktree = _bare_origin_with_worktree(tmp_path)
    branch = "feat/x"
    (worktree / "README").write_text("feat\n", encoding="utf-8")
    _git(worktree, "add", "README")
    _git(worktree, "commit", "-m", "feat")
    _git(worktree, "push", "-u", "origin", branch)

    monkeypatch.setattr("issuesmith.ops.publish.get_forge", lambda **_k: forge)
    monkeypatch.setattr("issuesmith.ops.publish._commit_if_needed", lambda *a, **k: None)
    monkeypatch.setattr("issuesmith.ops.publish._ensure_rebased", lambda *a, **k: None)
    monkeypatch.setattr("issuesmith.ops.publish._check_commit_diff_gates", lambda *a, **k: None)
    monkeypatch.setattr("issuesmith.ops.publish._maybe_bump_version", lambda *a, **k: None)
    monkeypatch.setattr("issuesmith.ops.publish._push_branch", lambda *a, **k: None)
    monkeypatch.setattr("issuesmith.ops.publish._ahead_commit_count", lambda *a, **k: 1)
    lang = get_config().language
    monkeypatch.setattr(
        develop_publish,
        "get_config",
        lambda: SimpleNamespace(
            merges_without_pr=lambda _repo: False,
            host_is_repo_checkout=False,
            root=Path("."),
            language=lang,
        ),
    )
    yield worktree, branch, forge
    reset_config_cache()


def test_publish_step_pushes_and_prints_pr_url(publish_push_env, capsys) -> None:
    worktree, branch, forge = publish_push_env
    ctx = _publish_ctx(worktree, branch=branch)
    result = develop_publish.run(ctx, StepConfig())
    assert result.status == "done"
    assert result.markers == ["PUBLISH_DONE"]
    out = capsys.readouterr().out
    pr_lines = [ln for ln in out.splitlines() if ln.startswith("PR_URL: ")]
    assert len(pr_lines) == 1
    assert forge.pr_list(state="open", head=branch, repo="o/r")


def test_publish_step_reuses_pr_on_rerun(publish_push_env, capsys) -> None:
    worktree, branch, forge = publish_push_env
    ctx = _publish_ctx(worktree, branch=branch)
    first = develop_publish.run(ctx, StepConfig())
    out1 = capsys.readouterr().out
    assert first.markers == ["PUBLISH_DONE"]
    url1 = [ln for ln in out1.splitlines() if ln.startswith("PR_URL: ")][0]
    develop_publish.run(ctx, StepConfig())
    out2 = capsys.readouterr().out
    url2 = [ln for ln in out2.splitlines() if ln.startswith("PR_URL: ")][0]
    assert url1 == url2
    assert len(forge.pr_list(state="open", head=branch, repo="o/r")) == 1


def test_publish_step_commits_tracked_autofix_before_push(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ghdag.forge import LocalForge

    forge = LocalForge(tmp_path / "forge-push")
    _origin, worktree = _bare_origin_with_worktree(tmp_path, branch="feat/autofix")
    branch = "feat/autofix"
    (worktree / "README").write_text("feat\n", encoding="utf-8")
    _git(worktree, "add", "README")
    _git(worktree, "commit", "-m", "feat")
    _git(worktree, "push", "-u", "origin", branch)

    monkeypatch.setattr("issuesmith.ops.publish.get_forge", lambda **_k: forge)
    monkeypatch.setattr("issuesmith.ops.publish._commit_if_needed", lambda *a, **k: None)
    monkeypatch.setattr("issuesmith.ops.publish._ensure_rebased", lambda *a, **k: None)
    monkeypatch.setattr("issuesmith.ops.publish._check_commit_diff_gates", lambda *a, **k: None)
    monkeypatch.setattr("issuesmith.ops.publish._maybe_bump_version", lambda *a, **k: None)
    monkeypatch.setattr("issuesmith.ops.publish._ahead_commit_count", lambda *a, **k: 1)
    lang = get_config().language
    monkeypatch.setattr(
        develop_publish,
        "get_config",
        lambda: SimpleNamespace(
            merges_without_pr=lambda _repo: False,
            host_is_repo_checkout=False,
            root=Path("."),
            language=lang,
        ),
    )

    tracked = worktree / "README"
    tracked.write_text("autofix\n", encoding="utf-8")
    head_before = _git(worktree, "rev-parse", "HEAD").stdout.strip()
    ctx = _publish_ctx(worktree, branch=branch)
    develop_publish.run(ctx, StepConfig())
    head_after = _git(worktree, "rev-parse", "HEAD").stdout.strip()
    assert head_after != head_before
    subjects = _git(worktree, "log", "--format=%s", "-1").stdout.strip()
    assert subjects == "Apply gate auto-fixes"
    remote = _git(worktree, "rev-parse", f"origin/{branch}").stdout.strip()
    assert remote == head_after


@pytest.mark.parametrize(
    ("failing_cmd", "args"),
    [
        ("status", ("status", "--porcelain", "--untracked-files=no")),
        ("add", ("add", "-u")),
        ("commit", ("commit", "-m", "Apply gate auto-fixes")),
    ],
)
def test_autofix_commit_failure_is_broken(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failing_cmd: str,
    args: tuple[str, ...],
) -> None:
    _origin, worktree = _bare_origin_with_worktree(tmp_path)
    if failing_cmd != "status":
        (worktree / "README").write_text("x\n", encoding="utf-8")
    publish_calls: list[int] = []
    monkeypatch.setattr(
        develop_publish,
        "publish_pr",
        lambda *_a, **_k: publish_calls.append(1) or PublishResult(status="OK", pr_url="u"),
    )
    real_git = develop_publish._git

    def _fail_git(wt: Path, *git_args: str):
        if git_args[: len(args)] == args:
            return subprocess.CompletedProcess(git_args, 1, "", "fail")
        return real_git(wt, *git_args)

    monkeypatch.setattr(develop_publish, "_git", _fail_git)
    result = develop_publish.run(_publish_ctx(worktree), StepConfig())
    assert result.status == "andon"
    assert result.andon is not None
    assert result.andon.kind == "broken"
    assert result.andon.rule_id == "publish.autofix_commit_failed"
    assert publish_calls == []


_STATUS_CASES = [
    ("REBASE_CONFLICT", "blocked", "publish.rebase_conflict"),
    ("PUSH_DIVERGED", "blocked", "publish.push_diverged"),
    ("NO_DIFF", "blocked", "publish.no_diff"),
    ("DIRTY_WORKTREE", "broken", "publish.dirty_worktree"),
    ("FETCH_FAILED", "broken", "publish.fetch_failed"),
    ("P3_GATE_FAILED", "broken", "publish.diff_gate_failed"),
    ("BUMP_FAILED", "broken", "publish.bump_failed"),
    ("PUSH_FAILED", "broken", "publish.push_failed"),
    ("PR_LIST_FAILED", "broken", "publish.pr_list_failed"),
    ("PR_CREATE_FAILED", "broken", "publish.pr_create_failed"),
    ("GIT_FAILED", "broken", "publish.git_failed"),
]


@pytest.mark.parametrize("status,kind,rule_id", _STATUS_CASES)
def test_publish_status_maps_to_andon(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    kind: str,
    rule_id: str,
) -> None:
    worktree = _init_git_worktree(tmp_path)
    monkeypatch.setattr(develop_publish, "_commit_gate_autofixes", lambda _w: True)

    def _fake_publish(*_a, **_k):
        return PublishResult(status=status, stderr="detail", exit_code=1)

    monkeypatch.setattr(develop_publish, "publish_pr", _fake_publish)
    lang = get_config().language
    monkeypatch.setattr(
        develop_publish,
        "get_config",
        lambda: SimpleNamespace(
            merges_without_pr=lambda _repo: False,
            host_is_repo_checkout=False,
            root=Path("."),
            language=lang,
        ),
    )
    result = develop_publish.run(_publish_ctx(worktree), StepConfig())
    assert result.status == "andon"
    assert result.andon is not None
    assert result.andon.kind == kind
    assert result.andon.rule_id == rule_id
    assert "PUBLISH_DONE" not in (result.markers or [])


@pytest.mark.parametrize(
    "fake",
    [
        PublishResult(status="OK", pr_url="", exit_code=0),
        PublishResult(status="WEIRD", stderr="x", exit_code=1),
    ],
    ids=["ok_empty_url", "unknown_status"],
)
def test_publish_status_maps_extra_cases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake: PublishResult
) -> None:
    worktree = _init_git_worktree(tmp_path)
    monkeypatch.setattr(develop_publish, "_commit_gate_autofixes", lambda _w: True)
    monkeypatch.setattr(develop_publish, "publish_pr", lambda *_a, **_k: fake)
    lang = get_config().language
    monkeypatch.setattr(
        develop_publish,
        "get_config",
        lambda: SimpleNamespace(
            merges_without_pr=lambda _repo: False,
            host_is_repo_checkout=False,
            root=Path("."),
            language=lang,
        ),
    )
    result = develop_publish.run(_publish_ctx(worktree), StepConfig())
    assert result.andon is not None
    if fake.status == "OK":
        assert result.andon.rule_id == "publish.pr_missing"
    else:
        assert result.andon.rule_id == "publish.unknown_status"
    assert result.andon.kind == "broken"


def test_publish_failure_uses_language_pack_and_truncates_detail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worktree = _init_git_worktree(tmp_path)
    monkeypatch.setattr(develop_publish, "_commit_gate_autofixes", lambda _w: True)
    long_err = "e" * 1200
    monkeypatch.setattr(
        develop_publish,
        "publish_pr",
        lambda *_a, **_k: PublishResult(status="PUSH_FAILED", stderr=long_err, exit_code=1),
    )
    lang = get_config().language
    custom_messages = dict(lang.messages)
    custom_messages["publish.step_failed"] = "CUSTOM {status} / {detail}"
    from issuesmith.language import LanguagePack

    custom_lang = LanguagePack(
        sections=lang.sections,
        sub_design_subsections=lang.sub_design_subsections,
        sub_header_prefix=lang.sub_header_prefix,
        sub_plan_columns=lang.sub_plan_columns,
        change_table_columns=lang.change_table_columns,
        no_deps_word=lang.no_deps_word,
        delete_words=lang.delete_words,
        new_words=lang.new_words,
        removal_words=lang.removal_words,
        placeholder_words=lang.placeholder_words,
        vague_ac_words=lang.vague_ac_words,
        derived_from_phrase=lang.derived_from_phrase,
        parent_issue_label=lang.parent_issue_label,
        dependencies_table_header=lang.dependencies_table_header,
        out_of_scope_heading=lang.out_of_scope_heading,
        order_after_words=lang.order_after_words,
        messages=custom_messages,
    )
    monkeypatch.setattr(
        develop_publish,
        "get_config",
        lambda: SimpleNamespace(
            merges_without_pr=lambda _repo: False,
            host_is_repo_checkout=False,
            root=Path("."),
            language=custom_lang,
        ),
    )
    result = develop_publish.run(_publish_ctx(worktree), StepConfig())
    assert result.andon is not None
    assert result.andon.summary.startswith("CUSTOM PUSH_FAILED / ")
    assert len(result.andon.summary) < len(long_err) + 40
    assert result.andon.summary.endswith("e" * 1000)


def test_publish_step_does_not_read_previous_result() -> None:
    tree = ast.parse(_LINE_PUB.read_text(encoding="utf-8"))
    source = ast.unparse(tree)
    for forbidden in (
        "result_filename",
        "IMPL_DONE",
        "read_result",
        "m1_result",
        "p1_result",
    ):
        assert forbidden not in source


def test_new_publish_files_are_ascii_only() -> None:
    paths = (_LINE_PUB, _VERBS_PUB, _TEST_VERBS, Path(__file__))
    for path in paths:
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            assert not line_has_cjk(line), f"{path.name}:{line_no}: CJK in {path.name}"
