"""Acceptance: issuesmith constructs forge clients via get_forge() (#3102 / #3066 サブ4)."""

from __future__ import annotations

import ast
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1] / "src" / "issuesmith"


def _iter_py_files(root: Path):
    yield from sorted(root.rglob("*.py"))


def test_no_github_client_construction_in_src():
    """GitHubClient( must not appear in issuesmith src (factory-only construction)."""
    offenders: list[str] = []
    for path in _iter_py_files(SRC_ROOT):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = None
            if isinstance(func, ast.Name):
                name = func.id
            elif isinstance(func, ast.Attribute):
                name = func.attr
            if name == "GitHubClient":
                rel = path.relative_to(SRC_ROOT.parent.parent)
                offenders.append(f"{rel}:{node.lineno}")
    assert offenders == [], f"GitHubClient( remains at: {offenders}"


def test_get_forge_imported_where_clients_are_built():
    """Modules that previously constructed clients must import get_forge."""
    # Spot-check high-traffic modules listed in #3066 サブ4.
    modules = [
        "dep_extractor.py",
        "queue.py",
        "milestone.py",
        "recovery.py",
        "ops/dispatch.py",
        "ops/smoke.py",
        "ops/publish.py",
        "context_hook.py",
        "steps/m2_finalize.py",
    ]
    for rel in modules:
        text = (SRC_ROOT / rel).read_text(encoding="utf-8")
        assert "get_forge" in text, f"{rel} must use get_forge"


def test_dep_extractor_rescue_uses_pr_get_not_api_request():
    """The raw api_request for pulls/{n} must move to typed pr_get."""
    from issuesmith import dep_extractor as de

    src = Path(de.__file__).read_text(encoding="utf-8")
    # Construction site for rescue PR detail fetch
    assert "pr_get(" in src
    assert 'api_request("GET"' not in src
    assert "api_request(f\"/repos" not in src


def test_check_dependencies_uses_get_forge_when_client_omitted(monkeypatch):
    from issuesmith import dep_extractor as de

    calls: list[dict] = []

    class FakeForge:
        def issue_get(self, number, fields=None):
            return {
                "state": "CLOSED",
                "title": "done",
                "labels": [{"name": "issuesmith:merge-done"}],
            }

    def fake_get_forge(**kwargs):
        calls.append(kwargs)
        return FakeForge()

    monkeypatch.setattr(de, "get_forge", fake_get_forge)
    result = de.check_dependencies([1])
    assert result.decision == "PASS"
    assert calls == [{}]


def test_rescue_pr_uses_pr_get_merged_flag():
    from unittest.mock import MagicMock

    from issuesmith.dep_extractor import check_dependencies

    client = MagicMock()
    client.issue_get.return_value = {
        "state": "CLOSED",
        "title": "legacy closed",
        "labels": [{"name": "issuesmith:develop-done"}],
    }
    client.issue_timeline.return_value = [
        {
            "event": "cross-referenced",
            "source": {
                "issue": {"number": 42, "pull_request": {"url": "..."}},
            },
        }
    ]
    client.pr_get.return_value = {"merged": True, "state": "CLOSED"}

    result = check_dependencies([600], client=client)
    assert result.decision == "PASS"
    assert result.dep_statuses[0].rescue_pr == 42
    client.pr_get.assert_called_once_with(42)
    client.api_request.assert_not_called()
