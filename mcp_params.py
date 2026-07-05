import os
from pathlib import Path

_EXPAND_SCRIPT = str(Path(__file__).parent / "scripts" / "expand_accordions.js")

# GitLab CI sets CI=true; runners have no display, so run the browser headless.
_headless = os.getenv("CRAWLER_HEADLESS", os.getenv("CI", "")).lower() in ("1", "true", "yes")

# Pin the MCP version (not @latest) for reproducible runs + supply-chain safety.
playwright_params = {
    "command": "npx",
    "args": ["@playwright/mcp@0.0.76", "--init-script", _EXPAND_SCRIPT]
            + (["--headless", "--no-sandbox"] if _headless else []),
    "client_timeout": 30,
}

web_crawling_mcp_params = [playwright_params]

knowledge_graph_db_params = {"command": "npx","args": ["-y", "mcp-memory-libsql"],"env": {"LIBSQL_URL": "file:./memory/sw_waiblingen_kg.db"}} # npx is the node.js tool that runs npm packages without installing them globally
kb_db_params = [knowledge_graph_db_params]

sql_db_name = "sw_waiblingen_sql"
sqlite_params = {"command": "uvx", "args": ["mcp-server-sqlite", "--db-path", f"memory/{sql_db_name}.db"]}
sqlite_db_params = [sqlite_params]
