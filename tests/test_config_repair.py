"""Tests for StepConfig.repair / review configuration (#4986)."""

from __future__ import annotations

import pytest
import yaml

from issuesmith.config import ConfigError, ReviewConfig, StepConfig, load_config, reset_config_cache
from issuesmith.gates import validate_step_requires


@pytest.fixture(autouse=True)
def _clear_cache():
    reset_config_cache()
    yield
    reset_config_cache()


def _write_config(tmp_path, monkeypatch, payload: dict) -> None:
    merged = {"repo": "example/app", **payload}
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(yaml.safe_dump(merged), encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()


def test_repair_max_from_yaml(tmp_path, monkeypatch) -> None:
    _write_config(
        tmp_path,
        monkeypatch,
        {"steps": {"x": {"repair": {"max": 1}}}},
    )
    assert load_config().steps["x"].repair.max == 1


def test_repair_defaults(tmp_path, monkeypatch) -> None:
    _write_config(tmp_path, monkeypatch, {"steps": {"x": {}}})
    step = load_config().steps["x"]
    assert step.repair.max == 3
    assert step.repair.push is False


def test_repair_invalid_max(tmp_path, monkeypatch) -> None:
    for bad in (-1, "a"):
        _write_config(tmp_path, monkeypatch, {"steps": {"x": {"repair": {"max": bad}}}})
        with pytest.raises(ConfigError):
            load_config()


def test_repair_invalid_push(tmp_path, monkeypatch) -> None:
    _write_config(
        tmp_path,
        monkeypatch,
        {"steps": {"x": {"repair": {"push": "yes"}}}},
    )
    with pytest.raises(ConfigError):
        load_config()


def test_review_loaded(tmp_path, monkeypatch) -> None:
    _write_config(
        tmp_path,
        monkeypatch,
        {
            "steps": {
                "x": {
                    "review": {
                        "role": "review",
                        "template": "cp2.md",
                        "success_status": "CP2_PASS",
                        "failure_status": "CP2_FAIL",
                    }
                }
            }
        },
    )
    review = load_config().steps["x"].review
    assert review is not None
    assert review.problems_heading == "Problems:"
    assert review.tier is None


def test_review_missing_template(tmp_path, monkeypatch) -> None:
    _write_config(
        tmp_path,
        monkeypatch,
        {
            "steps": {
                "x": {
                    "review": {
                        "role": "review",
                        "success_status": "CP2_PASS",
                        "failure_status": "CP2_FAIL",
                    }
                }
            }
        },
    )
    with pytest.raises(ConfigError):
        load_config()


def test_step_config_review_default_none() -> None:
    assert StepConfig().review is None


def test_validate_review_gate_requires_review_config() -> None:
    steps = {
        "p1": StepConfig(
            requires=("review",),
            input_kind="worktree",
            requires_declared=True,
        ),
    }
    with pytest.raises(ConfigError, match="steps.p1.review"):
        validate_step_requires(steps)


def test_validate_review_rejects_issue_step() -> None:
    review = ReviewConfig(
        role="review",
        template="cp2.md",
        success_status="CP2_PASS",
        failure_status="CP2_FAIL",
    )
    steps = {
        "p1": StepConfig(
            requires=("review",),
            input_kind="issue",
            requires_declared=True,
            review=review,
        ),
    }
    with pytest.raises(ConfigError, match="worktree gate"):
        validate_step_requires(steps)


def _review_payload(**extra) -> dict:
    return {
        "steps": {
            "cp2": {
                "review": {
                    "role": "review",
                    "template": "cp2.md",
                    "success_status": "CP2_PASS",
                    "failure_status": "CP2_FAIL",
                    **extra,
                }
            }
        }
    }


def test_review_optional_variables_loaded(tmp_path, monkeypatch) -> None:
    _write_config(
        tmp_path,
        monkeypatch,
        _review_payload(optional_variables=["execution_constraints"]),
    )
    review = load_config().steps["cp2"].review
    assert review is not None
    assert review.optional_variables == ("execution_constraints",)


def test_review_optional_variables_keeps_order(tmp_path, monkeypatch) -> None:
    _write_config(
        tmp_path,
        monkeypatch,
        _review_payload(optional_variables=["b", "a", "b"]),
    )
    review = load_config().steps["cp2"].review
    assert review is not None
    assert review.optional_variables == ("b", "a", "b")


def test_review_optional_variables_default_empty(tmp_path, monkeypatch) -> None:
    _write_config(tmp_path, monkeypatch, _review_payload())
    review = load_config().steps["cp2"].review
    assert review is not None
    assert review.optional_variables == ()


@pytest.mark.parametrize(
    "bad",
    [[""], ["  "], [1], "execution_constraints", {"execution_constraints": ""}],
)
def test_review_optional_variables_invalid(tmp_path, monkeypatch, bad) -> None:
    _write_config(tmp_path, monkeypatch, _review_payload(optional_variables=bad))
    with pytest.raises(ConfigError, match=r"steps\.cp2\.review\.optional_variables"):
        load_config()
