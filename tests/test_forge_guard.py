"""issuesmith.forge_guard: steps cannot write labels through the forge (#4807)."""
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import ghdag.forge as forge_mod
import pytest

from issuesmith.contract import LabelWriteForbidden
from issuesmith.forge_guard import ReadOnlyLabelForge


@pytest.mark.parametrize('call', [lambda f: f.issue_update(7, labels_add=['ns:develop-done']), lambda f: f.issue_update(7, labels_remove=['ns:develop-running']), lambda f: f.issue_update(7, body='x', labels_add=['ns:develop-done']), lambda f: f.add_label(7, 'ns:develop-done'), lambda f: f.remove_label(7, 'ns:develop-running')])
def test_enforce_raises_on_label_write_without_calling_inner(call):
    inner = MagicMock()
    forge = ReadOnlyLabelForge(inner, step_id='m2', mode='enforce')
    with pytest.raises(LabelWriteForbidden) as exc:
        call(forge)
    assert exc.value.step_id == 'm2'
    assert exc.value.labels
    inner.issue_update.assert_not_called()
    inner.add_label.assert_not_called()
    inner.remove_label.assert_not_called()

@pytest.mark.parametrize('mode', ['enforce', 'warn'])
def test_comments_reads_and_body_updates_pass_through(mode, capsys):
    inner = MagicMock()
    forge = ReadOnlyLabelForge(inner, step_id='cp2', mode=mode)
    forge.issue_comment(7, 'hello')
    forge.issue_get(7, fields=['labels'])
    forge.issue_update(7, body='new body')
    forge.issue_update(7, body='b', labels_add=[], labels_remove=None)
    forge.pr_list(state='open')
    inner.issue_comment.assert_called_once_with(7, 'hello')
    inner.issue_get.assert_called_once_with(7, fields=['labels'])
    assert inner.issue_update.call_count == 2
    inner.pr_list.assert_called_once_with(state='open')
    assert 'WARNING' not in capsys.readouterr().err

def test_warn_passes_label_writes_through_and_reports(capsys):
    inner = MagicMock()
    forge = ReadOnlyLabelForge(inner, step_id='m2', mode='warn')
    forge.issue_update(7, labels_add=['ns:migrate-ready'], labels_remove=['ns:merge-running'])
    forge.add_label(7, 'ns:a')
    forge.remove_label(7, 'ns:b')
    inner.issue_update.assert_called_once_with(7, labels_add=['ns:migrate-ready'], labels_remove=['ns:merge-running'])
    inner.add_label.assert_called_once_with(7, 'ns:a')
    inner.remove_label.assert_called_once_with(7, 'ns:b')
    err = capsys.readouterr().err
    assert '[issuesmith-dispatch] WARNING: step m2 wrote labels' in err
    assert 'ns:migrate-ready' in err and 'ns:merge-running' in err
    assert 'labels are projected by the runner' in err

@pytest.fixture
def step_module():
    """A loaded step module that bound the factory with ``from ghdag.forge import get_forge``."""
    mod = types.ModuleType('_issuesmith_test_step_mod')
    mod.get_forge = forge_mod.get_forge
    sys.modules[mod.__name__] = mod
    yield mod
    sys.modules.pop(mod.__name__, None)
