"""Config loading for paths.concurrency overlay (#8001)."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from issuesmith.config import ConcurrencyConfig, ConfigError, load_config, reset_config_cache


@pytest.fixture(autouse=True)
def _clear_cache():
    reset_config_cache()
    yield
    reset_config_cache()


def _write_config(
    tmp_path: Path,
    *,
    concurrency: dict | None = None,
    paths: dict | None = None,
    overlay: str | None = None,
    overlay_rel: str = "configs/concurrency.yaml",
) -> Path:
    data: dict = {"repo": "org/repo"}
    if concurrency is not None:
        data["concurrency"] = concurrency
    if paths is not None:
        data["paths"] = paths
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    if overlay is not None:
        overlay_path = tmp_path / overlay_rel
        overlay_path.parent.mkdir(parents=True, exist_ok=True)
        overlay_path.write_text(overlay, encoding="utf-8")
    return cfg_path


def test_no_paths_concurrency_keeps_config_values(tmp_path: Path, monkeypatch):
    cfg_path = _write_config(
        tmp_path,
        concurrency={"default": 2, "per_engine": {"claude": 3}},
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    cfg = load_config(cfg_path)
    assert cfg.paths.concurrency is None
    assert cfg.concurrency == ConcurrencyConfig(
        default=2,
        per_engine={"claude": 3},
        strict_order=False,
        max_dispatch_per_tick=1,
    )


def test_overlay_per_engine_merges_per_engine(tmp_path: Path, monkeypatch):
    cfg_path = _write_config(
        tmp_path,
        concurrency={"per_engine": {"claude": 3, "cursor": 2}},
        paths={"concurrency": "configs/concurrency.yaml"},
        overlay="per_engine:\n  cursor: 1\n",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    cfg = load_config(cfg_path)
    assert cfg.concurrency.per_engine == {"claude": 3, "cursor": 1}


def test_overlay_scalars_override(tmp_path: Path, monkeypatch):
    cfg_path = _write_config(
        tmp_path,
        concurrency={
            "default": 1,
            "strict_order": False,
            "max_dispatch_per_tick": 1,
        },
        paths={"concurrency": "configs/concurrency.yaml"},
        overlay=(
            "default: 4\n"
            "strict_order: true\n"
            "max_dispatch_per_tick: 3\n"
        ),
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    cfg = load_config(cfg_path)
    assert cfg.concurrency.default == 4
    assert cfg.concurrency.strict_order is True
    assert cfg.concurrency.max_dispatch_per_tick == 3


def test_missing_overlay_file_uses_config(tmp_path: Path, monkeypatch):
    cfg_path = _write_config(
        tmp_path,
        concurrency={"default": 2, "per_engine": {"codex": 1}},
        paths={"concurrency": "configs/concurrency.yaml"},
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    cfg = load_config(cfg_path)
    assert cfg.concurrency == ConcurrencyConfig(
        default=2,
        per_engine={"codex": 1},
        strict_order=False,
        max_dispatch_per_tick=1,
    )


def test_overlay_not_mapping_raises(tmp_path: Path, monkeypatch):
    cfg_path = _write_config(
        tmp_path,
        paths={"concurrency": "configs/concurrency.yaml"},
        overlay="- 1\n",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    with pytest.raises(ConfigError):
        load_config(cfg_path)


def test_overlay_per_engine_non_int_raises(tmp_path: Path, monkeypatch):
    cfg_path = _write_config(
        tmp_path,
        paths={"concurrency": "configs/concurrency.yaml"},
        overlay="per_engine:\n  claude: many\n",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    with pytest.raises(ConfigError):
        load_config(cfg_path)


def test_overlay_unknown_key_raises(tmp_path: Path, monkeypatch):
    cfg_path = _write_config(
        tmp_path,
        paths={"concurrency": "configs/concurrency.yaml"},
        overlay="engines: {}\n",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    with pytest.raises(ConfigError):
        load_config(cfg_path)
