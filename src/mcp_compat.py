"""Compatibility layer over the two generations of the `mcp` Python SDK.

The SDK changed its low-level server API between 1.x and 2.x:

* 1.x registers handlers with the ``@server.list_tools()`` / ``@server.call_tool()``
  decorators, and the handlers return bare ``list[Tool]`` / ``list[TextContent]``.
* 2.x removed the decorators. Handlers are passed to the constructor as
  ``on_list_tools`` / ``on_call_tool``, receive ``(context, params)``, and must
  return ``ListToolsResult`` / ``CallToolResult``.

Pinning to one generation is what broke this server before, so instead the
server writes its logic once against the plain shapes and this module adapts it
to whichever SDK is installed. ``mcp.types.Tool`` accepts the ``inputSchema``
alias in both generations, so tool definitions need no branching.
"""

import inspect
from collections.abc import Awaitable, Callable

from mcp.server import Server
from mcp.types import TextContent, Tool

# 2.x dropped the decorators; their absence is the version signal.
USES_DECORATOR_API = hasattr(Server, "list_tools") and hasattr(Server, "call_tool")
SDK_GENERATION = "1.x" if USES_DECORATOR_API else "2.x"

ListToolsHandler = Callable[[], Awaitable[list[Tool]]]
CallToolHandler = Callable[[str, dict], Awaitable[list[TextContent]]]


def _new_server(name: str, version: str) -> Server:
    """Construct a Server, tolerating SDKs that predate the ``version`` kwarg."""
    try:
        return Server(name, version=version)
    except TypeError:
        return Server(name)


def build_server(
    name: str,
    version: str,
    list_tools: ListToolsHandler,
    call_tool: CallToolHandler,
) -> Server:
    """Return a Server wired to the given handlers on either SDK generation."""
    if USES_DECORATOR_API:
        server = _new_server(name, version)
        server.list_tools()(list_tools)
        server.call_tool()(call_tool)
        return server

    from mcp.types import CallToolResult, ListToolsResult

    async def _on_list_tools(_ctx, _params) -> ListToolsResult:
        return ListToolsResult(tools=await list_tools())

    async def _on_call_tool(_ctx, params) -> CallToolResult:
        content = await call_tool(params.name, params.arguments or {})
        # The handler encodes tool-level failures into its JSON payload, so the
        # protocol-level result is never marked as an error.
        return CallToolResult(content=content, isError=False)

    try:
        return Server(
            name,
            version=version,
            on_list_tools=_on_list_tools,
            on_call_tool=_on_call_tool,
        )
    except TypeError as e:  # pragma: no cover - future SDK drift
        raise RuntimeError(
            f"Unsupported mcp SDK layout (generation {SDK_GENERATION}); "
            "idx-mcp needs mcp>=1.9."
        ) from e


async def serve_stdio(server: Server) -> None:
    """Run the server over stdio. The transport API is stable across 1.x/2.x."""
    from mcp.server.stdio import stdio_server

    async with stdio_server() as (read_stream, write_stream):
        options = server.create_initialization_options()
        result = server.run(read_stream, write_stream, options)
        if inspect.isawaitable(result):
            await result
