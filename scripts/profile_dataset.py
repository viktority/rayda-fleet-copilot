#!/usr/bin/env python3
"""Profile the assessment NDJSON without dependencies or modifying source records.

Run from any directory: python3 scripts/profile_dataset.py
Thresholds below are exploratory choices, not supplied company policy.
"""

import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
import hashlib
import json
from pathlib import Path
from statistics import mean


ROOT = Path(__file__).resolve().parents[1]
LOW_DISK_PERCENT = 10
HIGH_RAM_PERCENT = 85
PERSISTENT_FRACTION = 0.8


def evidence(line, row):
    return {
        "source_line": line,
        "company_id": row["company_id"],
        "device_id": row["device_id"],
        "collected_at": row["collected_at"],
    }


def walk(value, path=""):
    yield path or "$", value
    if isinstance(value, dict):
        for key, item in value.items():
            yield from walk(item, f"{path}.{key}" if path else key)
    elif isinstance(value, list):
        for item in value:
            yield from walk(item, f"{path}[]")


def profile(source):
    raw = source.read_bytes()
    numbered = [
        (n, json.loads(line))
        for n, line in enumerate(raw.decode("utf-8").splitlines(), 1)
        if line.strip()
    ]
    rows = [r for _, r in numbered]
    if not rows:
        raise ValueError("The dataset is empty")
    groups = defaultdict(list)
    field_presence = Counter()
    field_types = defaultdict(Counter)
    violations = defaultdict(list)
    by_device_tenant = defaultdict(set)
    by_employee_tenant = defaultdict(set)
    for line, r in numbered:
        timestamp = datetime.fromisoformat(r["collected_at"].replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            violations["timestamp_without_timezone"].append(evidence(line, r))
        groups[r["company_id"], r["device_id"]].append((line, r))
        by_device_tenant[r["device_id"]].add(r["company_id"])
        by_employee_tenant[r["employee_id"]].add(r["company_id"])
        seen = set()
        for field, value in walk(r):
            seen.add(field)
            field_types[field][type(value).__name__] += 1
            if value is None:
                violations["explicit_null"].append({**evidence(line, r), "field": field})
        field_presence.update(seen)
        m = r["memory"]
        checks = {
            "memory_sum_mismatch": m["used_memory_bytes"] + m["free_memory_bytes"] != m["total_memory_bytes"],
            "ram_total_mismatch": m["ram_bytes"] != m["total_memory_bytes"],
            "memory_bounds": not (0 <= m["used_memory_bytes"] <= m["total_memory_bytes"] and m["total_memory_bytes"] > 0),
            "device_serial_mismatch": r["device_id"] != r["device_identity"]["serial_number"],
            "duplicate_check_ids": len({c["check_id"] for c in r["compliance_results"]}) != len(r["compliance_results"]),
        }
        encryption = next((c for c in r["compliance_results"] if c["check_id"] == "disk_encryption"), None)
        checks["disk_bounds"] = any(not (0 <= d["available_bytes"] <= d["size_bytes"] and d["size_bytes"] > 0) for d in r["disk_volumes"])
        if encryption:
            checks["encryption_check_mismatch"] = (encryption["status"] == "pass") != all(d["encrypted"] for d in r["disk_volumes"])
        b = r.get("battery")
        if b:
            checks["battery_bounds"] = not (0 <= b["percentage"] <= 100 and b["cycle_count"] >= 0 and b["full_charge_capacity"] > 0)
        for name, failed in checks.items():
            if failed:
                violations[name].append(evidence(line, r))

    dates = sorted({r["collected_at"][:10] for r in rows})
    start, end = date.fromisoformat(dates[0]), date.fromisoformat(dates[-1])
    expected_days = {(start + timedelta(days=n)).isoformat() for n in range((end - start).days + 1)}
    devices = []
    cycle_decreases = []
    compliance_changes = []
    software_changes = []
    for (company, device), entries in sorted(groups.items()):
        entries.sort(key=lambda x: x[1]["collected_at"])
        first_line, first = entries[0]
        last_line, last = entries[-1]
        rs = [r for _, r in entries]
        disk = [min(d["available_bytes"] / d["size_bytes"] * 100 for d in r["disk_volumes"]) for r in rs]
        ram = [r["memory"]["used_memory_bytes"] / r["memory"]["total_memory_bytes"] * 100 for r in rs]
        bat = [(n, r) for n, r in entries if "battery" in r]
        for (pn, prev), (cn, cur) in zip(bat, bat[1:]):
            if cur["battery"]["cycle_count"] < prev["battery"]["cycle_count"]:
                cycle_decreases.append({"before": evidence(pn, prev), "after": evidence(cn, cur), "values": [prev["battery"]["cycle_count"], cur["battery"]["cycle_count"]]})
        for (pn, prev), (cn, cur) in zip(entries, entries[1:]):
            previous_checks = {c["check_id"]: c["status"] for c in prev["compliance_results"]}
            for c in cur["compliance_results"]:
                if previous_checks.get(c["check_id"]) != c["status"]:
                    compliance_changes.append({"before": evidence(pn, prev), "after": evidence(cn, cur), "check_id": c["check_id"], "from": previous_checks.get(c["check_id"]), "to": c["status"]})
            previous_software = {(s["name"], s["version"], s["publisher"]) for s in prev["installed_software"]}
            current_software = {(s["name"], s["version"], s["publisher"]) for s in cur["installed_software"]}
            if previous_software != current_software:
                software_changes.append({"before": evidence(pn, prev), "after": evidence(cn, cur), "added": sorted(current_software - previous_software), "removed": sorted(previous_software - current_software)})
        day_counts = Counter(r["collected_at"][:10] for r in rs)
        changing_fields = [field for field in ("company_id", "employee_id", "device_identity", "os", "agent_version") if len({json.dumps(r[field], sort_keys=True) for r in rs}) > 1]
        devices.append({
            "company_id": company, "device_id": device,
            "hostname": last["os"]["hostname"], "model": last["device_identity"]["model_name"],
            "employee_ids": sorted({r["employee_id"] for r in rs}),
            "platform": last["os"]["platform"], "os_version": last["os"]["product_version"],
            "snapshots": len(rs), "days": len(day_counts),
            "missing_dates": sorted(expected_days - set(day_counts)),
            "multiple_snapshot_dates": {d: n for d, n in day_counts.items() if n > 1},
            "changing_identity_or_os_fields": changing_fields,
            "first": evidence(first_line, first), "latest": evidence(last_line, last),
            "disk": {"first_free_percent": disk[0], "latest_free_percent": disk[-1], "observations_below_threshold": sum(p < LOW_DISK_PERCENT for p in disk), "evidence": [evidence(n, r) for (n, r), p in zip(entries, disk) if p < LOW_DISK_PERCENT]},
            "ram": {"mean_used_percent": mean(ram), "min_used_percent": min(ram), "max_used_percent": max(ram), "latest_used_percent": ram[-1], "observations_above_threshold": sum(p > HIGH_RAM_PERCENT for p in ram), "persistent": sum(p > HIGH_RAM_PERCENT for p in ram) / len(rs) >= PERSISTENT_FRACTION, "evidence": [evidence(n, r) for (n, r), p in zip(entries, ram) if p > HIGH_RAM_PERCENT]},
            "battery": {"observations": len(bat), "missing": len(rs) - len(bat), "latest_snapshot_has_battery": "battery" in last, "conditions": dict(Counter(r["battery"]["condition"] for _, r in bat)), "first_capacity": bat[0][1]["battery"]["full_charge_capacity"] if bat else None, "last_observed_capacity": bat[-1][1]["battery"]["full_charge_capacity"] if bat else None, "first_evidence": evidence(*bat[0]) if bat else None, "last_observed_evidence": evidence(*bat[-1]) if bat else None},
            "latest_failed_checks": [c for c in last["compliance_results"] if c["status"] == "fail"],
            "failed_check_observations": dict(Counter(c["check_id"] for r in rs for c in r["compliance_results"] if c["status"] == "fail")),
            "network_missing": sum("network" not in r for r in rs),
        })

    tenants = []
    for company in sorted({r["company_id"] for r in rows}):
        selected = [d for d in devices if d["company_id"] == company]
        tenants.append({
            "company_id": company, "devices": len(selected),
            "snapshots": sum(d["snapshots"] for d in selected),
            "employees": len({e for d in selected for e in d["employee_ids"]}),
            "platforms": dict(Counter(d["platform"] for d in selected)),
            "latest_low_disk_devices": [d["device_id"] for d in selected if d["disk"]["latest_free_percent"] < LOW_DISK_PERCENT],
            "persistent_high_ram_devices": [d["device_id"] for d in selected if d["ram"]["persistent"]],
            "latest_macos_older_than_15": sum(d["platform"] == "darwin" and int(d["os_version"].split(".")[0]) < 15 for d in selected),
            "latest_failed_checks": dict(Counter(c["check_id"] for d in selected for c in d["latest_failed_checks"])),
        })
    snapshot_keys = Counter((r["company_id"], r["device_id"], r["collected_at"]) for r in rows)
    check_counts = Counter((c["check_id"], c["severity"], c["status"]) for r in rows for c in r["compliance_results"])
    return {
        "source": {"filename": source.name, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()},
        "thresholds": {"status": "exploratory assumptions, not supplied policy", "low_disk_free_percent_strictly_below": LOW_DISK_PERCENT, "high_ram_used_percent_strictly_above": HIGH_RAM_PERCENT, "persistent_minimum_fraction_of_observations": PERSISTENT_FRACTION},
        "overview": {"snapshots": len(rows), "companies": len(tenants), "devices": len(devices), "employees": len({r["employee_id"] for r in rows}), "days": len(dates), "first_collected_at": min(r["collected_at"] for r in rows), "last_collected_at": max(r["collected_at"] for r in rows), "platform_devices": dict(Counter(d["platform"] for d in devices)), "model_devices": dict(Counter(d["model"] for d in devices))},
        "quality": {
            "duplicate_rows": len(rows) - len({json.dumps(r, sort_keys=True) for r in rows}),
            "duplicate_snapshot_keys": [{"key": key, "count": count} for key, count in snapshot_keys.items() if count > 1],
            "globally_sorted_by_timestamp": all(a["collected_at"] <= b["collected_at"] for a, b in zip(rows, rows[1:])),
            "devices_with_missing_days": [d["device_id"] for d in devices if d["missing_dates"]],
            "devices_with_multiple_snapshots_per_day": [d["device_id"] for d in devices if d["multiple_snapshot_dates"]],
            "cross_tenant_device_ids": {k: sorted(v) for k, v in by_device_tenant.items() if len(v) > 1},
            "cross_tenant_employee_ids": {k: sorted(v) for k, v in by_employee_tenant.items() if len(v) > 1},
            "basic_validation_violations": dict(violations),
            "battery_missing_snapshots": sum("battery" not in r for r in rows),
            "network_missing_snapshots": sum("network" not in r for r in rows),
            "battery_cycle_decrease_count": len(cycle_decreases),
            "battery_cycle_decrease_device_count": len({x["after"]["device_id"] for x in cycle_decreases}),
            "battery_cycle_decreases": cycle_decreases,
        },
        "schema": {field: {"snapshots_present": field_presence[field], "occurrence_types": dict(types)} for field, types in sorted(field_types.items())},
        "compliance_observations": [{"check_id": key[0], "severity": key[1], "status": key[2], "count": count} for key, count in sorted(check_counts.items())],
        "compliance_transitions": compliance_changes,
        "software_transitions": software_changes,
        "tenants": tenants,
        "devices": devices,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "data/raw/device-telemetry-dataset.ndjson")
    parser.add_argument("--output", type=Path, default=ROOT / "reports/dataset-profile.json")
    args = parser.parse_args()
    result = profile(args.input)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"overview": result["overview"], "quality": {k: v for k, v in result["quality"].items() if k != "battery_cycle_decreases"}, "report": str(args.output)}, indent=2))


if __name__ == "__main__":
    main()
