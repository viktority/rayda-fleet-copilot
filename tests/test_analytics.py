import json
from copy import deepcopy
from datetime import date

import pytest
from conftest import DATASET

from fleet_copilot.analytics import Query, Trend
from fleet_copilot.domain import FleetError, Principal


def test_exact_latest_counts_and_device_sets(analytics, operator):
    facts = analytics.query(operator, Query(kind="low_disk"))
    assert facts[0].values["count"] == 2
    assert {f.device_id for f in facts[1:]} == {"8NM23J95R5I6", "MT7PJB7N5LRE"}
    assert len(facts[0].evidence) == 10  # Devices, not 300 snapshots.
    assert facts[0].values["as_of"].startswith("2026-06-12")
    assert analytics.query(operator, Query(kind="compliance", severity="high"))[0].values["count"] == 0
    assert (
        analytics.query(operator, Query(kind="os_older", platform="darwin", version_major=15))[0].values[
            "count"
        ]
        == 4
    )


def test_historical_as_of_not_latest(analytics, operator):
    facts = analytics.query(operator, Query(kind="low_disk", as_of=date(2026, 5, 14)))
    assert facts[0].values["count"] == 1
    assert facts[1].device_id == "MT7PJB7N5LRE"
    with pytest.raises(FleetError, match="No observations"):
        analytics.query(operator, Query(kind="inventory", as_of=date(2026, 1, 1)))


def test_foreign_and_unknown_device_are_indistinguishable(analytics, operator):
    for device in ("E4V0SB5I0MYU", "nonexistent", "' OR 1=1 --"):
        with pytest.raises(FleetError) as exc:
            analytics.query(operator, Query(kind="inventory", device_id=device))
        assert (exc.value.code, exc.value.status, exc.value.message) == (
            "not_found",
            404,
            "No accessible telemetry found",
        )


def test_persistence_not_mean_or_observation_count(analytics, operator):
    result = analytics.trends(operator, Trend(kind="ram"))
    assert [(f.device_id, f.values["hits"]) for f in result[1:]] == [("M4XVHUV1MEPZ", 30)]
    globex = Principal(company_id="globex-002", actor_id="viewer")
    assert analytics.trends(globex, Trend(kind="ram"))[0].values["finding_count"] == 0
    assert analytics.trends(globex, Trend(kind="ram", fraction=0.75))[0].values["finding_count"] == 1


def test_battery_unknown_and_cycle_anomalies(analytics, operator):
    missing = analytics.trends(operator, Trend(kind="battery", device_id="MT7PJB7N5LRE"))[1]
    assert missing.values["missing_observations"] == 5
    assert missing.values["latest_snapshot_has_battery"] is False
    assert missing.values["last_battery_at"] < missing.values["end"]
    assert missing.values["cycle_decreases"] > 0
    assert "no health percentage" in missing.text
    desktop = analytics.trends(operator, Trend(kind="battery", device_id="DLT35MHGTH2D"))[1]
    assert desktop.kind == "battery_unknown"


def test_battery_eol_risk_needs_corroborating_signals(analytics, operator):
    facts = analytics.trends(operator, Trend(kind="battery_eol"))
    assert facts[0].values["finding_count"] == 3
    risky = {f.device_id: f for f in facts[1:]}
    assert set(risky) == {"7VP16KHV88LM", "8NM23J95R5I6", "M4XVHUV1MEPZ"}
    dell = risky["M4XVHUV1MEPZ"]
    assert dell.values["signals"] == {
        "service_condition": True,
        "capacity_decline": True,
        "high_cycle_count": False,
    }
    assert all(len(f.evidence) <= 3 for f in risky.values())
    assert all("not a remaining-life prediction" in f.text for f in risky.values())
    globex = Principal(company_id="globex-002", actor_id="viewer")
    assert analytics.trends(globex, Trend(kind="battery_eol"))[0].values["finding_count"] == 0


@pytest.mark.parametrize(("cycles", "flagged"), [(999, False), (1000, True)])
def test_battery_eol_single_signal_is_not_enough(store, analytics, operator, tmp_path, cycles, flagged):
    template = json.loads(DATASET.read_text().splitlines()[0])
    rows = []
    for day in range(10):
        row = deepcopy(template)
        row["collected_at"] = f"2026-05-{14 + day:02d}T09:00:00Z"
        row["battery"].update(condition="Normal", cycle_count=cycles, full_charge_capacity=5000 - 20 * day)
        rows.append(row)
    fixture = tmp_path / "synthetic-battery.ndjson"
    fixture.write_text("\n".join(json.dumps(r) for r in rows))
    store.ingest(fixture)
    # Capacity falls 3.6% in every variant; only the cycle-count boundary adds a second signal.
    facts = analytics.trends(operator, Trend(kind="battery_eol"))
    assert facts[0].values["finding_count"] == (1 if flagged else 0)


