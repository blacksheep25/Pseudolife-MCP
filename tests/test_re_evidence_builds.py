"""Build scopes are computed identities, never caller-created labels."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from psycopg.types.json import Jsonb

from tests.pg_fixtures import pg_conn, pg_url  # noqa: F401
from pseudolife_memory.re_evidence import EvidenceInputError, parse_evidence_bytes


def test_register_hashes_binary_and_reuses_identity_after_rename(pg_conn, tmp_path):
    from pseudolife_memory.storage.re_evidence import ReEvidenceArchiveStorage

    store = ReEvidenceArchiveStorage(pg_conn)
    executable = tmp_path / "client.exe"
    executable.write_bytes(b"original executable bytes")
    expected = "client.exe:sha256:" + hashlib.sha256(executable.read_bytes()).hexdigest()
    assert store.register_re_build(project="example", path=executable)["binary_id"] == expected
    renamed = tmp_path / "renamed.exe"
    renamed.write_bytes(executable.read_bytes())
    assert store.register_re_build(project="example", path=renamed)["binary_id"] == expected
    assert store.require_re_build("example", expected) == expected


@pytest.mark.parametrize("suffix", ["b", "?", "", "valid-length-typo"])
def test_unregistered_or_malformed_build_cannot_create_artifact_or_claim(pg_conn, tmp_path, suffix):
    from pseudolife_memory.storage.re_evidence import ReEvidenceArchiveStorage

    store = ReEvidenceArchiveStorage(pg_conn)
    executable = tmp_path / "client.exe"
    executable.write_bytes(b"original executable bytes")
    canonical = store.register_re_build(project="example", path=executable)["binary_id"]
    if suffix == "valid-length-typo":
        bad = canonical[:-1] + ("0" if canonical[-1] != "0" else "1")
    elif suffix == "":
        bad = "client.exe:sha256:"
    else:
        bad = canonical + suffix
    artifact = {**parse_evidence_bytes(b'{}', source_path="report.json"),
                "project": "example", "binary_id": bad, "kind": "verification"}
    with pytest.raises(EvidenceInputError, match="register"):
        store.insert_re_evidence(artifact)
    with pytest.raises(EvidenceInputError, match="register"):
        store.upsert_re_claim(project="example", binary_id=bad, subject="subject",
                              claim="hypothesis", status="hypothesis")
    assert store.re_evidence_scopes() == []


def test_known_build_is_project_scoped_and_filename_is_not_an_alias(pg_conn, tmp_path):
    from pseudolife_memory.storage.re_evidence import ReEvidenceArchiveStorage

    store = ReEvidenceArchiveStorage(pg_conn)
    executable = tmp_path / "client.exe"
    executable.write_bytes(b"original executable bytes")
    canonical = store.register_re_build(project="example", path=executable)["binary_id"]
    with pytest.raises(EvidenceInputError, match="register"):
        store.require_re_build("other-project", canonical)
    with pytest.raises(EvidenceInputError, match="client.exe"):
        store.require_re_build("example", canonical.replace("client.exe", "renamed.exe"))


def test_empty_archive_cannot_bypass_registration(pg_conn, tmp_path):
    import zipfile
    from pseudolife_memory.re_evidence import ARCHIVE_FORMAT, import_evidence_archive
    from pseudolife_memory.storage.re_evidence import ReEvidenceArchiveStorage

    identity = "client.exe:sha256:" + "a" * 64
    path = tmp_path / "empty.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", json.dumps({
            "format": ARCHIVE_FORMAT, "project": "example", "binary_id": identity,
            "artifacts": [], "claims": [],
        }))
    with pytest.raises(EvidenceInputError, match="register"):
        import_evidence_archive(ReEvidenceArchiveStorage(pg_conn), path=path,
                                project="example", binary_id=identity, archive_root=tmp_path)


def test_registration_cannot_be_imported_as_ordinary_memory():
    from pseudolife_memory.transfer_cli import _skip_meta_key

    assert _skip_meta_key("re_evidence_build:example")


def test_registration_hashing_does_not_hold_shared_service_lock():
    import threading
    from pseudolife_memory.service import MemoryService

    service = MemoryService.__new__(MemoryService)
    service._lock = threading.Lock()
    class Storage:
        closed = False
        def verify_writer_session(self):
            pass
        def re_evidence_archive_storage(self):
            return self
        def register_re_build(self, **kwargs):
            assert not service._lock.locked(), "binary hashing blocks unrelated service requests"
            return kwargs
        def close(self):
            self.closed = True
    storage = Storage()
    service._ensure_postgres_storage = lambda: storage
    assert service.re_evidence_register(project="example", path="binary.exe") == {
        "project": "example", "path": "binary.exe"}
    assert storage.closed


def test_register_renamed_original_keeps_existing_historical_scope(pg_conn, tmp_path):
    from pseudolife_memory.storage.re_evidence import ReEvidenceArchiveStorage

    binary = tmp_path / "renamed-copy.exe"
    binary.write_bytes(b"original executable bytes")
    old_scope = "client.exe:sha256:" + hashlib.sha256(binary.read_bytes()).hexdigest()
    pg_conn.execute(
        "INSERT INTO re_claims (project,binary_id,subject,claim,status,created_at,updated_at) "
        "VALUES ('example',%s,'subject','legacy hypothesis','hypothesis',1,1)", (old_scope,))
    pg_conn.commit()
    store = ReEvidenceArchiveStorage(pg_conn)
    assert store.register_re_build(project="example", path=binary, dry_run=True)["binary_id"] == old_scope
    with pytest.raises(EvidenceInputError, match="unregistered"):
        store.require_re_build("example", old_scope)
    assert store.register_re_build(project="example", path=binary)["binary_id"] == old_scope


def test_reconcile_preserves_bytes_claim_ids_and_links(pg_conn, tmp_path):
    from pseudolife_memory.re_evidence_repair import reconcile_scopes
    from pseudolife_memory.storage.re_evidence import ReEvidenceArchiveStorage

    store = ReEvidenceArchiveStorage(pg_conn)
    binary = tmp_path / "client.exe"
    binary.write_bytes(b"original executable bytes")
    canonical = store.register_re_build(project="example", path=binary)["binary_id"]
    # Historical rows bypassed registration. Keep their original raw JSON,
    # including the embedded typo, while repairing only the index and links.
    typo = canonical + "b"
    raw = json.dumps({"binary_id": typo, "address": "00401000"}).encode()
    original = parse_evidence_bytes(raw, source_path="original.json")
    with pg_conn.transaction():
        old_id = pg_conn.execute(
            "INSERT INTO re_evidence_artifacts (project,binary_id,kind,locator,source_path,"
            "content_hash,raw_bytes,payload,payload_keys,addresses,ingested_at) "
            "VALUES ('example',%s,'capture',%s,'original.json',%s,%s,%s,%s,%s,1) RETURNING id",
            (typo, original["locator"], original["content_hash"], raw,
             Jsonb(original["payload"]),
             original["payload_keys"], original["addresses"])).fetchone()[0]
        claim_id = pg_conn.execute(
            "INSERT INTO re_claims (project,binary_id,subject,claim,status,created_at,updated_at) "
            "VALUES ('example',%s,'00401000','original claim','verified',1,2) RETURNING id",
            (typo,)).fetchone()[0]
        pg_conn.execute("INSERT INTO re_claim_evidence VALUES (%s,%s,1)", (claim_id, old_id))
    pg_conn.commit()
    archive = tmp_path / "original-records.json"
    result = reconcile_scopes(store, project="example", binary_id=canonical,
                              source_scopes=[typo], archive_path=archive)
    assert archive.is_file()
    assert result["artifact_id_map"][str(old_id)] != old_id
    assert store.re_evidence_stats("example", typo)["artifacts"] == 0
    claim = store.query_re_claims(project="example", binary_id=canonical)[0]
    assert claim["id"] == claim_id
    assert claim["created_at"] == 1 and claim["updated_at"] == 2
    assert claim["evidence_ids"] == [result["artifact_id_map"][str(old_id)]]
    restored = store.get_re_evidence_for_export(
        project="example", binary_id=canonical, artifact_id=claim["evidence_ids"][0])
    assert restored["raw_bytes"] == raw
    assert restored["content_hash"] == original["content_hash"]
    assert json.loads(restored["raw_bytes"])["binary_id"] == typo
    assert result["ledger_artifact_id"]
    with pytest.raises(FileExistsError):
        reconcile_scopes(store, project="example", binary_id=canonical,
                         source_scopes=[typo], archive_path=archive)


@pytest.mark.parametrize("conflicting", [False, True])
def test_reconcile_deduplicates_or_rolls_back_without_changing_claim_status(pg_conn, tmp_path, conflicting):
    from pseudolife_memory.re_evidence_repair import reconcile_scopes
    from pseudolife_memory.storage.re_evidence import ReEvidenceArchiveStorage

    store = ReEvidenceArchiveStorage(pg_conn)
    binary = tmp_path / "client.exe"
    binary.write_bytes(b"original executable bytes")
    canonical = store.register_re_build(project="example", path=binary)["binary_id"]
    artifact = {**parse_evidence_bytes(b'{"address":"00401000"}', source_path="canonical.json"),
                "project": "example", "binary_id": canonical, "kind": "capture"}
    target_artifact = store.insert_re_evidence(artifact)
    target_claim = store.upsert_re_claim(project="example", binary_id=canonical,
        subject="subject", claim="same assertion", status="verified", evidence_ids=[target_artifact])
    typo = canonical + "b"
    with pg_conn.transaction():
        source_artifact = pg_conn.execute(
            "INSERT INTO re_evidence_artifacts (project,binary_id,kind,locator,source_path,"
            "content_hash,raw_bytes,payload,payload_keys,addresses,ingested_at) "
            "SELECT project,%s,kind,locator,'historical.json',content_hash,raw_bytes,payload,"
            "payload_keys,addresses,1 FROM re_evidence_artifacts WHERE id=%s RETURNING id",
            (typo, target_artifact)).fetchone()[0]
        source_claim = pg_conn.execute(
            "INSERT INTO re_claims (project,binary_id,subject,claim,status,created_at,updated_at) "
            "SELECT project,%s,subject,claim,%s,1,1 FROM re_claims WHERE id=%s RETURNING id",
            (typo, "observed" if conflicting else "verified", target_claim)).fetchone()[0]
        pg_conn.execute("INSERT INTO re_claim_evidence VALUES (%s,%s,1)", (source_claim, source_artifact))
    pg_conn.commit()
    archive = tmp_path / "recovery.json"
    if conflicting:
        with pytest.raises(EvidenceInputError, match="conflicts"):
            reconcile_scopes(store, project="example", binary_id=canonical,
                             source_scopes=[typo], archive_path=archive)
        assert store.re_evidence_stats("example", typo)["artifacts"] == 1
        assert store.query_re_claims(project="example", binary_id=typo)[0]["status"] == "observed"
        assert store.re_evidence_stats("example", canonical)["artifacts"] == 1
    else:
        result = reconcile_scopes(store, project="example", binary_id=canonical,
                                  source_scopes=[typo], archive_path=archive)
        assert result["artifact_id_map"][str(source_artifact)] == target_artifact
        assert result["claim_id_map"][str(source_claim)] == target_claim
        assert store.re_evidence_stats("example", typo)["artifacts"] == 0
    assert store.query_re_claims(project="example", binary_id=canonical)[0]["status"] == "verified"
    assert archive.is_file()


def test_repair_cli_dry_run_then_apply_uses_daemon_database_environment(pg_conn, pg_url, tmp_path):
    from pseudolife_memory.storage.re_evidence import ReEvidenceArchiveStorage

    binary = tmp_path / "client.exe"
    binary.write_bytes(b"original executable bytes")
    identity = "client.exe:sha256:" + hashlib.sha256(binary.read_bytes()).hexdigest()
    typo = identity + "b"
    pg_conn.execute(
        "INSERT INTO re_claims (project,binary_id,subject,claim,status,created_at,updated_at) "
        "VALUES ('example',%s,'subject','historical todo','todo',1,1)", (typo,))
    pg_conn.commit()
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"project": "example", "binary_path": str(binary),
                                "source_scopes": [typo]}), encoding="utf-8")
    archive = tmp_path / "recovery.json"
    root = Path(__file__).resolve().parents[1]
    command = [sys.executable, str(root / "ops/reconcile_re_evidence.py"),
               "--plan", str(plan), "--archive", str(archive)]
    env = {**os.environ, "PSEUDOLIFE_MCP_DATABASE_URL": str(pg_url)}
    preview = subprocess.run(command, env=env, capture_output=True, text=True, check=True)
    assert json.loads(preview.stdout)["apply"] is False
    assert not archive.exists()
    store = ReEvidenceArchiveStorage(pg_conn)
    with pytest.raises(EvidenceInputError, match="unregistered"):
        store.require_re_build("example", identity)
    applied = subprocess.run([*command, "--apply"], env=env, capture_output=True, text=True, check=True)
    assert json.loads(applied.stdout)["ledger_artifact_id"]
    assert archive.exists()
    assert store.query_re_claims(project="example", binary_id=identity)[0]["status"] == "todo"
    assert store.query_re_claims(project="example", binary_id=typo) == []


def test_archive_restore_accepts_registered_renamed_original_with_same_digest(pg_conn, tmp_path):
    from pseudolife_memory.re_evidence import export_evidence_archive, import_evidence_archive
    from pseudolife_memory.storage.re_evidence import ReEvidenceArchiveStorage

    store = ReEvidenceArchiveStorage(pg_conn)
    binary = tmp_path / "original.exe"
    binary.write_bytes(b"original executable bytes")
    old = store.register_re_build(project="example", path=binary)["binary_id"]
    raw = b'{"address":"00401000"}'
    store.insert_re_evidence({**parse_evidence_bytes(raw, source_path="original.json"),
        "project": "example", "binary_id": old, "kind": "capture"})
    archive = tmp_path / "evidence.zip"
    export_evidence_archive(store, project="example", binary_id=old, path=archive, archive_root=tmp_path)
    with pg_conn.transaction():
        pg_conn.execute("DELETE FROM re_evidence_artifacts WHERE project='example'")
        pg_conn.execute("DELETE FROM meta WHERE key LIKE 're_evidence_build:%'")
    pg_conn.commit()
    renamed = tmp_path / "renamed.exe"
    renamed.write_bytes(binary.read_bytes())
    new = store.register_re_build(project="example", path=renamed)["binary_id"]
    assert new != old
    result = import_evidence_archive(store, project="example", binary_id=new,
        path=archive, archive_root=tmp_path)
    assert result["binary_id"] == new and result["artifacts"] == 1
    assert store.query_re_evidence(project="example", binary_id=new)[0]["content_hash"] == hashlib.sha256(raw).hexdigest()


def test_repair_requires_explicit_summary_policy_and_records_both_values(pg_conn, tmp_path):
    from pseudolife_memory.re_evidence_repair import reconcile_scopes
    from pseudolife_memory.storage.re_evidence import ReEvidenceArchiveStorage

    store = ReEvidenceArchiveStorage(pg_conn)
    binary = tmp_path / "client.exe"
    binary.write_bytes(b"original executable bytes")
    canonical = store.register_re_build(project="example", path=binary)["binary_id"]
    artifact_id = store.insert_re_evidence({**parse_evidence_bytes(b'{}', source_path="capture.json"),
        "project": "example", "binary_id": canonical, "kind": "capture", "summary": "canonical summary"})
    typo = canonical + "b"
    pg_conn.execute(
        "INSERT INTO re_evidence_artifacts (project,binary_id,kind,locator,source_path,content_hash,"
        "raw_bytes,payload,payload_keys,addresses,summary,ingested_at) SELECT project,%s,kind,locator,"
        "source_path,content_hash,raw_bytes,payload,payload_keys,addresses,'historical summary',1 "
        "FROM re_evidence_artifacts WHERE id=%s", (typo, artifact_id))
    pg_conn.commit()
    with pytest.raises(EvidenceInputError, match="summary"):
        reconcile_scopes(store, project="example", binary_id=canonical,
            source_scopes=[typo], archive_path=tmp_path / "refused.json")
    result = reconcile_scopes(store, project="example", binary_id=canonical,
        source_scopes=[typo], archive_path=tmp_path / "approved.json", allow_summary_differences=True)
    assert list(result["summary_differences"].values()) == [{
        "canonical_artifact_id": artifact_id, "historical": "historical summary", "canonical": "canonical summary"}]
