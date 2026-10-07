"""
Shared pytest fixtures for the issuesmith test suite.
"""

import builtins
import contextlib
import dataclasses
import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

# Keep the suite independent of the host config the runner inherits (a host
# language_pack must not leak into tests). Popped before any module calls get_config().
os.environ.pop("ISSUESMITH_CONFIG", None)

import issuesmith.config as config_module  # noqa: E402
from issuesmith.language import EN  # noqa: E402
from tests import legacy_text  # noqa: E402

NEXUS_TEST_PHASES = [
    {
        "name": "draft",
        "role": "design",
        "entry_step": "b1",
        "handler": "brushup",
        "steps": ["b1"],
        "writes_files": False,
        "advance_when": ["deps_terminal"],
    },
    {
        "name": "sub",
        "role": "implementation",
        "entry_step": "sub-ready",
        "handler": "subissue",
        "steps": ["sub1"],
        "writes_files": False,
        "preconditions": ["draft-done", "scope:milestone"],
        "advance_when": ["deps_terminal"],
    },
    {
        "name": "develop",
        "role": "implementation",
        "entry_step": "cp2",
        "handler": "impl",
        "steps": ["p0", "p1", "p3", "cp2"],
        "preconditions": ["draft-done"],
        "excludes": ["scope:milestone"],
        "advance_when": ["deps_terminal"],
    },
    {
        "name": "merge",
        "role": "implementation",
        "entry_step": "m2",
        "handler": "merge",
        "steps": ["m1", "m2-role-dispatch"],
        "advance_when": ["deps_terminal", "closing_pr_exists"],
    },
]

# Nexus declaration without explicit ``steps`` so entry_step stays the final step (#4790).
NEXUS_DECLARATION_PHASES = [
    {key: value for key, value in phase.items() if key != "steps"}
    for phase in NEXUS_TEST_PHASES
]

_NEXUS_CONFIG_PAYLOAD = {
    "repo": "sumipan/nexus",
    "supported_repos": [
        "sumipan/nexus",
        "sumipan/mltgnt",
        "sumipan/mltgnt-vscode-extension",
        "sumipan/ghdag",
        "sumipan/slack-project",
        "sumipan/diary",
        "sumipan/nexus-companion",
        "sumipan/okr-core",
        "sumipan/issuesmith",
    ],
    "phases": NEXUS_DECLARATION_PHASES,
    "terminal_labels": ["issuesmith:merge-done", "bump:done"],
    "terminal_without_merge": [
        "rejected",
        "superseded",
        "sub-ready",
        "sub-done",
    ],
    "steps": {"p1": {"andon_when": ["external_leak.target_unknown"]}},
}

_NEXUS_CONFIG_MODULES = frozenset({
    "tests.test_preconditions",
    "tests.test_queue",
    "tests.test_queue_config_driven",
    "tests.test_queue_language_pack",
    "tests.test_queue_ordering",
    "tests.test_queue_redispatch",
    "tests.test_queue_triage",
    "tests.test_queue_sub_phase",
    "tests.test_recovery",
    "tests.test_redispatch_sub",
    "tests.test_resume_restores_state",
    "tests.test_dispatch_requires",
    "tests.test_second_consumer_declaration",
})


def _write_nexus_config(tmp_path: Path) -> Path:
    cfg_path = tmp_path / "issuesmith-nexus.yaml"
    cfg_path.write_text(yaml.safe_dump(_NEXUS_CONFIG_PAYLOAD), encoding="utf-8")
    return cfg_path


_ENGLISH_SECTIONS = dict(EN.sections)
_ENGLISH_SUBSECTIONS = EN.sub_design_subsections

# The EN pack with the legacy sub-header prefix and change-table columns: modules not
# yet migrated to the language pack (scope_size, milestone_consistency, sub1_create, ...)
# still write and read those words, and their tests build bodies from tests.legacy_text.
# Drop the overrides once every module reads the vocabulary from the pack (#4347).
TEST_LANGUAGE_PACK = dataclasses.replace(
    EN,
    sub_header_prefix=legacy_text.SUB,
    change_table_columns=(
        legacy_text.REPOSITORY,
        legacy_text.FILE_PATH,
        legacy_text.CHANGE_TYPE,
        legacy_text.DESCRIPTION,
    ),
)


def _is_write_mode(mode: str) -> bool:
    return bool(set(mode) & set("wax+"))


@pytest.fixture(autouse=True)
def _issuesmith_config_for_tests(request, monkeypatch, tmp_path):
    """Apply the nexus declaration only to modules that exercise config-driven queue logic."""
    if request.node.get_closest_marker("no_auto_phases"):
        monkeypatch.delenv("ISSUESMITH_CONFIG", raising=False)
    elif request.module.__name__ in _NEXUS_CONFIG_MODULES:
        monkeypatch.setenv("ISSUESMITH_CONFIG", str(_write_nexus_config(tmp_path)))
    else:
        monkeypatch.delenv("ISSUESMITH_CONFIG", raising=False)
    config_module.reset_config_cache()
    yield
    config_module.reset_config_cache()


