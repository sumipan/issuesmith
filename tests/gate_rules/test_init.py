
import issuesmith.gate_rules  # noqa: F401
from issuesmith.gate_rules import GATE_REGISTRY


def test_gate_registry_is_dict():
    assert isinstance(GATE_REGISTRY, dict)


def test_gate_registry_has_design_lint():
    assert "design_lint" in GATE_REGISTRY


