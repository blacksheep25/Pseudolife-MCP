// What the token dialog says, from /health's non-secret "auth" flag: a
// daemon with no bearer token configured has nothing to paste, so the dialog
// names the fix instead of pointing at a token that was never minted.

import type { Health } from "./api/types";

/** The one fix for a tokenless daemon, shared with errors.ts. */
export const INSTALL_TOKEN_FIX =
  "Re-run the installer (pwsh ops\install.ps1 on Windows, ops/install.sh elsewhere): it adds PSEUDOLIFE_MCP_TOKEN to ops/.env, restarts the daemon and writes ~/.pseudolife-mcp/claude-code.token. Then paste that file's contents here.";

export interface TokenHelp {
  /** /health said the daemon has no bearer token configured. */
  tokenless: boolean;
  text: string;
  fix?: string;
}

export function tokenHelp(health: Health | null | undefined): TokenHelp {
  // Only an explicit false: before /health answers, or from a daemon that
  // predates the flag, the normal prompt is the safe one.
  if (health?.auth === false) {
    return {
      tokenless: true,
      text: "This daemon has no bearer token yet, so there is nothing to paste. Memory views work without one from this machine, but the board needs one.",
      fix: INSTALL_TOKEN_FIX,
    };
  }
  return {
    tokenless: false,
    text: "The daemon's token: the contents of ~/.pseudolife-mcp/<principal>.token (claude-code.token for Claude Code) on the daemon's machine, or PSEUDOLIFE_MCP_TOKEN in ops/.env. It stays in this browser's local storage.",
  };
}