def test_drift_has_before_and_after_evidence(analytics, operator):
    facts = analytics.trends(operator, Trend(kind="compliance"))[1:]
    assert len(facts) == 5
    assert all(f.values["before"] == "pass" and f.values["after"] == "fail" for f in facts)
    assert all(
        len(f.evidence) == 2 and f.evidence[0].collected_at < f.evidence[1].collected_at for f in facts
    )


def test_strict_threshold_and_positive_high_severity_synthetic_fixture(store, analytics, operator, tmp_path):
    row = json.loads(DATASET.read_text().splitlines()[0])
    row["disk_volumes"][0].update(size_bytes=1000, available_bytes=100)
    row["compliance_results"][0]["status"] = "fail"
    row["disk_volumes"][0]["encrypted"] = False
    fixture = tmp_path / "synthetic-boundary.ndjson"
    fixture.write_text(json.dumps(row) + "\n")
    store.ingest(fixture)
    assert analytics.query(operator, Query(kind="low_disk"))[0].values["count"] == 0
    assert analytics.query(operator, Query(kind="compliance", severity="high"))[0].values["count"] == 1


def test_synthetic_colliding_device_ids_do_not_cross_tenants(store, analytics, operator, tmp_path):
    row = json.loads(DATASET.read_text().splitlines()[0])
    other = deepcopy(row)
    other["company_id"] = "globex-002"
    other["disk_volumes"][0]["available_bytes"] = 1
    fixture = tmp_path / "synthetic-collisions.ndjson"
    fixture.write_text("\n".join(json.dumps(r) for r in [row, other]))
    store.ingest(fixture)
    assert analytics.query(operator, Query(kind="low_disk"))[0].values["count"] == 0
    assert len(store.rows(operator)) == 1


def test_invalid_ingestion_is_atomic(store, tmp_path, operator):
    before = store.source_sha()
    row = json.loads(DATASET.read_text().splitlines()[0])
    row["memory"]["used_memory_bytes"] = -1
    fixture = tmp_path / "invalid.ndjson"
    fixture.write_text(json.dumps(row))
    with pytest.raises(FleetError):
        store.ingest(fixture)
    assert store.source_sha() == before
    assert len(store.rows(operator)) == 300


def test_laptop_filter_excludes_desktops(analytics, operator):
    facts = analytics.query(operator, Query(kind="inventory", device_type="laptop"))
    assert facts[0].values["scanned_devices"] == 9
    assert "DLT35MHGTH2D" not in {f.device_id for f in facts[1:]}


def test_requested_window_counts_missing_edge_days(analytics, operator):
    facts = analytics.trends(operator, Trend(kind="ram", start=date(2026, 5, 1), end=date(2026, 6, 12)))
    assert facts[0].values["finding_count"] == 0  # 30 high days / 43 requested days < 80%.


def test_multiple_same_day_snapshots_do_not_fake_persistence(store, analytics, operator, tmp_path):
    template = json.loads(DATASET.read_text().splitlines()[0])
    template["memory"]["used_memory_bytes"] = template["memory"]["total_memory_bytes"]
    template["memory"]["free_memory_bytes"] = 0
    rows = []
    for hour in range(8):
        row = deepcopy(template)
        row["collected_at"] = f"2026-05-14T{hour:02d}:00:00Z"
        rows.append(row)
    fixture = tmp_path / "synthetic-same-day.ndjson"
    fixture.write_text("\n".join(json.dumps(r) for r in rows))
    store.ingest(fixture)
    assert analytics.trends(operator, Trend(kind="ram"))[0].values["finding_count"] == 0


@pytest.mark.parametrize(
    "args",
    [
        {"kind": "low_disk", "company_id": "globex-002"},
        {"kind": "inventory", "threshold": 10},
        {"kind": "os_older", "platform": "win32", "version_major": 15},
        {"kind": "low_disk", "threshold": float("nan")},
    ],
)
def test_reject_invalid_and_tenant_override_arguments(args):
    with pytest.raises(ValueError):
        Query.model_validate(args)
