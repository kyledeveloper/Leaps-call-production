# Optional context-mode MCP

Host adapter for Antigravity, Cursor, and Claude Code. **Not** part of `plan --target`, local CLIs, or reflexion memory.

Start only for long sessions or heavy tool output. `npm install` in this repo does not install `context-mode` (avoids `better-sqlite3` on every clone).

```bash
npx -y context-mode
```

Point the host at `mcp_config.json` in this directory.
