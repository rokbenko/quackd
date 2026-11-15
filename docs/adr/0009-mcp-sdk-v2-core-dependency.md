# ADR-0009: MCP Python SDK v2 as a core dependency

**Status:** accepted · **Date:** 2026-08-28

## Context

`roboglot serve-mcp` is the second wow-demo ("I asked Claude to make the duck patrol my
desk"). The brief allows an `[mcp]` extra "if the SDK is heavy". The official Python SDK
moved to a **v2** line in 2026 (`mcp` 2.1.1 at time of writing): `FastMCP` became
`MCPServer` (`from mcp.server import MCPServer`; `Context` and `Image` live in
`mcp.server.mcpserver`), `mcp.run(transport="stdio")`, and tools return `Image(...)` for
image content.

## Measurement (2026-08-28, Windows, `uv sync --extra dev`)

| Package | Installed size |
|---|---|
| `mcp` | 1.1 MB |
| its tree (starlette, uvicorn, httpx, cryptography, pyjwt, anyio, sse-starlette…) | ≈ 15 MB |
| `opencv-python-headless` (**already mandatory** for perception) | 112 MB |

The MCP tree is an order of magnitude smaller than a dependency we cannot drop.

## Decision

- `mcp>=2.0,<3` is a **core** dependency. No `[mcp]` extra.
- We code against the v2 API only (`MCPServer`, `Image`, `run(transport="stdio")`).
  `mcp.server.fastmcp` is not imported anywhere.
- Logging in the MCP server goes to **stderr only** — stdout is the wire.

## Consequences

- `uvx roboglot serve-mcp --transport sim2d` works with zero extras; the config snippets in
  `docs/mcp.md` need no install step.
- A v3 SDK will require a deliberate bump (pinned `<3`), not a silent break.
- If `Image.data` semantics differ from the migration guide (raw bytes vs. base64), the
  M4 test `tests/test_mcp_server.py` is what catches it.
