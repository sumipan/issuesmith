import json
import subprocess
import sys
import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import issuesmith.gate_rules  # noqa: F401 - populates issuesmith.gate_registry.GATE_REGISTRY
from issuesmith.cli import _run_gate_preflight
from issuesmith.gate_registry import GATE_REGISTRY, Violation
from issuesmith.ops.preflight import check_post_merge


def run_preflight_subprocess(*args):
    return subprocess.run(
        [sys.executable, "-m", "issuesmith", "gate-preflight", *args],
        capture_output=True,
        text=True,
    )


def run_preflight_direct(argv):
    with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
        rc = _run_gate_preflight(argv)
        return mock_stdout.getvalue(), rc


def test_unknown_gate_exits_1():
    result = run_preflight_subprocess("--gate", "nope", "--body-file", "/dev/null")
    assert result.returncode == 1
    assert "Error: gate 'nope' is not registered" in result.stderr


def test_nonexistent_body_file_exits_1():
    missing = "/nonexistent/path/to/body.md"
    result = run_preflight_subprocess(
        "--gate", "ac_format", "--body-file", missing,
    )
    assert result.returncode == 1
    assert f"Error: body file '{missing}' does not exist" in result.stderr


def test_known_gate_outputs_json_array():
    class NoopRule:
        def check(self, body: str, labels: list[str]) -> list[Violation]:
            return []

    GATE_REGISTRY["_test_noop"] = NoopRule
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("test body")
            body_path = f.name

        output, rc = run_preflight_direct(["--gate", "_test_noop", "--body-file", body_path])
        assert rc == 0
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

        output, rc = run_preflight_direct([
            "--gate", "_test_capture",
            "--body-file", body_path,
            "--labels-file", labels_path,
        ])
        assert rc == 0
        result = json.loads(output)
        assert isinstance(result, list)
        assert received["body"] == "body content"
        assert received["labels"] == ["label-a", "label-b"]
    finally:
        GATE_REGISTRY.pop("_test_capture", None)
        Path(body_path).unlink(missing_ok=True)
        Path(labels_path).unlink(missing_ok=True)


def test_check_post_merge_tag_fails_with_recovery_command():
    with patch("issuesmith.ops.preflight.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        results = check_post_merge(
            [{"kind": "tag", "repo": "sumipan/issuesmith", "tag": "v0.1.0"}]
        )
    assert len(results) == 1
    ok, msg = results[0]
    assert ok is False
    assert "git tag v0.1.0" in msg


def test_check_post_merge_stable_install_fails_with_recovery_command():
    with patch("issuesmith.ops.preflight.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Name: issuesmith\nEditable project location: /wrong/path\n",
            stderr="",
        )
        results = check_post_merge(
            [
                {
                    "kind": "stable_install",
                    "repo": "sumipan/issuesmith",
                    "path": "/var/tmp/issuesmith",
                }
            ]
        )
    assert len(results) == 1
    ok, msg = results[0]
    assert ok is False
    assert "pip install -e" in msg
    assert "/var/tmp/issuesmith" in msg
