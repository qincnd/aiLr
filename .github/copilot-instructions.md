# aiLr Workspace Notes

- Backend source is under `backend/`; run its tests with `python -m unittest discover -s backend/tests -v` from the workspace root.
- Keep Lightroom develop controls validated against `backend/app/settings.py` before exposing them to an export or plugin bridge.
- The MCP server uses the official Python SDK v2 (`mcp>=2`). Reference: https://py.sdk.modelcontextprotocol.io/ and https://modelcontextprotocol.io/docs/develop/build-server.
- MCP stdio output is reserved for protocol messages; send diagnostic logs to stderr.
- The Lightroom Classic plugin bridge is a documented integration boundary, not a working write-back implementation yet.
