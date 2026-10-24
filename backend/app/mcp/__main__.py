"""stdio transport: `python -m app.mcp` / `frogradar-mcp` / `make mcp`.

For MCP clients on a laptop (e.g. Claude Desktop) and the MCP Inspector. stdout is the protocol,
so all logging goes to stderr. Needs Postgres (`make db-up`); provider keys (for `ask` and the
search_intel query embedding) come from the environment, e.g. `uv run --env-file ../.env`.
"""

import logging
import sys


def main() -> None:
    logging.basicConfig(
        level=logging.WARNING, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s"
    )
    from app.mcp.server import build_server

    build_server().run("stdio")


if __name__ == "__main__":
    main()
