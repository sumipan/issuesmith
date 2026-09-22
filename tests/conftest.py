"""
Shared pytest fixtures for the issuesmith test suite.
"""

import builtins
import contextlib
import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

import issuesmith.config as config_module

_ENGLISH_SECTIONS = {
    "acceptance_criteria": "Acceptance Criteria",
    "migration": "Migration Steps",
    "migration_state_survey": "Runtime State Survey",
    "sub_plan": "Sub-issue Plan",
    "design": "Design",
    "background": "Background",
    "dependencies": "Dependencies",
    "impact_survey": "Impact Survey",
    "milestone": "Milestone",
    "changed_files": "Changed Files",
}
_ENGLISH_SUBSECTIONS = ("Scope", "Design Policy", "Changed Files", "Acceptance Criteria")


@pytest.fixture(autouse=True)
def english_section_defaults(monkeypatch):
    """Exercise section parsing with ASCII-only configured names."""
    monkeypatch.setattr(config_module, "_DEFAULT_SECTIONS", _ENGLISH_SECTIONS)
    monkeypatch.setattr(
        config_module,
        "_DEFAULT_SUB_DESIGN_SUBSECTIONS",
        _ENGLISH_SUBSECTIONS,
    )
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
