# gate_rules

issuesmith's gate check mechanism. Each gate implements the `GateRule` protocol and registers itself in `GATE_REGISTRY`; the registered gates then validate an Issue body and its labels.

## GateRule protocol

```python
class GateRule(Protocol):
    def check(self, body: str, labels: list[str]) -> list[Violation]: ...
```

| Argument | Type | Description |
|------|----|------|
| `body` | `str` | Full Issue body (Markdown text) |
| `labels` | `list[str]` | Names of the labels on the Issue |
| Return value | `list[Violation]` | Violations found (an empty list when there are none) |

## Violation

```python
@dataclass
class Violation:
    rule_id: str
    severity: str
    message: str
    location: str | None
    auto_fixable: bool
    fix_hint: str | None
```

| Field | Type | Description |
|------------|----|------|
| `rule_id` | `str` | Rule identifier (e.g. `cp1.forbidden_word.todo`) |
| `severity` | `str` | Severity. `"fail"` is a blocking violation, `"warn"` is a warning |
| `message` | `str` | Human-readable error message |
| `location` | `str \| None` | Text of the violating location (optional) |
| `auto_fixable` | `bool` | Whether the B1 phase can fix it automatically |
| `fix_hint` | `str \| None` | Hint for the fix (optional) |

Hosts decide on `rule_id`; `message` and `fix_hint` are English literals. Text a gate posts to GitHub (e.g. the `scope_breadth` auto-narrowing note) comes from the language pack (`get_config().language.messages`), and the Issue body headings a gate reads come from `get_config().sections`.

## GATE_REGISTRY

```python
GATE_REGISTRY: dict[str, type[GateRule]] = {}
```

Keys are gate names (`"cp1"`, `"m2"`, ...); values are classes implementing `GateRule` (the class itself, not an instance).

The end of `gate_rules/__init__.py` imports every gate module; registration in `GATE_REGISTRY` happens as an import side effect.

## Adding a new rule

1. Create `gate_rules/<gate_name>.py`
2. Define a class that satisfies the `GateRule` protocol and implement `check()`

   ```python
   from issuesmith.gate_rules import GATE_REGISTRY, GateRule, Violation

   class MyGateRules:
       def check(self, body: str, labels: list[str]) -> list[Violation]:
           violations: list[Violation] = []
           # implement the check logic
           return violations
   ```

3. Register it in `GATE_REGISTRY` at the end of the file

   ```python
   GATE_REGISTRY["<gate_name>"] = MyGateRules
   ```

4. Add an import at the end of `gate_rules/__init__.py`

   ```python
   import issuesmith.gate_rules.<gate_name>  # noqa: E402, F401
   ```

See `cp1.py` (CP1 gate) and `m2.py` (M2 gate) for existing examples.

## Utilities (ghdag.workflow.gates.common)

The implementation moved to ghdag (`common.py` / `preflight.py` no longer exist in this directory).

```python
from ghdag.workflow.gates.common import strip_code_regions

stripped = strip_code_regions(body)
```

`strip_code_regions(body: str) -> str` returns the Issue body with fenced code blocks (ranges enclosed in ` ``` `) and inline code spans (`` `...` ``) removed. Call it inside `check()` before pattern matching so forbidden words inside code are not false positives.

## Existing rules

### cp1 — forbidden words and intentional hold

Returns a `severity="fail"` violation when the Issue body (code excluded) contains one of the following patterns. Non-ASCII patterns are written as Python `\uXXXX` escapes.

| rule_id | Pattern | Notes |
|---------|------------|------|
| `cp1.forbidden_word.todo` | `TODO:` | |
| `cp1.forbidden_word.tbd` | `TBD` | |
| `cp1.forbidden_word.youkakunin` | `\u8981\u78ba\u8a8d` ("needs confirmation") | |
| `cp1.forbidden_word.mitei` | `\u672a\u5b9a` ("undecided"; `\u672a\u5b9a\u7fa9` "undefined" is excluded) | regex `\u672a\u5b9a(?!\u7fa9)` |
| `cp1.forbidden_word.kentouchuu` | `\u691c\u8a0e\u4e2d` ("under consideration") | |
| `cp1.forbidden_word.user_confirm` | `\u30e6\u30fc\u30b6\u30fc\u306b\u78ba\u8a8d` ("ask the user") | |
| `cp1.intentional_hold` | `cp1_must_fail: true` in the YAML frontmatter | Intentional hold; must be released manually (`auto_fixable: false`) |

Every forbidden-word violation is `auto_fixable: true` (fixed automatically in the B1 phase).

### m2 — acceptance criteria section check

| rule_id | Condition | severity |
|---------|------|---------|
| `m2.ac_section_missing` | The acceptance criteria section (`## <sections.acceptance_criteria>`) does not exist | `warn` |
| `m2.unchecked_ac` | The section has one or more unchecked checkboxes (`- [ ]`) | `fail` |

Returns an empty list when the section exists and has zero unchecked checkboxes.

## gate-preflight CLI

The CLI itself moved to `ghdag.workflow.gates.__main__`. `python -m issuesmith gate-preflight` is a thin entry point that imports issuesmith.gate_rules to register the rules and then delegates to the ghdag CLI; it runs a single gate against any Issue body file.

```bash
# Run the CP1 gate against body.md
python -m issuesmith gate-preflight --gate cp1 --body-file body.md

# Also consider labels (labels.txt holds one label per line)
python -m issuesmith gate-preflight --gate cp1 --body-file body.md --labels-file labels.txt

# Run the M2 gate
python -m issuesmith gate-preflight --gate m2 --body-file body.md
```

The output is a JSON array: `[]` when there are no violations, otherwise a list of `Violation` objects.

```json
[
  {
    "rule_id": "cp1.forbidden_word.todo",
    "severity": "fail",
    "message": "TODO: remains",
    "location": null,
    "auto_fixable": true,
    "fix_hint": "replace it with a concrete description"
  }
]
```