@pytest.fixture(autouse=True)
def _redirect_metrics_paths(english_section_defaults, monkeypatch, tmp_path_factory):
    """Route metrics and exec.jsonl to pytest basetemp; set METRICS_JSONL_PATH."""
    basetemp = tmp_path_factory.getbasetemp().resolve()
    metrics_path = basetemp / "metrics.jsonl"
    exec_path = basetemp / "exec.jsonl"
    monkeypatch.setenv("METRICS_JSONL_PATH", str(metrics_path))
    config_module.reset_config_cache()
    cfg = config_module.get_config()
    redirected = dataclasses.replace(
        cfg,
        paths=dataclasses.replace(
            cfg.paths,
            metrics=metrics_path,
            exec_jsonl=exec_path,
        ),
    )
    monkeypatch.setattr(config_module, "_cached", redirected)
    yield


@pytest.fixture(autouse=True)
def english_section_defaults(monkeypatch):
    """Exercise section parsing with the EN pack's ASCII section names."""
    monkeypatch.setattr(config_module, "EN", TEST_LANGUAGE_PACK)
    config_module.reset_config_cache()
    yield
    config_module.reset_config_cache()


@pytest.fixture(autouse=True)
def no_gh_fetch(request):
    """By default, mock _fetch_issue_body_from_gh to return None.

    Prevents tests that use a local design.md from accidentally fetching
    a real GitHub Issue (no gh CLI dependency). Override with patch when
    explicitly testing gh fetch behavior.
    """
    if "gh_fetch" in request.keywords:
        yield
        return
    with (
        patch("issuesmith.context_hook._fetch_issue_body_from_gh", return_value=None),
        patch("issuesmith.context_hook._fetch_issue_comments_from_api", return_value=[]),
    ):
        yield


_WRITE_MODES = frozenset({"w", "a", "x", "r+", "wb", "ab", "xb", "r+b"})


