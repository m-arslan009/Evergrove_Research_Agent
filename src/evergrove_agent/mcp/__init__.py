"""The MCP surface: the research agent, reachable by any MCP client.

A surface, on the same terms as `main.py` and `tools/cli.py` — it composes nothing, decides
nothing, and holds no research logic. `server.py` is the whole package; see its docstring for
what is exposed and why the layer stays this thin.

The package shares its name with the `mcp` SDK it imports. That is safe and deliberate:
Python 3 resolves `import mcp` absolutely, so a module inside this package still reaches the
installed SDK rather than itself. The name matches the plan's repository layout.
"""
