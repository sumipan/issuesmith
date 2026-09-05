import importlib
import json
import subprocess
import sys
import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from ghdag.workflow.gates import GATE_REGISTRY, Violation

import issuesmith.gate_rules  # noqa: F401 - populates ghdag.workflow.gates.GATE_REGISTRY


def run_preflight_subprocess(*args):
    return subprocess.run(
        [sys.executable, "-m", "issuesmith", "gate-preflight", *args],
        capture_output=True,
        text=True,
    )


def run_preflight_direct(argv):
    gates_main = importlib.import_module("ghdag.workflow.gates.__main__")
    with patch("sys.argv", ["ghdag.workflow.gates"] + argv):
        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            gates_main.main()
            return mock_stdout.getvalue()


def test_unknown_gate_exits_1():
    result = run_preflight_subprocess("--gate", "unknown", "--body-file", "/dev/null")
    assert result.returncode == 1
    assert result.stderr


def test_nonexistent_body_file_exits_1():
    result = run_preflight_subprocess("--gate", "cp1", "--body-file", "/nonexistent/path")
    assert result.returncode == 1
    assert result.stderr


def test_known_gate_outputs_json_array():
    class NoopRule:
        def check(self, body: str, labels: list[str]) -> list[Violation]:
            return []

    GATE_REGISTRY["_test_noop"] = NoopRule
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("test body")
            body_path = f.name

        output = run_preflight_direct(["--gate", "_test_noop", "--body-file", body_path])
        result = json.loads(output)
        assert isinstance(result, list)
    finally:
        GATE_REGISTRY.pop("_test_noop", None)
        Path(body_path).unlink(missing_ok=True)


def test_gate_with_labels_file():
    received = {}

    class CapturingRule:
        def check(self, body: str, labels: list[str]) -> list[Violation]:
            received["body"] = body
            received["labels"] = labels
            return []

    GATE_REGISTRY["_test_capture"] = CapturingRule
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("body content")
            body_path = f.name

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("label-a\nlabel-b\n")
            labels_path = f.name

        output = run_preflight_direct([
            "--gate", "_test_capture",
            "--body-file", body_path,
            "--labels-file", labels_path,
        ])
        result = json.loads(output)
        assert isinstance(result, list)
        assert received["body"] == "body content"
        assert received["labels"] == ["label-a", "label-b"]
    finally:
        GATE_REGISTRY.pop("_test_capture", None)
        Path(body_path).unlink(missing_ok=True)
        Path(labels_path).unlink(missing_ok=True)
