from issuesmith.gate_rules.common import strip_code_regions


def test_strip_code_regions_removes_fenced_and_inline():
    body = "before\n```python\nx = 1\n```\nmiddle `inline` after"
    assert strip_code_regions(body) == "before\n\nmiddle  after"
