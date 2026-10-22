import { describe, expect, it } from "vitest";
import { ApiError } from "./api/client";
import { explainError } from "./errors";
import { tokenHelp } from "./tokenHelp";

describe("tokenHelp", () => {
  it("says a tokenless daemon has no token yet and names the installer fix", () => {
    // 2026-10-23: on a loopback install with no PSEUDOLIFE_MCP_TOKEN the dialog
    // named the daemon's token as if one existed, and the user hunted for a
    // ~/.pseudolife-mcp/claude-code.token that was never written.
    const h = tokenHelp({ status: "ok", auth: false });
    expect(h.tokenless).toBe(true);
    expect(h.text).toMatch(/no bearer token yet/i);
    expect(h.text).toMatch(/board needs one/i);
    expect(h.fix).toContain(String.raw`pwsh ops\install.ps1`);
    expect(h.fix).toContain("ops/install.sh");
    expect(h.fix).toContain("~/.pseudolife-mcp/claude-code.token");
  });

  it("keeps the normal prompt, with where to find the token, when the daemon has one", () => {
    // Near miss: a token-configured daemon must never be told it has none.
    const h = tokenHelp({ status: "ok", auth: true });
    expect(h.tokenless).toBe(false);
    expect(h.text).not.toMatch(/no bearer token/i);
    expect(h.text).toContain("~/.pseudolife-mcp/<principal>.token");
    expect(h.text).toContain("ops/.env");
    expect(h.fix).toBeUndefined();
  });

  it("does not claim a tokenless daemon before /health has answered", () => {
    for (const health of [null, undefined, { status: "ok" }]) {
      const h = tokenHelp(health);
      expect(h.tokenless).toBe(false);
      expect(h.text).not.toMatch(/no bearer token/i);
    }
  });
});

describe("explainError authentication_required", () => {
  it("names the installer as the fix for a daemon with no token", () => {
    const x = explainError(new ApiError(403, "authentication_required", { error: "authentication_required" }), "The board");
    expect(x.body).toMatch(/no bearer token yet/i);
    expect(x.body).toContain(String.raw`pwsh ops\install.ps1`);
    expect(x.body).toContain("ops/install.sh");
    expect(x.body).toContain("~/.pseudolife-mcp/claude-code.token");
  });
});
