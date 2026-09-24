import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from hype_autopilot.hashing import canonical_json, sha256_canonical
from hype_autopilot.phase2.storage import (
    INITIAL_EVIDENCE_WINDOW_RULE,
    Phase2Repository,
)
from hype_autopilot.phase3.day42_evidence_freeze import (
    EPOCH006_CUTOFF,
    FreezeRequest,
    freeze_day42_evidence,
)

ROOT = Path(__file__).resolve().parents[1]
BINDING = ROOT / "config/phase3/epoch006_operational_binding_v1.yaml"
RESEARCH_SHA = "41e9e9acc261fd69d32c2d811e9ab4dd556c4620"
ANCHOR = "2026-09-24T00:11:09.998160+00:00"
FIRST = "2026-09-24T00:15:00+00:00"


def _fixture_database(path: Path, *, epoch: str = "phase2_epoch_006", cutoff: datetime = EPOCH006_CUTOFF, future_cycle: bool = False) -> None:
    db = sqlite3.connect(path)
    Phase2Repository(db).initialize()
    anchor_body = {"phase2_epoch_id": epoch, "event_type": "PROSPECTIVE_START_ESTABLISHED", "source_identity": epoch, "occurred_at": ANCHOR, "details": {"prospective_start": ANCHOR}}
    anchor_hash = sha256_canonical(anchor_body)
    db.execute("INSERT INTO phase2_recovery_events VALUES (?,?,?,?,?,?,?)", ("anchor", epoch, "PROSPECTIVE_START_ESTABLISHED", epoch, ANCHOR, canonical_json(anchor_body), anchor_hash))
    window_body = {"window_id": "window", "phase2_epoch_id": epoch, "first_eligible_boundary": FIRST, "deployment_id": None, "prospective_start_integrity_hash": anchor_hash, "established_at": ANCHOR, "rule_version": INITIAL_EVIDENCE_WINDOW_RULE, "reason_code": "INITIAL_PROSPECTIVE_START"}
    db.execute("INSERT INTO phase2_evidence_windows VALUES (?,?,?,?,?,?,?,?,?,?)", ("window", epoch, FIRST, None, anchor_hash, ANCHOR, INITIAL_EVIDENCE_WINDOW_RULE, "INITIAL_PROSPECTIVE_START", canonical_json(window_body), sha256_canonical(window_body)))
    manifest = {"phase2_epoch_id": epoch, "git_commit_hash": RESEARCH_SHA, "config_hash": "config-hash", "prompt_hash": "prompt-hash", "output_schema_hash": "output-hash", "database_schema_hash": "schema-hash"}
    db.execute("INSERT INTO phase2_manifests VALUES (?,?,?,?,?,?,?,?,?,?,?)", ("manifest", epoch, epoch, ANCHOR, RESEARCH_SHA, "config-hash", "prompt-hash", "output-hash", "manifest-hash", "schema-hash", canonical_json(manifest)))
    db.execute("INSERT INTO research_cycles VALUES (?,?,?,?,?,?,?,?)", ("cutoff", cutoff.isoformat(), "SCORED_PROSPECTIVE", cutoff.isoformat(), cutoff.isoformat(), "COMPLETE", None, '{"scoreable":true}'))
    if future_cycle:
        db.execute("INSERT INTO research_cycles VALUES (?,?,?,?,?,?,?,?)", ("future", "2026-11-05T00:30:00+00:00", "SCORED_PROSPECTIVE", cutoff.isoformat(), None, "STARTED", None, "{}"))
    db.commit()
    db.close()


def _request(tmp_path: Path, source: Path, *, cutoff: datetime = EPOCH006_CUTOFF) -> FreezeRequest:
    config = tmp_path / "epoch006.yaml"
    config.write_text("simulator:\n  taker_fee_bps_per_side: 4.5\n  slippage_bps_per_side: 2.0\n", encoding="utf-8")
    return FreezeRequest(source, tmp_path / "package", BINDING, config, ROOT, {"worker": "synthetic"}, cutoff)


def test_freeze_preserves_cutoff_copy_manifest_and_source_mutation_does_not_change_it(tmp_path):
    source = tmp_path / "phase2_epoch_006.sqlite3"
    _fixture_database(source)
    manifest = freeze_day42_evidence(_request(tmp_path, source))
    frozen = tmp_path / "package/epoch006-cutoff.sqlite3"
    assert manifest["manifest_hash"] == sha256_canonical({key: value for key, value in manifest.items() if key != "manifest_hash"})
    assert manifest["cutoff_cycle"]["status"] == "COMPLETE"
    before = frozen.read_bytes()
    source_db = sqlite3.connect(source)
    source_db.execute("INSERT INTO research_cycles VALUES (?,?,?,?,?,?,?,?)", ("later", "2026-11-05T00:30:00+00:00", "SCORED_PROSPECTIVE", EPOCH006_CUTOFF.isoformat(), None, "STARTED", None, "{}"))
    source_db.commit()
    source_db.close()
    assert frozen.read_bytes() == before
    with pytest.raises(sqlite3.OperationalError):
        frozen_db = sqlite3.connect(f"file:{frozen}?mode=ro", uri=True)
        frozen_db.execute("INSERT INTO research_cycles VALUES ('x','x','x','x',NULL,'x',NULL,'{}')")


def test_freeze_rejects_wrong_epoch_cutoff_and_post_cutoff_cycle(tmp_path):
    wrong_epoch = tmp_path / "phase2_epoch_006.sqlite3"
    _fixture_database(wrong_epoch, epoch="wrong")
    with pytest.raises(ValueError, match="epoch"):
        freeze_day42_evidence(_request(tmp_path, wrong_epoch))
    wrong_cutoff = tmp_path / "other/phase2_epoch_006.sqlite3"
    wrong_cutoff.parent.mkdir()
    _fixture_database(wrong_cutoff, cutoff=datetime(2026, 11, 5, 0, 0, tzinfo=UTC))
    with pytest.raises(ValueError, match="cutoff boundary"):
        freeze_day42_evidence(_request(tmp_path / "other", wrong_cutoff))
    post_cutoff = tmp_path / "third/phase2_epoch_006.sqlite3"
    post_cutoff.parent.mkdir()
    _fixture_database(post_cutoff, future_cycle=True)
    with pytest.raises(ValueError, match="post-cutoff"):
        freeze_day42_evidence(_request(tmp_path / "third", post_cutoff))


def test_freeze_rejects_evaluator_sha_mismatch(tmp_path, monkeypatch):
    source = tmp_path / "phase2_epoch_006.sqlite3"
    _fixture_database(source)
    monkeypatch.setattr("hype_autopilot.phase3.day42_evidence_freeze.EXPECTED_EVALUATOR_SHA256", "bad")
    with pytest.raises(ValueError, match="evaluator SHA"):
        freeze_day42_evidence(_request(tmp_path, source))
