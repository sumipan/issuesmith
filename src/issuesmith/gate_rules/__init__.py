from ghdag.workflow.gates import GATE_REGISTRY, GateRule, Violation  # noqa: F401

import issuesmith.gate_rules.allow_paths_forbidden  # noqa: E402,F401
import issuesmith.gate_rules.b1_ac_format  # noqa: E402,F401
import issuesmith.gate_rules.b1_migration  # noqa: E402,F401
import issuesmith.gate_rules.b1_milestone_subdesign  # noqa: E402,F401
import issuesmith.gate_rules.change_table_scope  # noqa: E402,F401
import issuesmith.gate_rules.cp1  # noqa: E402,F401
import issuesmith.gate_rules.deps_format  # noqa: E402,F401
import issuesmith.gate_rules.m2  # noqa: E402,F401
import issuesmith.gate_rules.milestone_consistency  # noqa: E402,F401
import issuesmith.gate_rules.pin_bump  # noqa: E402,F401
import issuesmith.gate_rules.scope_breadth  # noqa: E402,F401
import issuesmith.gate_rules.scope_coupling  # noqa: E402,F401
import issuesmith.gate_rules.scope_size  # noqa: E402,F401

# Old gate ids → concept names (sumipan/nexus#5804). Violation.rule_id unchanged.
GATE_ID_ALIASES: dict[str, str] = {
    "cp1": "design_lint",
    "m2": "ac_complete",
    "b1_ac_format": "ac_format",
    "b1_migration": "migration_plan",
    "b1_milestone_subdesign": "milestone_subdesign",
}

for _old_id, _new_id in GATE_ID_ALIASES.items():
    GATE_REGISTRY[_new_id] = GATE_REGISTRY[_old_id]
