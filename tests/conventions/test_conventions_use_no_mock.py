"""The structural tests themselves must not mock (OSS_QUALITY §8.6)."""
from __future__ import annotations

from pathlib import Path


def test_no_unittest_mock_in_conventions():
    here = Path(__file__).resolve().parent
    banned = ("unittest" + ".mock", "monkeypatch" + ".setattr")
    for path in here.glob("test_*.py"):
        if path.name == Path(__file__).name:
            continue
        text = path.read_text(encoding="utf-8")
        assert not any(b in text for b in banned), path.name
