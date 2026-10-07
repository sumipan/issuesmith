"""Keep steps from writing labels through the forge (#4807).

Labels are a projection of issue state written by the runner. While a step runs,
:func:`guard_step_forge` makes every ``get_forge()`` return a :class:`ReadOnlyLabelForge`:
in ``enforce`` mode a label write raises :class:`~issuesmith.contract.LabelWriteForbidden`
before anything is sent, in ``warn`` mode it is reported on stderr and passed through.
"""
from __future__ import annotations

import sys
from contextlib import contextmanager
from typing import Any, Callable, Iterator

from issuesmith.contract import LabelWriteForbidden


class ReadOnlyLabelForge:
    """ForgePort wrapper that refuses (``enforce``) or reports (``warn``) label writes."""

    def __init__(self, inner: Any, *, step_id: str, mode: str) -> None:
        self._inner = inner
        self._step_id = step_id
        self._mode = mode

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def _check(self, labels: list[str]) -> None:
        if self._mode == "enforce":
            raise LabelWriteForbidden(self._step_id, labels)
        print(
            f"[issuesmith-dispatch] WARNING: step {self._step_id} wrote labels {labels};"
            " labels are projected by the runner",
            file=sys.stderr,
        )

    def issue_update(self, *args: Any, **kwargs: Any) -> Any:
        labels = [*(kwargs.get("labels_add") or []), *(kwargs.get("labels_remove") or [])]
        if labels:
            self._check(labels)
        return self._inner.issue_update(*args, **kwargs)

    def add_label(self, number: int, label: str) -> Any:
        self._check([label])
        return self._inner.add_label(number, label)

    def remove_label(self, number: int, label: str) -> Any:
        self._check([label])
        return self._inner.remove_label(number, label)


def _modules_binding(factory: Callable[..., Any]) -> list[Any]:
    """Loaded modules whose ``get_forge`` attribute is ``factory``."""
    found = []
    for module in list(sys.modules.values()):
        try:
            if vars(module).get("get_forge") is factory:
                found.append(module)
        except TypeError:  # module without __dict__
            continue
    return found


@contextmanager
def guard_step_forge(step_id: str, mode: str) -> Iterator[None]:
    """Swap ``get_forge`` for a guarded factory for the duration of a step.

    Steps bind the factory with ``from ghdag.forge import get_forge``, so every loaded
    module holding the original factory is patched, not only ``ghdag.forge``. On exit
    (including on exception) all of them, and modules imported meanwhile that bound the
    guarded factory, get the original back.
    """
    import ghdag.forge as forge_mod

    original = forge_mod.get_forge

    def guarded(*args: Any, **kwargs: Any) -> ReadOnlyLabelForge:
        return ReadOnlyLabelForge(original(*args, **kwargs), step_id=step_id, mode=mode)

    for module in _modules_binding(original):
        module.get_forge = guarded
    try:
        yield
    finally:
        for module in _modules_binding(guarded):
            module.get_forge = original
