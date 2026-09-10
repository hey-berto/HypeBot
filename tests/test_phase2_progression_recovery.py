from __future__ import annotations

from pathlib import Path

import pytest

from tests.phase2_recovery_harness import run_suite


@pytest.fixture(scope="module")
def acceptance(tmp_path_factory):
    return run_suite(tmp_path_factory.mktemp("non_scored_recovery") / "evidence")


def llm_trades(checkpoint):
    return [row for row in checkpoint["trades"] if row["strategy_id"] == "LLM_V1"]


def test_real_scheduler_progresses_pending_after_process_restart(acceptance):
    case = acceptance["cases"]["normal_progression"]
    trace = case["trace"]
    for snapshot in case["final"]["snapshot_lineage"]:
        assert snapshot["scoreable"]
        assert len(snapshot["strategies"]) == 5
        assert snapshot["detectors"] == ["SETUP_DETECTOR_V1"]
        assert snapshot["snapshot_schema"] == snapshot["feature_schema"] == "1.0.0"
    assert trace[0]["status"] == "COMPLETE"
    assert llm_trades(trace[0]["checkpoint"])[0]["status"] == "PENDING_ENTRY"
    assert trace[1]["status"] == "DUPLICATE_SKIPPED"
    assert trace[1]["checkpoint"]["counts"] == trace[0]["checkpoint"]["counts"]
    assert trace[2]["pid"] != trace[0]["pid"]
    assert llm_trades(trace[2]["checkpoint"])[0]["status"] == "OPEN"
    assert trace[3]["status"] == "DUPLICATE_SKIPPED"
    assert trace[3]["checkpoint"]["counts"] == trace[2]["checkpoint"]["counts"]
    trade = llm_trades(trace[4]["checkpoint"])[0]
    assert trade["status"] == "CLOSED"
    assert trade["exit_reason"] == "TARGET"
    fills = [
        row
        for row in case["final"]["fills"]
        if row["paper_trade_id"] == trade["paper_trade_id"]
    ]
    assert [row["fill_type"] for row in fills] == ["ENTRY", "EXIT_TARGET"]
    assert (
        len(
            [
                row
                for row in case["final"]["orders"]
                if row["paper_trade_id"] == trade["paper_trade_id"]
            ]
        )
        == 1
    )


def test_frozen_pending_no_expiry_and_entry_anchored_ttl(acceptance):
    pending = acceptance["cases"]["pending_without_eligible_bar"]
    assert llm_trades(pending["final"])[0]["status"] == "PENDING_ENTRY"
    assert not pending["final"]["fills"]
    assert len(pending["final"]["cycles"]) == 6
    ttl = llm_trades(acceptance["cases"]["ttl_exit"]["final"])[0]
    assert ttl["status"] == "CLOSED" and ttl["exit_reason"] == "TTL"
    adverse = llm_trades(acceptance["cases"]["adverse_first"]["final"])[0]
    assert adverse["status"] == "CLOSED" and adverse["exit_reason"] == "STOP"
    assert "INTRABAR_ORDER_AMBIGUOUS" in adverse["flags_json"]


def test_transport_only_retry_policy_and_exact_raw_response_lineage(acceptance):
    retry = acceptance["cases"]["transport_retry"]["trace"][0]["checkpoint"]
    assert retry["counts"]["llm_invocation_attempts"] == 2
    assert [row["provider_status"] for row in retry["raw_audit"]] == [
        "TIMEOUT",
        "VALID",
    ]
    assert retry["parsed_raw_lineage"][0]["exact_parsed_raw_lineage"]
    exhausted = acceptance["cases"]["transport_exhaustion"]["trace"][0]["checkpoint"]
    assert exhausted["counts"]["llm_invocation_attempts"] == 2
    assert exhausted["counts"]["llm_decisions"] == 1
    malformed = acceptance["cases"]["malformed_fail_closed"]["trace"][0]["checkpoint"]
    assert malformed["counts"]["llm_invocation_attempts"] == 1
    assert malformed["counts"]["llm_decisions"] == 1
    for case in acceptance["cases"].values():
        final = case["final"]
        assert final["attempt_policy_violation_groups"] == 0
        assert final["all_record_model_identities_match_frozen"]
        assert final["llm_decision_integrity_hashes_match"]
        assert final["snapshot_hashes_match"]
        for attempt in final["raw_audit"]:
            if attempt["provider_status"] in {"TIMEOUT", "TRANSPORT_ERROR"}:
                assert not attempt["raw_retained"]
            else:
                assert attempt["raw_retained"] and attempt["raw_sha256_matches"]
            assert attempt["attempt_integrity_hash_matches"]
            assert attempt["tool_calls"] == 0
            if attempt["provider_status"] == "VALID":
                assert attempt["valid_schema_and_input_lineage"]
        assert all(
            item["exact_parsed_raw_lineage"] for item in final["parsed_raw_lineage"]
        )


