import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from conftest import DATASET

from fleet_copilot.actions import Actions, Decision, ProposalRequest
from fleet_copilot.analytics import Analytics, Query
from fleet_copilot.domain import FleetError, Principal
from fleet_copilot.store import Store, canonical, digest


def proposal(actions, analytics, operator, action="open_remediation_ticket"):
    fact = analytics.query(operator, Query(kind="low_disk", device_id="MT7PJB7N5LRE"))[1]
    request = ProposalRequest(action=action, device_id=fact.device_id, finding_ids=[fact.id])
    return actions.propose(operator, request, "test-run")


def effects(store):
    with store.connection() as db:
        return db.execute("SELECT COUNT(*) FROM effects").fetchone()[0]


def test_proposing_never_executes_and_repeated_call_is_idempotent(store, actions, analytics, operator):
    p = proposal(actions, analytics, operator)
    assert p["status"] == "pending"
    assert p["mode"] == "simulation"
    assert effects(store) == 0
    assert proposal(actions, analytics, operator) == p


def test_approval_persists_across_restart_and_executes_once(store, actions, analytics, operator, admin):
    p = proposal(actions, analytics, operator)
    decision = Decision(decision="approve", expected_digest=p["digest"], confirmed=True)
    restarted = Store(store.path)
    actions2 = Actions(restarted, Analytics(restarted))
    first = actions2.decide(admin, p["id"], decision)
    repeat = actions2.decide(admin, p["id"], decision)
    assert first["status"] == "executed"
    assert first["effect"]["external_side_effect"] is False
    assert repeat["effect"] == first["effect"]
    assert repeat["replayed"] is True
    assert effects(store) == 1
    assert {x["event"] for x in store.audit_log(admin)} >= {
        "proposal_created",
        "proposal_approved",
        "simulated_action_executed",
        "decision_replayed",
    }


def test_simultaneous_approvals_record_one_effect(store, actions, analytics, operator, admin):
    p = proposal(actions, analytics, operator)
    d = Decision(decision="approve", expected_digest=p["digest"], confirmed=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: actions.decide(admin, p["id"], d), range(2)))
    assert effects(store) == 1
    assert sorted(r["replayed"] for r in results) == [False, True]


def test_rejection_is_terminal_and_has_no_effect(store, actions, analytics, operator, admin):
    p = proposal(actions, analytics, operator)
    actions.decide(admin, p["id"], Decision(decision="reject", expected_digest=p["digest"], confirmed=True))
    with pytest.raises(FleetError) as exc:
        actions.decide(
            admin, p["id"], Decision(decision="approve", expected_digest=p["digest"], confirmed=True)
        )
    assert exc.value.code == "already_decided"
    assert effects(store) == 0


def test_role_and_cross_tenant_guards(store, actions, analytics, operator, admin):
    p = proposal(actions, analytics, operator)
    d = Decision(decision="approve", expected_digest=p["digest"], confirmed=True)
    with pytest.raises(FleetError) as exc:
        actions.decide(operator, p["id"], d)
    assert exc.value.status == 403
    foreign = Principal(company_id="globex-002", actor_id="foreign-admin", role="admin")
    for function in (lambda: actions.get(foreign, p["id"]), lambda: actions.decide(foreign, p["id"], d)):
        with pytest.raises(FleetError) as exc:
            function()
        assert exc.value.status == 404
    assert actions.list(foreign) == []
    assert effects(store) == 0


def test_tampered_review_digest_is_refused(store, actions, analytics, operator, admin):
    p = proposal(actions, analytics, operator)
    with pytest.raises(FleetError) as exc:
        actions.decide(admin, p["id"], Decision(decision="approve", expected_digest="0" * 64, confirmed=True))
    assert exc.value.code == "proposal_changed"
    assert effects(store) == 0


def test_expired_proposal_cannot_execute(store, actions, analytics, operator, admin):
    p = proposal(actions, analytics, operator)
    with store.connection() as db:
        row = db.execute("SELECT payload FROM proposals WHERE id=?", (p["id"],)).fetchone()
        body = json.loads(row[0])
        expired = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        body["expires_at"] = expired
        d = digest(body)
        db.execute(
            "UPDATE proposals SET expires_at=?,payload=?,digest=? WHERE id=?",
            (expired, canonical(body), d, p["id"]),
        )
    with pytest.raises(FleetError) as exc:
        actions.decide(admin, p["id"], Decision(decision="approve", expected_digest=d, confirmed=True))
    assert exc.value.code == "proposal_expired"
    assert effects(store) == 0


def test_changed_dataset_invalidates_approval(store, actions, analytics, operator, admin, tmp_path):
    p = proposal(actions, analytics, operator)
    changed = tmp_path / "updated.ndjson"
    changed.write_bytes(DATASET.read_bytes() + b"\n")
    store.ingest(changed)
    with pytest.raises(FleetError) as exc:
        actions.decide(
            admin, p["id"], Decision(decision="approve", expected_digest=p["digest"], confirmed=True)
        )
    assert exc.value.code == "stale_evidence"
    assert effects(store) == 0


def test_fabricated_evidence_and_missing_catalog_are_refused(actions, operator):
    with pytest.raises(FleetError) as exc:
        actions.propose(
            operator,
            ProposalRequest(
                action="open_remediation_ticket", device_id="MT7PJB7N5LRE", finding_ids=["F-fabricated"]
            ),
            "run",
        )
    assert exc.value.code == "insufficient_evidence"
    with pytest.raises(FleetError) as exc:
        actions.propose(
            operator,
            ProposalRequest(
                action="create_upgrade_order", device_id="MT7PJB7N5LRE", finding_ids=["F-fabricated"]
            ),
            "run",
        )
    assert exc.value.code == "missing_compatibility_policy"


def test_replacement_review_needs_two_independent_signals(actions, analytics, operator):
    with pytest.raises(FleetError) as exc:
        proposal(actions, analytics, operator, action="flag_device_for_replacement")
    assert exc.value.code == "insufficient_evidence"
    facts = analytics.actionable(operator, "M4XVHUV1MEPZ")
    ids = [f.id for f in facts.values() if f.kind in ("persistent_ram", "battery_service")]
    p = actions.propose(
        operator,
        ProposalRequest(action="flag_device_for_replacement", device_id="M4XVHUV1MEPZ", finding_ids=ids),
        "replacement-review",
    )
    assert p["status"] == "pending"
    assert "assessment only" in p["scope"]


def test_notification_recipient_comes_from_telemetry(actions, analytics, operator):
    p = proposal(actions, analytics, operator, action="notify_employee")
    assert p["employee_id"] == "emp-acme-1003"
    assert p["status"] == "pending"


def test_confirmation_and_audit_are_enforced(store, operator, admin):
    with pytest.raises(ValueError):
        Decision(decision="approve", expected_digest="0" * 64, confirmed=False)
    with pytest.raises(ValueError):
        Decision(decision="approve", expected_digest="0" * 64, confirmed=1)
    store.audit(operator, "test", {})
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        with store.connection() as db:
            db.execute("DELETE FROM audit")
    with pytest.raises(FleetError):
        store.audit_log(operator)
