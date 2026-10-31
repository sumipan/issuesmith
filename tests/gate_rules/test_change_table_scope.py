"""Tests for change_table_scope gate rule (#5589)."""

from __future__ import annotations

import pytest
import yaml

import issuesmith.gate_rules.change_table_scope  # noqa: F401 — registers on import
from issuesmith.config import reset_config_cache
from issuesmith.gate_rules import GATE_REGISTRY
from issuesmith.gate_rules.change_table_scope import ChangeTableScopeRules
from tests import legacy_text as lt

_TABLE_HEADER = (
    "### Change targets\n\n"
    f"| {lt.REPOSITORY} | {lt.FILE_PATH} | {lt.CHANGE_TYPE} | {lt.DESCRIPTION} |\n"
    "|---|---|---|---|\n"
)


def _yaml_block(
    *,
    allow_paths: list[str] | None = None,
    host_allow_paths: list[str] | None = None,
    target_repo: str = "sumipan/issuesmith",
) -> str:
    lines = [
        "```yaml",
        f"target_repo: {target_repo}",
        "base_branch: main",
    ]
    if allow_paths is not None:
        lines.append("allow_paths:")
        for p in allow_paths:
            lines.append(f"  - {p!r}")
    if host_allow_paths is not None:
        lines.append("host_allow_paths:")
        for p in host_allow_paths:
            lines.append(f"  - {p!r}")
    lines.append("```")
    return "\n".join(lines)


def _body(
    yaml_part: str,
    row: str,
    *,
    extra: str = "",
) -> str:
    return f"{yaml_part}\n\n## Design\n\nscope\n\n{_TABLE_HEADER}{row}{extra}"


@pytest.fixture(autouse=True)
def _config(tmp_path, monkeypatch):
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump({"repo": "sumipan/nexus", "scope_gate": {"enabled": False}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()
    yield
    reset_config_cache()


def _check(body: str, labels: list[str] | None = None):
    return ChangeTableScopeRules().check(body, labels or [])


def test_registered_in_gate_registry():
    assert "change_table_scope" in GATE_REGISTRY


def test_target_row_exact_match_passes():
    yaml_part = _yaml_block(allow_paths=["src/foo.py"])
    body = _body(
        yaml_part,
        "| `sumipan/issuesmith` | `src/foo.py` | modify | change |\n",
    )
    assert _check(body) == []


def test_target_row_glob_match_passes():
    yaml_part = _yaml_block(allow_paths=["src/**"])
    body = _body(
        yaml_part,
        "| `sumipan/issuesmith` | `src/pkg/mod.py` | modify | change |\n",
    )
    assert _check(body) == []


def test_target_row_dot_slash_normalized():
    yaml_part = _yaml_block(allow_paths=["./CHANGELOG.md"])
    body = _body(
        yaml_part,
        "| `sumipan/issuesmith` | `CHANGELOG.md` | modify | note |\n",
    )
    assert _check(body) == []


def test_target_row_outside_allow_paths_violation():
    yaml_part = _yaml_block(allow_paths=["src/foo.py"])
    body = _body(
        yaml_part,
        "| `sumipan/issuesmith` | `src/bar.py` | modify | change |\n",
    )
    violations = _check(body)
    assert len(violations) == 1
    v = violations[0]
    assert v.rule_id == "change_table_scope.outside_allow_paths"
    assert v.severity == "fail"
    assert v.auto_fixable is True
    assert v.location == "src/bar.py"
    assert "allow_paths" in (v.fix_hint or "")


def test_empty_allow_paths_every_target_row_fails():
    yaml_part = _yaml_block(allow_paths=[])
    body = _body(
        yaml_part,
        "| `sumipan/issuesmith` | `src/a.py` | modify | a |\n"
        "| `sumipan/issuesmith` | `src/b.py` | modify | b |\n",
    )
    violations = _check(body)
    assert len(violations) == 2
    assert {v.location for v in violations} == {"src/a.py", "src/b.py"}


def test_host_row_uses_host_allow_paths():
    yaml_part = _yaml_block(
        allow_paths=["src/foo.py"],
        host_allow_paths=["workflows/issuesmith.yml"],
    )
    body = _body(
        yaml_part,
        "| `sumipan/nexus` | `workflows/issuesmith.yml` | modify | cfg |\n",
    )
    assert _check(body) == []


def test_host_row_missing_host_key_fails():
    yaml_part = _yaml_block(allow_paths=["src/foo.py"])
    body = _body(
        yaml_part,
        "| `sumipan/nexus` | `workflows/issuesmith.yml` | modify | cfg |\n",
    )
    violations = _check(body)
    assert len(violations) == 1
    assert "host_allow_paths" in (violations[0].fix_hint or "")


def test_unknown_repository_row_fails():
    yaml_part = _yaml_block(allow_paths=["**"])
    body = _body(
        yaml_part,
        "| `sumipan/ghdag` | `src/x.py` | modify | x |\n",
    )
    violations = _check(body)
    assert len(violations) == 1
    assert "sumipan/ghdag" in violations[0].message
    assert "target_repo" in (violations[0].fix_hint or "")


def test_delete_and_move_rows_are_checked():
    yaml_part = _yaml_block(allow_paths=["src/old.py"])
    body = _body(
        yaml_part,
        "| `sumipan/issuesmith` | `src/old.py` | delete | rm |\n"
        "| `sumipan/issuesmith` | `src/new.py` | move | mv |\n",
    )
    violations = _check(body)
    assert len(violations) == 1
    assert violations[0].location == "src/new.py"


def test_no_change_table_returns_empty():
    yaml_part = _yaml_block(allow_paths=["src/foo.py"])
    body = f"{yaml_part}\n\n## Design\n\nno table here\n"
    assert _check(body) == []


def test_unparseable_yaml_returns_empty():
    body = "## Design\n\n```yaml\n: bad\n```\n"
    assert _check(body) == []


def test_missing_target_repo_returns_empty():
    body = _body(
        "```yaml\nbase_branch: main\nallow_paths:\n  - src/a.py\n```",
        "| `sumipan/issuesmith` | `src/a.py` | modify | a |\n",
    )
    assert _check(body) == []


def test_milestone_label_skips():
    yaml_part = _yaml_block(allow_paths=[])
    body = _body(
        yaml_part,
        "| `sumipan/issuesmith` | `src/a.py` | modify | a |\n",
    )
    assert _check(body, ["scope:milestone"]) == []


def test_legacy_row_without_repo_column():
    yaml_part = _yaml_block(allow_paths=["src/legacy.py"])
    body = (
        f"{yaml_part}\n\n## Design\n\n"
        f"| {lt.FILE_PATH} | {lt.CHANGE_TYPE} | {lt.DESCRIPTION} |\n"
        "|---|---|---|\n"
        "| `src/legacy.py` | modify | legacy |\n"
    )
    assert _check(body) == []