def test_integrity_no_duplicates_no_backfill_and_quarter_hour_alignment(acceptance):
    for case in acceptance["cases"].values():
        final = case["final"]
        assert final["integrity"] == "ok"
        assert final["journal_mode"] == "wal"
        assert final["foreign_key_violations"] == 0
        assert not any(final["duplicate_groups"].values())
        assert final["boundaries_before_fixture_activation"] == 0
        assert final["utc_quarter_hour_aligned"]
        assert Path(final["database"]).name.startswith("NON_SCORED_")
    assert acceptance["identities"]["production_config_gates"] == {
        "activation_authorized": False,
        "evidence_collection_enabled": False,
    }


def test_interrupted_cycles_recover_without_recollection_or_duplicates(acceptance):
    for name in (
        "interrupted_before_collection",
        "interrupted_after_trade",
        "interrupted_after_attempt",
    ):
        trace = acceptance["cases"][name]["trace"]
        assert trace[0]["exit_code"] == 87
        assert trace[1]["status"] == "RECOVERY_EXCLUDED"
        assert trace[1]["provider_fixture_calls"] == 0
        assert trace[2]["status"] == "COMPLETE"
        assert acceptance["cases"][name]["final"]["running_cycles"] == 0
    orphan = acceptance["cases"]["interrupted_after_trade"]["final"]
    assert orphan["trades_missing_orders"] == 0
    unfinished = acceptance["cases"]["interrupted_after_attempt"]["final"]
    assert unfinished["attempts_without_final_decision"] == 0
    recovered_lineage = [
        row
        for row in unfinished["parsed_raw_lineage"]
        if row["recovered_from_persisted_raw_response"]
    ]
    assert len(recovered_lineage) == 1
    assert recovered_lineage[0]["provider_reinvoked"] is False
    assert len(recovered_lineage[0]["source_attempt_integrity_hash"]) == 64
    recovered = [
        row
        for row in unfinished["recovery_events"]
        if row["event_type"] == "INTERRUPTED_CYCLE_FINALIZED"
    ]
    assert recovered
    assert acceptance["disposition"] == "PHASE_2_SIMULATOR_ACCEPTANCE_PASSED"
    assert not acceptance["blocking_witnesses"]


def test_entry_fill_crash_is_observable_and_does_not_duplicate_fill_on_recovery(
    acceptance,
):
    trace = acceptance["cases"]["interrupted_during_entry"]["trace"]
    assert trace[1]["exit_code"] == 87
    assert llm_trades(trace[1]["checkpoint"])[0]["status"] == "PENDING_ENTRY"
    trade_id = llm_trades(trace[1]["checkpoint"])[0]["paper_trade_id"]
    assert not [
        row
        for row in trace[1]["checkpoint"]["fills"]
        if row["paper_trade_id"] == trade_id
    ]
    assert trace[2]["status"] == "RECOVERY_EXCLUDED"
    assert trace[3]["status"] == "COMPLETE"
    assert trace[3]["checkpoint"]["duplicate_groups"]["paper_fills"] == 0
    assert trace[3]["checkpoint"]["running_cycles"] == 0
    assert (
        len(
            [
                row
                for row in trace[3]["checkpoint"]["fills"]
                if row["paper_trade_id"] == trade_id
            ]
        )
        == 2
    )


def test_double_crash_recovery_is_idempotent_and_never_recalls_provider(acceptance):
    case = acceptance["cases"]["double_crash_raw_recovery"]
    trace = case["trace"]
    assert trace[0]["exit_code"] == 87
    assert trace[1]["exit_code"] == 87
    assert trace[2]["status"] == "RECOVERY_EXCLUDED"
    assert trace[2]["provider_fixture_calls"] == 0
    final = case["final"]
    assert final["running_cycles"] == 0
    assert final["attempts_without_final_decision"] == 0
    assert final["counts"]["llm_invocation_attempts"] == 2
    assert not any(final["duplicate_groups"].values())
    assert final["integrity"] == "ok"
