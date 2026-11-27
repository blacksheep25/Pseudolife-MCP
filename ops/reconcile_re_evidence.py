"""Archive and repair explicitly reviewed RE build scopes; dry-run by default.

Run on the daemon host with PSEUDOLIFE_MCP_DATABASE_URL configured. The plan is
JSON: {"project": "example", "binary_path": "/mounted/client.exe",
       "source_scopes": ["historical-mistyped-build-id"]}.
No similarity-based source selection is performed.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg

from pseudolife_memory.re_evidence_repair import reconcile_scopes
from pseudolife_memory.storage.re_evidence import ReEvidenceArchiveStorage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    with psycopg.connect(os.environ["PSEUDOLIFE_MCP_DATABASE_URL"], autocommit=True) as conn:
        storage = ReEvidenceArchiveStorage(conn)
        registered = storage.register_re_build(project=plan["project"], path=plan["binary_path"],
                                               dry_run=not args.apply)
        if not args.apply:
            result = {"apply": False, "destination": registered["binary_id"], "sources": [
                storage.re_evidence_stats(plan["project"], scope)
                for scope in plan["source_scopes"]]}
        else:
            result = reconcile_scopes(storage, project=plan["project"],
                binary_id=registered["binary_id"], source_scopes=plan["source_scopes"],
                archive_path=args.archive,
                allow_summary_differences=plan.get("allow_summary_differences") is True)
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
