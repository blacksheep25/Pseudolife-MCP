### Fixed (2026-10-23 — Console token dialog pointed at a token a tokenless daemon never had)
- On a daemon with no bearer token configured (neither
  `PSEUDOLIFE_MCP_TOKEN` nor `PSEUDOLIFE_MCP_TOKENS`, `/health` reporting
  `"auth": false`), the Console's "Bearer token" dialog now says the daemon
  has no token yet, that the board needs one, and names the fix: re-run the
  installer with its defaults (`pwsh ops\install.ps1` / `ops/install.sh`,
  not `-NoToken` / `--no-token`), which adds
  `PSEUDOLIFE_MCP_TOKEN` to `ops/.env` and writes
  `~/.pseudolife-mcp/claude-code.token`. Before, it described "the daemon's
  token" as if one existed, and a user went looking for a token file that was
  never written. The board's `authentication_required` message names the same
  fix instead of asking the user to start the daemon with a token.
- A daemon that has a token keeps the normal prompt, which now says where to
  find it: `~/.pseudolife-mcp/<principal>.token`, or `PSEUDOLIFE_MCP_TOKEN` in
  `ops/.env`. The token value is never shown.
