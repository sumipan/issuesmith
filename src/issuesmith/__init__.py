"""
issuesmith — tools for ghdag workflows

Main modules:
    context_hook    — ghdag context_hook (context generation for impl/merge handlers)
    engine          — LLM role switcher / runner
    ops             — operational commands such as dispatch / publish / preflight
    cli             — unified CLI (python3 -m issuesmith)

The stash design-doc tools (apply / ingest_review) moved to tools/stash/.

Pipeline orchestration (polling / DAG construction / label transitions / idempotency)
is handled by ghdag WorkflowDispatcher. See workflows/issuesmith.yml.
"""

__all__: list[str] = []
