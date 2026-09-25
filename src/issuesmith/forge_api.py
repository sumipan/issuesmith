"""Typed entry point for raw REST calls on forge clients (#3611).

``ForgePort`` has no ``api_request``; callers that need raw REST paths go
through :func:`api_request`, which delegates when the client supports it and
raises ``NotImplementedError`` otherwise (callers already degrade on errors).
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class RawApiForge(Protocol):
    def api_request(self, path: str, *, method: str = "GET", **kwargs: Any) -> Any: ...


def api_request(client: object, path: str, *, method: str = "GET", **kwargs: Any) -> Any:
    """Return ``client.api_request(path, ...)`` unchanged.

    ``method`` is forwarded only when it is not ``"GET"`` so fakes asserting the
    exact call arguments keep matching. Raises ``NotImplementedError`` when the
    client has no ``api_request``.
    """
    # getattr rather than isinstance(client, RawApiForge): on 3.12+ runtime
    # protocol checks use getattr_static and miss MagicMock's dynamic attributes.
    request = getattr(client, "api_request", None)
    if not callable(request):
        raise NotImplementedError(f"{type(client).__name__} does not support api_request")
    if method != "GET":
        kwargs["method"] = method
    return request(path, **kwargs)
