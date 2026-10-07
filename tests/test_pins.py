"""Unit tests for issuesmith.pins."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from packaging.version import Version

from issuesmith import pins


def test_requires_pins_parses_mapping():
    metadata = {"requires_pins": {"issuesmith": "v0.133.1"}}
    assert pins.requires_pins(metadata) == {"issuesmith": Version("0.133.1")}


def test_requires_pins_missing_returns_empty():
    assert pins.requires_pins({}) == {}


def test_requires_pins_invalid_shape_raises():
    with pytest.raises(ValueError, match="mapping"):
        pins.requires_pins({"requires_pins": ["x"]})


def test_requires_pins_invalid_version_raises():
    with pytest.raises(ValueError, match="banana"):
        pins.requires_pins({"requires_pins": {"issuesmith": "banana"}})


def test_pin_version_reads_plain_and_extras():
    text = (
        'issuesmith = "issuesmith @ git+https://github.com/sumipan/issuesmith.git@v0.133.0"\n'
        'mltgnt = "mltgnt[slack] @ git+https://github.com/sumipan/mltgnt.git@v0.123.3"\n'
    )
    assert pins.pin_version(text, "issuesmith") == Version("0.133.0")
    assert pins.pin_version(text, "mltgnt") == Version("0.123.3")
    assert pins.pin_version(text, "ghdag") is None


def test_installed_version_reads_git_describe(monkeypatch):
    proc = MagicMock(returncode=0, stdout="v0.133.1\n")
    monkeypatch.setattr(pins.subprocess, "run", lambda *a, **k: proc)
    assert pins.installed_version(Path("/var/tmp/issuesmith")) == Version("0.133.1")


def test_installed_version_nonzero_exit_returns_none(monkeypatch):
    proc = MagicMock(returncode=1, stdout="")
    monkeypatch.setattr(pins.subprocess, "run", lambda *a, **k: proc)
    assert pins.installed_version(Path("/var/tmp/issuesmith")) is None


def test_unlanded_pins_base_not_landed(monkeypatch):
    required = {"issuesmith": Version("0.133.1")}
    base = 'issuesmith = "issuesmith @ git+https://github.com/sumipan/issuesmith.git@v0.133.0"\n'
    installs = {"issuesmith": Path("/var/tmp/issuesmith")}
    proc = MagicMock(returncode=1, stdout="")
    monkeypatch.setattr(pins.subprocess, "run", lambda *a, **k: proc)
    got = pins.unlanded_pins(required, base, installs)
    assert got == ["issuesmith v0.133.1 (base=v0.133.0, installed=unknown)"]


def test_unlanded_pins_install_not_caught_up(monkeypatch):
    required = {"issuesmith": Version("0.133.1")}
    base = 'issuesmith = "issuesmith @ git+https://github.com/sumipan/issuesmith.git@v0.133.1"\n'
    proc = MagicMock(returncode=0, stdout="v0.133.0\n")
    monkeypatch.setattr(pins.subprocess, "run", lambda *a, **k: proc)
    got = pins.unlanded_pins(required, base, {"issuesmith": Path("/var/tmp/issuesmith")})
    assert got == ["issuesmith v0.133.1 (base=v0.133.1, installed=v0.133.0)"]


def test_unlanded_pins_both_landed(monkeypatch):
    required = {"issuesmith": Version("0.133.1")}
    base = 'issuesmith = "issuesmith @ git+https://github.com/sumipan/issuesmith.git@v0.133.1"\n'
    proc = MagicMock(returncode=0, stdout="v0.133.1\n")
    monkeypatch.setattr(pins.subprocess, "run", lambda *a, **k: proc)
    assert pins.unlanded_pins(required, base, {"issuesmith": Path("/var/tmp/issuesmith")}) == []


def test_unlanded_pins_same_version_is_landed():
    required = {"issuesmith": Version("0.133.1")}
    base = 'issuesmith = "issuesmith @ git+https://github.com/sumipan/issuesmith.git@v0.133.1"\n'
    assert pins.unlanded_pins(required, base, {}) == []


def test_unlanded_pins_semver_ordering():
    required = {"issuesmith": Version("0.133.10")}
    base = 'issuesmith = "issuesmith @ git+https://github.com/sumipan/issuesmith.git@v0.133.9"\n'
    got = pins.unlanded_pins(required, base, {})
    assert got == ["issuesmith v0.133.10 (base=v0.133.9, installed=unknown)"]


def test_unlanded_pins_without_installs_key_checks_pin_only():
    required = {"issuesmith": Version("0.133.1")}
    base = 'issuesmith = "issuesmith @ git+https://github.com/sumipan/issuesmith.git@v0.133.1"\n'
    assert pins.unlanded_pins(required, base, {}) == []


def test_unlanded_pins_base_none_is_unlanded():
    required = {"issuesmith": Version("0.133.1")}
    got = pins.unlanded_pins(required, None, {})
    assert got == ["issuesmith v0.133.1 (base=unknown, installed=unknown)"]
