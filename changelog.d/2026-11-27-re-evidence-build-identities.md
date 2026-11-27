### Fixed (2026-11-27 — prevent mistyped executable hashes from splitting RE evidence)

- Require a registered build identity for evidence ingestion, claims and
  archive imports. The new `re_evidence` register action computes SHA-256
  from the original binary and returns the identity to reuse. Malformed
  hashes, valid-length typos and alternate names cannot create stray builds.
- Keep registrations in existing durable metadata, outside general memory
  transfer. Historical scopes remain readable/exportable. Register original
  binaries before their next write; legacy labels are not silently trusted.
- Add an explicit, dry-run-first scope repair tool that archives original
  rows, preserves immutable bytes and claims, redirects evidence links, and
  records a migration ledger without fuzzy merging or promoting claims.
