"""Explicit, archived repair of historical RE build scopes.

This is operator maintenance, not a fuzzy deduplicator. A reviewed source
list and a registered destination are mandatory. Historical JSON stays intact.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path

from pseudolife_memory.re_evidence import EvidenceInputError, parse_evidence_bytes


def _rows(conn, sql, params):
    cursor = conn.execute(sql, params)
    columns = [column.name for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _archive_value(value):
    if isinstance(value, (bytes, memoryview)):
        return {"base64": base64.b64encode(value).decode("ascii")}
    raise TypeError(f"unsupported recovery value: {type(value).__name__}")


def reconcile_scopes(storage, *, project: str, binary_id: str,
                     source_scopes: list[str], archive_path: str | Path,
                     allow_summary_differences: bool = False) -> dict:
    """Copy original bytes, redirect links, then remove explicitly named scopes.

    Existing claim IDs survive unless the same assertion already exists in
    the target. Such duplicates must agree on status/confidence; otherwise
    the entire transaction refuses rather than adjudicating evidence.
    Every original row and link is saved before any proof row changes.
    """
    project = project.strip()
    sources = sorted(set(source_scopes))
    path = Path(archive_path).expanduser().resolve()
    if path.exists():
        raise FileExistsError(f"recovery archive already exists: {path}")
    if not project or not sources or binary_id in sources or any(not s.strip() for s in sources):
        raise EvidenceInputError("name non-empty source scopes distinct from the destination")
    with storage._txn():
        storage.require_re_build(project, binary_id)
        for scope in sorted([binary_id, *sources]):
            storage._lock_re_evidence_scope(project, scope)
        artifacts = _rows(storage.conn,
            "SELECT * FROM re_evidence_artifacts WHERE project = %s AND binary_id = ANY(%s) "
            "ORDER BY id", (project, sources))
        claims = _rows(storage.conn,
            "SELECT * FROM re_claims WHERE project = %s AND binary_id = ANY(%s) ORDER BY id",
            (project, sources))
        links = _rows(storage.conn,
            "SELECT l.* FROM re_claim_evidence l JOIN re_claims c ON c.id = l.claim_id "
            "WHERE c.project = %s AND c.binary_id = ANY(%s) ORDER BY l.claim_id,l.evidence_id",
            (project, sources))
        if not artifacts and not claims:
            raise EvidenceInputError("the selected source scopes are empty")
        for artifact in artifacts:
            if hashlib.sha256(bytes(artifact["raw_bytes"])).hexdigest() != artifact["content_hash"]:
                raise EvidenceInputError(f"original artifact {artifact['id']} has a byte/hash mismatch")
        snapshot = {"project": project, "canonical_binary_id": binary_id,
                    "source_scopes": sources, "artifacts": artifacts,
                    "claims": claims, "links": links}
        raw_archive = json.dumps(snapshot, default=_archive_value, sort_keys=True,
                                 ensure_ascii=True, indent=2).encode()
        path.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive create: never replace a previous recovery archive, even
        # if an earlier repair failed or another operator chose the same name.
        with path.open("xb") as stream:
            stream.write(raw_archive)
            stream.flush()
            os.fsync(stream.fileno())
        if path.read_bytes() != raw_archive:
            raise EvidenceInputError("recovery archive verification failed")

        artifact_map = {}
        summary_differences = {}
        for artifact in artifacts:
            old_id = artifact["id"]
            existing = storage.query_re_evidence(
                project=project, binary_id=binary_id,
                content_hash=artifact["content_hash"], limit=None)
            existing = next((a for a in existing if a["locator"] == artifact["locator"]), None)
            if existing:
                new_id = existing["id"]
                for field in ("kind", "addresses", "payload_keys"):
                    if existing[field] != artifact[field]:
                        raise EvidenceInputError(
                            f"artifact {old_id} conflicts with canonical artifact {new_id}: {field}")
                if existing.get("summary") != artifact.get("summary"):
                    if not allow_summary_differences:
                        raise EvidenceInputError(
                            f"artifact {old_id} has a different summary from canonical artifact {new_id}; "
                            "review both summaries before explicitly allowing summary differences")
                    summary_differences[str(old_id)] = {
                        "canonical_artifact_id": new_id, "historical": artifact.get("summary"),
                        "canonical": existing.get("summary"),
                    }
            else:
                new_id = storage.insert_re_evidence({**artifact, "binary_id": binary_id})
            restored = storage.get_re_evidence_for_export(
                project=project, binary_id=binary_id, artifact_id=new_id)
            if bytes(restored["raw_bytes"]) != bytes(artifact["raw_bytes"]):
                raise EvidenceInputError(f"canonical artifact {new_id} has different original bytes")
            artifact_map[str(old_id)] = new_id

        claim_map = {}
        for claim in claims:
            old_id = claim["id"]
            existing = storage.conn.execute(
                "SELECT id,status,confidence FROM re_claims WHERE project=%s AND binary_id=%s "
                "AND subject=%s AND claim=%s",
                (project, binary_id, claim["subject"], claim["claim"])).fetchone()
            if existing:
                new_id, status, confidence = existing
                if (status, confidence) != (claim["status"], claim["confidence"]):
                    raise EvidenceInputError(
                        f"claim {old_id} conflicts with canonical claim {new_id}; review their statuses first")
            else:
                new_id = old_id
                storage.conn.execute("UPDATE re_claims SET binary_id=%s WHERE id=%s",
                                     (binary_id, old_id))
            for link in links:
                if link["claim_id"] != old_id:
                    continue
                new_artifact = artifact_map.get(str(link["evidence_id"]))
                if new_artifact is None:
                    raise EvidenceInputError(f"claim {old_id} has an out-of-scope evidence link")
                storage.conn.execute(
                    "INSERT INTO re_claim_evidence (claim_id,evidence_id,linked_at) VALUES (%s,%s,%s) "
                    "ON CONFLICT DO NOTHING", (new_id, new_artifact, link["linked_at"]))
                storage.conn.execute(
                    "DELETE FROM re_claim_evidence WHERE claim_id=%s AND evidence_id=%s",
                    (old_id, link["evidence_id"]))
            if existing:
                storage.conn.execute("DELETE FROM re_claims WHERE id=%s", (old_id,))
            claim_map[str(old_id)] = new_id
        storage.conn.execute(
            "DELETE FROM re_evidence_artifacts WHERE project=%s AND binary_id=ANY(%s)",
            (project, sources))
        # Run every deferred proof gate before recording a successful ledger.
        storage.conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
        result = {
            "kind": "binary-scope-migration-ledger", "project": project,
            "canonical_binary_id": binary_id, "source_scopes": sources,
            "artifact_id_map": artifact_map, "claim_id_map": claim_map,
            "summary_differences": summary_differences,
            "recovery_archive": str(path),
            "recovery_archive_sha256": hashlib.sha256(raw_archive).hexdigest(),
            "preservation": "Original bytes and embedded IDs unchanged; original metadata, "
                            "timestamps and links archived; claim statuses/confidence preserved. "
                            "No behavioral assertion was promoted.",
        }
        ledger = parse_evidence_bytes(json.dumps(result, sort_keys=True).encode(),
                                      source_path=str(path) + "#migration-ledger")
        ledger.update(project=project, binary_id=binary_id,
                      kind="binary-scope-migration-ledger", summary="Archived build-scope reconciliation")
        result["ledger_artifact_id"] = storage.insert_re_evidence(ledger)
    return result
