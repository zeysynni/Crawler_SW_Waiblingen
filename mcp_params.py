# Pin the MCP version (not @latest) for reproducible runs + supply-chain safety.
# 0.0.76 is what's currently in use; bump deliberately after testing.
playwright_params = {"command": "npx","args": [ "@playwright/mcp@0.0.76"], "client_timeout": 30}
web_crawling_mcp_params = [playwright_params]

knowledge_graph_db_params = {"command": "npx","args": ["-y", "mcp-memory-libsql"],"env": {"LIBSQL_URL": "file:./memory/sw_waiblingen_kg.db"}} # npx is the node.js tool that runs npm packages without installing them globally
kb_db_params = [knowledge_graph_db_params]

sql_db_name = "sw_waiblingen_sql"
sqlite_params = {"command": "uvx", "args": ["mcp-server-sqlite", "--db-path", f"memory/{sql_db_name}.db"]}
sqlite_db_params = [sqlite_params]
