### Added (2026-10-23 — portable RE archive import identity receipts)

- RE evidence archive imports return the archive SHA-256 and old/new artifact and claim ID mappings, so callers can reconnect manifests to restored records without repeating ingestion.
- The checksum, validation and restore all use one bounded private snapshot; malformed or duplicate source IDs and duplicate artifact identities are rejected before writing.
