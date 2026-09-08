"""Ambiguous/oversized inputs must not become immutable evidence payloads."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from pseudolife_memory.re_evidence import EvidenceInputError, parse_evidence_bytes, parse_evidence_file


@pytest.mark.parametrize("raw", [
    b'{"address":"00401000","address":"00402000"}',
    b'{"function":{"address":"00401000","address":"00402000"}}',
])
def test_duplicate_fields_are_not_silently_discarded(raw):
    with pytest.raises(EvidenceInputError, match="duplicate"):
        parse_evidence_bytes(raw, source_path="synthetic.json")


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity", "1e999"])
def test_nonfinite_numbers_are_rejected_before_storage(token):
    with pytest.raises(EvidenceInputError, match="finite"):
        parse_evidence_bytes(('{"number":' + token + '}').encode(), source_path="synthetic.json")


def test_nested_input_has_a_controlled_error():
    raw = b'{"nested":' + b'[' * 1500 + b'0' + b']' * 1500 + b'}'
    with pytest.raises(EvidenceInputError, match="nest"):
        parse_evidence_bytes(raw, source_path="synthetic.json")


def test_byte_parser_enforces_the_same_size_limit(monkeypatch):
    import pseudolife_memory.re_evidence as evidence
    monkeypatch.setattr(evidence, "MAX_EVIDENCE_BYTES", 16)
    with pytest.raises(EvidenceInputError, match="maximum"):
        parse_evidence_bytes(b'{}' + b' ' * 32, source_path="synthetic.json")


def test_growing_file_does_not_bypass_the_read_limit(tmp_path, monkeypatch):
    path = tmp_path / "growing.json"
    path.write_bytes(b'{}' + b' ' * 32)
    real_stat = Path.stat

    def stale_stat(self, *args, **kwargs):
        result = real_stat(self, *args, **kwargs)
        if self == path:
            fields = list(result)
            fields[6] = 2
            return os.stat_result(fields)
        return result

    monkeypatch.setattr(Path, "stat", stale_stat)
    with pytest.raises(EvidenceInputError, match="maximum"):
        parse_evidence_file(path, max_bytes=16)


def test_valid_json_keeps_exact_bytes_hash_and_addresses():
    raw = b'\xef\xbb\xbf{ "function": {"address":"00401000"}, "value":1.25, "other":{"value":2}}\r\n'
    parsed = parse_evidence_bytes(raw, source_path="synthetic.json")
    assert parsed["raw_bytes"] == raw
    assert parsed["content_hash"] == hashlib.sha256(raw).hexdigest()
    assert parsed["addresses"] == ["00401000"]
    assert parsed["payload"] == json.loads(raw.decode("utf-8-sig"))
