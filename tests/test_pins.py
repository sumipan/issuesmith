"""Unit tests for issuesmith.pins."""

from __future__ import annotations

from importlib.metadata import version as dist_version
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from packaging.version import Version

from issuesmith import pins
from issuesmith.config import INSTALLS_ENV


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


def test_unlanded_pins_env_install_landed():
    """Refs sumipan/nexus#7096 acceptance: @env with satisfied pin returns no unlanded."""
    required = {"packaging": Version("1.0.0")}
    base = 'packaging = "packaging @ git+https://example.invalid/packaging.git@v1.0.0"\n'
    installs = {"packaging": INSTALLS_ENV}
    assert pins.unlanded_pins(required, base, installs) == []


def test_unlanded_pins_env_install_lower():
    """Refs sumipan/nexus#7096 acceptance: @env below required pin is unlanded."""
    env_ver = Version(dist_version("packaging"))
    required = {"packaging": Version("9999.0.0")}
    base = 'packaging = "packaging @ git+https://example.invalid/packaging.git@v9999.0.0"\n'
    installs = {"packaging": INSTALLS_ENV}
    got = pins.unlanded_pins(required, base, installs)
    assert got == [
        f"packaging v9999.0.0 (base=v9999.0.0, installed=v{env_ver})",
    ]


def test_pin_version_reads_lower_bound():
    text = 'dependencies = ["ghdag>=1.2.3"]\n'
    assert pins.pin_version(text, "ghdag") == Version("1.2.3")
    text_extras = 'optional = ["ghdag[x] >= 1.2.3"]\n'
    assert pins.pin_version(text_extras, "ghdag") == Version("1.2.3")


def test_pin_version_prefers_url_over_lower_bound():
    text = (
        'ghdag = "ghdag @ git+https://github.com/sumipan/ghdag.git@v2.0.0"\n'
        'other = "ghdag>=1.2.3"\n'
    )
    assert pins.pin_version(text, "ghdag") == Version("2.0.0")


def test_lower_bound_version_ignores_other_package_prefix():
    text = 'dep = "ghdag-foo>=9.9.9"\n'
    assert pins.lower_bound_version(text, "ghdag") is None


def test_unlanded_pins_lower_bound_landed():
    required = {"ghdag": Version("1.2.0")}
    base = 'ghdag = "ghdag>=1.3.0"\n'
    assert pins.unlanded_pins(required, base, {}) == []


def test_unlanded_pins_lower_bound_below_required():
    required = {"ghdag": Version("1.2.0")}
    base = 'ghdag = "ghdag>=1.1.0"\n'
    got = pins.unlanded_pins(required, base, {})
    assert got == ["ghdag v1.2.0 (base=v1.1.0, installed=unknown)"]


def test_unlanded_pins_env_install_missing():
    """Refs sumipan/nexus#7096 acceptance: @env for absent package is installed=unknown."""
    pkg = "issuesmith-nonexistent-package-7096"
    required = {pkg: Version("1.0.0")}
    base = (
        f'{pkg} = "{pkg} @ git+https://example.invalid/{pkg}.git@v1.0.0"\n'
    )
    installs = {pkg: INSTALLS_ENV}
    got = pins.unlanded_pins(required, base, installs)
    assert got == [f"{pkg} v1.0.0 (base=v1.0.0, installed=unknown)"]