@pytest.fixture(autouse=True, scope="session")
def _no_side_effects(tmp_path_factory):
    """Guard against I/O writes outside pytest's tmp directory."""
    import issuesmith.queue as _qmod
    import issuesmith.queue_triage as _qtriage

    basetemp = tmp_path_factory.getbasetemp().resolve()

    def _safe(path) -> bool:
        if isinstance(path, int):
            return True
        try:
            return Path(path).resolve().is_relative_to(basetemp)
        except (TypeError, ValueError, OSError):
            return False

    def _guard(path) -> None:
        if not _safe(path):
            raise AssertionError(f"side effect outside tmp: {path}")

    _real_open = builtins.open

    def _open_guard(file, mode="r", *args, **kwargs):
        if mode in _WRITE_MODES:
            _guard(file)
        return _real_open(file, mode, *args, **kwargs)

    _Path_write_text = Path.write_text

    def _write_text(self, *args, **kwargs):
        _guard(self)
        return _Path_write_text(self, *args, **kwargs)

    _Path_write_bytes = Path.write_bytes

    def _write_bytes(self, *args, **kwargs):
        _guard(self)
        return _Path_write_bytes(self, *args, **kwargs)

    _Path_mkdir = Path.mkdir

    def _mkdir(self, *args, **kwargs):
        # Allow mkdir(exist_ok=True) on an already-existing dir — it is a no-op.
        if kwargs.get("exist_ok") and self.is_dir():
            return _Path_mkdir(self, *args, **kwargs)
        _guard(self)
        return _Path_mkdir(self, *args, **kwargs)

    _Path_touch = Path.touch

    def _touch(self, *args, **kwargs):
        _guard(self)
        return _Path_touch(self, *args, **kwargs)

    _Path_unlink = Path.unlink

    def _unlink(self, *args, **kwargs):
        _guard(self)
        return _Path_unlink(self, *args, **kwargs)

    _Path_rename = Path.rename

    def _rename_path(self, target, *args, **kwargs):
        _guard(self)
        _guard(target)
        return _Path_rename(self, target, *args, **kwargs)

    _Path_open = Path.open

    def _path_open(self, mode="r", *args, **kwargs):
        if _is_write_mode(mode):
            _guard(self)
        return _Path_open(self, mode, *args, **kwargs)

    _os_makedirs = os.makedirs

    def _makedirs(name, *args, **kwargs):
        # Allow makedirs(exist_ok=True) on an already-existing dir — it is a no-op.
        exist_ok = kwargs.get("exist_ok") or (len(args) >= 2 and args[1])
        if exist_ok and Path(name).is_dir():
            return _os_makedirs(name, *args, **kwargs)
        _guard(name)
        return _os_makedirs(name, *args, **kwargs)

    _os_mkdir = os.mkdir

    def _os_mkdir_guard(path, *args, **kwargs):
        # Allow os.mkdir on an already-existing directory — it is a no-op
        # (raises FileExistsError which callers with exist_ok handle).
        if Path(path).is_dir():
            return _os_mkdir(path, *args, **kwargs)
        _guard(path)
        return _os_mkdir(path, *args, **kwargs)

    _os_remove = os.remove

    def _remove(path, *args, **kwargs):
        _guard(path)
        return _os_remove(path, *args, **kwargs)

    _os_rename = os.rename

    def _os_rename_guard(src, dst, *args, **kwargs):
        _guard(src)
        _guard(dst)
        return _os_rename(src, dst, *args, **kwargs)

    _os_replace = os.replace

    def _replace(src, dst, *args, **kwargs):
        _guard(src)
        _guard(dst)
        return _os_replace(src, dst, *args, **kwargs)

    _shutil_copy = shutil.copy

    def _copy(src, dst, *args, **kwargs):
        _guard(dst)
        return _shutil_copy(src, dst, *args, **kwargs)

    _shutil_copy2 = shutil.copy2

    def _copy2(src, dst, *args, **kwargs):
        _guard(dst)
        return _shutil_copy2(src, dst, *args, **kwargs)

    _shutil_copytree = shutil.copytree

    def _copytree(src, dst, *args, **kwargs):
        _guard(dst)
        return _shutil_copytree(src, dst, *args, **kwargs)

    _shutil_move = shutil.move

    def _move(src, dst, *args, **kwargs):
        _guard(dst)
        return _shutil_move(src, dst, *args, **kwargs)

    _shutil_rmtree = shutil.rmtree

    def _rmtree(path, *args, **kwargs):
        _guard(path)
        return _shutil_rmtree(path, *args, **kwargs)

    _NTF = tempfile.NamedTemporaryFile

    def _ntf(*args, **kwargs):
        if kwargs.get("delete") is False:
            dir_ = kwargs.get("dir")
            if dir_ is None:
                # Redirect to basetemp so the file stays inside tmp and can be unlinked.
                kwargs["dir"] = basetemp
            elif not _safe(dir_):
                raise AssertionError(
                    f"side effect outside tmp: NamedTemporaryFile(delete=False, dir={dir_!r})"
                )
        return _NTF(*args, **kwargs)

    with contextlib.ExitStack() as stack:
        # Redirect runtime-data paths so dispatch/triage/quota writes go to basetemp.
        stack.enter_context(patch.dict(os.environ, {"ISSUESMITH_QUEUE_DIR": str(basetemp)}))
        stack.enter_context(patch.object(_qmod, "QUOTA_STATE_PATH", new=basetemp / "quota-gate.json"))
        stack.enter_context(patch.object(_qmod, "BRAKE_STATE_PATH", new=basetemp / "brake.json"))
        stack.enter_context(patch.object(_qmod, "EXEC_PATH", new=basetemp / "exec.jsonl"))
        stack.enter_context(patch.object(_qmod, "DONE_DIR", new=basetemp / "done"))
        stack.enter_context(patch.object(_qmod, "JOBS_DIR", new=basetemp))
        stack.enter_context(patch.object(_qtriage, "DEFAULT_TRIAGE_LOG_PATH", new=basetemp / "issuesmith-triage.jsonl"))
        # I/O write guard.
        stack.enter_context(patch("builtins.open", _open_guard))
        stack.enter_context(patch.object(Path, "write_text", _write_text))
        stack.enter_context(patch.object(Path, "write_bytes", _write_bytes))
        stack.enter_context(patch.object(Path, "mkdir", _mkdir))
        stack.enter_context(patch.object(Path, "touch", _touch))
        stack.enter_context(patch.object(Path, "unlink", _unlink))
        stack.enter_context(patch.object(Path, "rename", _rename_path))
        stack.enter_context(patch.object(Path, "open", _path_open))
        stack.enter_context(patch("os.makedirs", _makedirs))
        stack.enter_context(patch("os.mkdir", _os_mkdir_guard))
        stack.enter_context(patch("os.remove", _remove))
        stack.enter_context(patch("os.rename", _os_rename_guard))
        stack.enter_context(patch("os.replace", _replace))
        stack.enter_context(patch("shutil.copy", _copy))
        stack.enter_context(patch("shutil.copy2", _copy2))
        stack.enter_context(patch("shutil.copytree", _copytree))
        stack.enter_context(patch("shutil.move", _move))
        stack.enter_context(patch("shutil.rmtree", _rmtree))
        stack.enter_context(patch("tempfile.NamedTemporaryFile", _ntf))
        yield

    repo_root = Path(__file__).parent.parent
    for d in ("jobs", "logs", ".pipeline-state"):
        assert not (repo_root / d).exists(), (
            f"side effect: {d}/ was created at repo root during test session"
        )
