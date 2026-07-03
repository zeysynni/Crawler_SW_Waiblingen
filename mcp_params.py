import os

# GitLab CI sets CI=true; runners have no display, so run the browser headless.
# Force locally with CRAWLER_HEADLESS=1 (or disable in CI with CRAWLER_HEADLESS=0).
_headless = os.getenv("CRAWLER_HEADLESS", os.getenv("CI", "")).lower() in ("1", "true", "yes")

_playwright_args = ["@playwright/mcp@latest"]
if _headless:
    # --no-sandbox: CI containers run as root, where Chromium's sandbox cannot start.
    _playwright_args += ["--headless", "--no-sandbox"]

playwright_params = {"command": "npx", "args": _playwright_args, "client_timeout": 30}
web_crawling_mcp_params = [playwright_params]

knowledge_graph_db_params = {"command": "npx","args": ["-y", "mcp-memory-libsql"],"env": {"LIBSQL_URL": "file:./memory/sw_waiblingen_kg.db"}} # npx is the node.js tool that runs npm packages without installing them globally
kb_db_params = [knowledge_graph_db_params]

sql_db_name = "sw_waiblingen_sql"
sqlite_params = {"command": "uvx", "args": ["mcp-server-sqlite", "--db-path", f"memory/{sql_db_name}.db"]}
sqlite_db_params = [sqlite_params]
