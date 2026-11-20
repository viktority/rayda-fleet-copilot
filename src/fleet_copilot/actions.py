"""Durable human decisions. The model has no access to the decision method."""

import json
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import Field, model_validator

from .analytics import Analytics
from .domain import FleetError, Principal, StrictModel, utcnow
from .store import Store, canonical, digest


class ProposalRequest(StrictModel):
    action: Literal[
        "open_remediation_ticket", "flag_device_for_replacement", "notify_employee", "create_upgrade_order"
    ]
    device_id: str = Field(min_length=1, max_length=100)
    finding_ids: list[str] = Field(min_length=1, max_length=10)


class Decision(StrictModel):
    decision: Literal["approve", "reject"]
    expected_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmed: bool = Field(strict=True)

    @model_validator(mode="after")
    def explicit_confirmation(self):
        if self.confirmed is not True:
            raise ValueError("Explicit confirmation is required")
        return self


class Actions:
    def __init__(self, store: Store, analytics: Analytics):
        self.store, self.analytics = store, analytics

    def propose(self, principal: Principal, request: ProposalRequest, run_id: str) -> dict:
        try:
            return self._propose(principal, request, run_id)
        except FleetError as exc:
            self.store.audit(
                principal, "proposal_denied", {"action": request.action, "code": exc.code, "run_id": run_id}
            )
            raise

    def _propose(self, principal, request, run_id):
        if principal.role not in ("operator", "admin"):
            raise FleetError("forbidden", "Operator role required to propose actions", 403)
        if request.action == "create_upgrade_order":
            raise FleetError(
                "missing_compatibility_policy",
                "Upgrade orders require a trusted component compatibility and purchasing catalog; none is supplied",
            )
        trusted = self.analytics.actionable(principal, request.device_id)
        selected = [trusted.get(fid) for fid in set(request.finding_ids)]
        if not selected or any(f is None for f in selected):
            raise FleetError(
                "insufficient_evidence",
                "Only current, canonical, actionable findings for this device can justify a proposal",
            )
        selected.sort(key=lambda f: f.id)
        kinds = {f.kind for f in selected}
        if (
            request.action == "flag_device_for_replacement"
            and not {"persistent_ram", "battery_service"} <= kinds
        ):
            raise FleetError(
                "insufficient_evidence",
                "Replacement review policy requires both persistent RAM utilization and a reported battery service condition",
            )
        latest = self.store.rows(principal, request.device_id)[-1]
        sha = latest["_sha"]
        if any(e.source_sha256 != sha for f in selected for e in f.evidence):
            raise FleetError(
                "stale_evidence", "Telemetry changed while preparing the proposal; query again", 409
            )
        proposal_id = (
            "P-"
            + digest(
                [
                    principal.model_dump(),
                    run_id,
                    request.action,
                    request.device_id,
                    sorted(request.finding_ids),
                ]
            )[:24]
        )
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT * FROM proposals WHERE company=? AND id=?", (principal.company_id, proposal_id)
            ).fetchone()
            if existing:
                return self._decode(existing)
            if self.store.source_sha(db) != sha:
                raise FleetError(
                    "stale_evidence", "Telemetry changed while preparing the proposal; query again", 409
                )
            created = utcnow()
            expires = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
            payload = {
                "id": proposal_id,
                "company_id": principal.company_id,
                "run_id": run_id,
                "action": request.action,
                "device_id": request.device_id,
                "hostname": latest["os"]["hostname"],
                "employee_id": latest["employee_id"] if request.action == "notify_employee" else None,
                "source_sha256": sha,
                "findings": [f.model_dump() for f in selected],
                "justification": "\n".join(f.text for f in selected),
                "mode": "simulation",
                "created_at": created,
                "expires_at": expires,
                "scope": "Flag for assessment only; no replacement or purchase"
                if request.action == "flag_device_for_replacement"
                else "Record a simulated action; no external delivery or system modification",
            }
            payload_digest = digest(payload)
            db.execute(
                "INSERT INTO proposals VALUES (?,?,?,?,?,?,?,?,NULL)",
                (
                    proposal_id,
                    principal.company_id,
                    principal.actor_id,
                    payload_digest,
                    "pending",
                    canonical(payload),
                    created,
                    expires,
                ),
            )
            self.store.audit_in(
                db,
                principal,
                "proposal_created",
                {"proposal_id": proposal_id, "digest": payload_digest, "run_id": run_id},
            )
        return self.get(principal, proposal_id)

    @staticmethod
    def _decode(row):
        return {
            **json.loads(row["payload"]),
            "digest": row["digest"],
            "status": row["status"],
            "decided_by": row["decided_by"],
        }

    def get(self, principal: Principal, proposal_id: str) -> dict:
        with self.store.connection() as db:
            row = db.execute(
                "SELECT * FROM proposals WHERE company=? AND id=?", (principal.company_id, proposal_id)
            ).fetchone()
        if row is None:
            raise FleetError("not_found", "Proposal not found", 404)
        return self._decode(row)

    def list(self, principal: Principal) -> list[dict]:
        with self.store.connection() as db:
            rows = db.execute(
                "SELECT * FROM proposals WHERE company=? ORDER BY created_at", (principal.company_id,)
            ).fetchall()
        return [self._decode(row) for row in rows]

    def decide(self, principal: Principal, proposal_id: str, decision: Decision) -> dict:
        try:
            return self._decide(principal, proposal_id, decision)
        except FleetError as exc:
            self.store.audit(principal, "decision_denied", {"proposal_id": proposal_id, "code": exc.code})
            raise

    def _decide(self, principal, proposal_id, decision):
        if principal.role != "admin":
            raise FleetError("forbidden", "Administrator role required to decide proposals", 403)
        # Serialize check + decision + effect + audit in one transaction. No external calls here.
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM proposals WHERE company=? AND id=?", (principal.company_id, proposal_id)
            ).fetchone()
            if row is None:
                raise FleetError("not_found", "Proposal not found", 404)
            payload = json.loads(row["payload"])
            if digest(payload) != row["digest"] or row["digest"] != decision.expected_digest:
                raise FleetError(
                    "proposal_changed", "Proposal digest does not match the reviewed payload", 409
                )
            if row["status"] != "pending":
                terminal = "executed" if decision.decision == "approve" else "rejected"
                if row["status"] != terminal:
                    raise FleetError("already_decided", "Proposal already has a different decision", 409)
                effect = db.execute(
                    "SELECT payload FROM effects WHERE proposal_id=? AND company=?",
                    (proposal_id, principal.company_id),
                ).fetchone()
                self.store.audit_in(
                    db, principal, "decision_replayed", {"proposal_id": proposal_id, "status": terminal}
                )
                return {
                    "proposal_id": proposal_id,
                    "status": terminal,
                    "effect": json.loads(effect[0]) if effect else None,
                    "replayed": True,
                }
            if decision.decision == "approve":
                if datetime.fromisoformat(row["expires_at"]) <= datetime.now(timezone.utc):
                    raise FleetError(
                        "proposal_expired", "Proposal expired; request a new evidence review", 409
                    )
                if self.store.source_sha(db) != payload["source_sha256"]:
                    raise FleetError("stale_evidence", "Dataset changed; request a new proposal", 409)
                effect = {
                    "id": "SIM-" + proposal_id,
                    "action": payload["action"],
                    "device_id": payload["device_id"],
                    "employee_id": payload["employee_id"],
                    "mode": "simulation",
                    "external_side_effect": False,
                    "recorded_at": utcnow(),
                    "justification": payload["justification"],
                }
                db.execute(
                    "INSERT INTO effects VALUES (?,?,?)",
                    (proposal_id, principal.company_id, canonical(effect)),
                )
                status = "executed"
            else:
                effect, status = None, "rejected"
            db.execute(
                "UPDATE proposals SET status=?,decided_by=? WHERE company=? AND id=?",
                (status, principal.actor_id, principal.company_id, proposal_id),
            )
            self.store.audit_in(
                db,
                principal,
                "proposal_approved" if decision.decision == "approve" else "proposal_rejected",
                {"proposal_id": proposal_id, "digest": row["digest"]},
            )
            if effect:
                self.store.audit_in(
                    db,
                    principal,
                    "simulated_action_executed",
                    {"proposal_id": proposal_id, "effect_id": effect["id"]},
                )
            return {"proposal_id": proposal_id, "status": status, "effect": effect, "replayed": False}
