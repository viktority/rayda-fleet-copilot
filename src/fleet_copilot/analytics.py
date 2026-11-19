"""Deterministic facts. Text, arithmetic and citations originate here, not in the LLM."""

from collections import defaultdict
from datetime import date
from statistics import mean
from typing import Literal

from pydantic import Field, model_validator

from .domain import Evidence, Finding, FleetError, Principal, StrictModel
from .store import Store, digest


class Query(StrictModel):
    kind: Literal[
        "inventory", "low_disk", "high_ram", "compliance", "os_older", "battery_service", "software"
    ]
    device_id: str | None = None
    as_of: date | None = None
    threshold: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    severity: Literal["low", "medium", "high", "critical"] | None = None
    check_id: Literal["disk_encryption", "screen_lock", "os_up_to_date"] | None = None
    platform: Literal["darwin", "win32"] | None = None
    device_type: Literal["laptop", "desktop"] | None = None
    version_major: int | None = Field(default=None, ge=1, le=100)
    software_name: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def valid_filters(self):
        if self.kind == "os_older" and (self.platform != "darwin" or self.version_major is None):
            raise ValueError("os_older requires platform darwin and an explicit macOS major version")
        if self.kind == "software" and not self.software_name:
            raise ValueError("software requires an exact software name")
        if self.threshold is not None and self.kind not in ("low_disk", "high_ram"):
            raise ValueError("threshold is only valid for low_disk/high_ram")
        if (self.check_id or self.severity) and self.kind != "compliance":
            raise ValueError("Check/severity filters require compliance")
        if self.version_major is not None and self.kind != "os_older":
            raise ValueError("version_major requires os_older")
        if self.software_name is not None and self.kind != "software":
            raise ValueError("software_name requires software")
        return self


class Trend(StrictModel):
    kind: Literal["disk", "ram", "battery", "compliance", "software"]
    device_id: str | None = None
    start: date | None = None
    end: date | None = None
    threshold: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    fraction: float = Field(default=0.8, gt=0, le=1, allow_inf_nan=False)
    min_observations: int = Field(default=7, ge=2, le=365)

    @model_validator(mode="after")
    def valid_window(self):
        if self.start and self.end and self.start > self.end:
            raise ValueError("Start must not be after end")
        if self.threshold is not None and self.kind not in ("disk", "ram"):
            raise ValueError("Threshold is only valid for disk or RAM trends")
        return self


def reference(row: dict, fields: list[str]) -> Evidence:
    return Evidence(
        source_sha256=row["_sha"],
        source_line=row["_line"],
        company_id=row["company_id"],
        device_id=row["device_id"],
        collected_at=row["collected_at"],
        fields=fields,
    )


def finding(principal, kind, text, values, rows, fields, method, device_id=None):
    body = dict(
        company_id=principal.company_id,
        device_id=device_id,
        kind=kind,
        text=text,
        values=values,
        evidence=[reference(r, fields).model_dump() for r in rows],
        method=method,
    )
    return Finding(id="F-" + digest(body)[:16], **body)


class Analytics:
    def __init__(self, store: Store):
        self.store = store

    def query(self, principal: Principal, q: Query) -> list[Finding]:
        rows = self.store.rows(principal, q.device_id)
        if q.as_of:
            rows = [r for r in rows if date.fromisoformat(r["collected_at"][:10]) <= q.as_of]
        if not rows:
            raise FleetError("no_observations", "No observations on or before the requested date")
        latest = {}
        for r in rows:
            latest[r["device_id"]] = r
        model_types = {
            "MacBook Pro": "laptop",
            "MacBook Air": "laptop",
            "Mac mini": "desktop",
            "ThinkPad X1 Carbon": "laptop",
            "Dell XPS 15": "laptop",
        }
        scoped = [
            r
            for r in latest.values()
            if (q.platform is None or r["os"]["platform"] == q.platform)
            and (
                q.device_type is None or model_types.get(r["device_identity"]["model_name"]) == q.device_type
            )
        ]
        results = []
        fields = ["device_id", "collected_at"]
        label = q.kind
        threshold = q.threshold if q.threshold is not None else (10 if q.kind == "low_disk" else 85)
        for r in scoped:
            host, device, ts = r["os"]["hostname"], r["device_id"], r["collected_at"]
            values, text, method = {}, "", "latest snapshot per device"
            if q.kind == "inventory":
                fields = ["os", "device_identity.model_name", "employee_id"]
                values = {
                    "hostname": host,
                    "model": r["device_identity"]["model_name"],
                    "os": r["os"]["product_name"],
                    "version": r["os"]["product_version"],
                    "employee_id": r["employee_id"],
                }
                text = f"{host} ({device}): {values['model']}, {values['os']} {values['version']}, employee {values['employee_id']} as of {ts}."
            elif q.kind == "low_disk":
                fields = ["disk_volumes"]
                label = f"disk space strictly below {threshold:g}% available"
                volumes = [
                    {
                        "mount_point": v["mount_point"],
                        "free_percent": 100 * v["available_bytes"] / v["size_bytes"],
                        "available_bytes": v["available_bytes"],
                        "size_bytes": v["size_bytes"],
                    }
                    for v in r["disk_volumes"]
                    if 100 * v["available_bytes"] / v["size_bytes"] < threshold
                ]
                if volumes:
                    values = {"volumes": volumes, "threshold_percent": threshold}
                    detail = ", ".join(f"{v['mount_point']}: {v['free_percent']:.2f}% free" for v in volumes)
                    text = f"{host} ({device}) has low disk space ({detail}) as of {ts}; threshold is <{threshold:g}% free."
                    method = "100 * available_bytes / size_bytes, per volume; any volume below threshold"
            elif q.kind == "high_ram":
                fields = ["memory"]
                label = f"RAM usage strictly above {threshold:g}%"
                pct = 100 * r["memory"]["used_memory_bytes"] / r["memory"]["total_memory_bytes"]
                if pct > threshold:
                    values = {"used_percent": pct, "threshold_percent": threshold}
                    text = f"{host} ({device}) reports {pct:.2f}% RAM used as of {ts}, above {threshold:g}%. This measures utilization, not its cause."
                    method = "100 * used_memory_bytes / total_memory_bytes"
            elif q.kind == "compliance":
                fields = ["compliance_results"]
                label = (
                    f"failed compliance checks (severity={q.severity or 'any'}, check={q.check_id or 'any'})"
                )
                checks = [
                    c
                    for c in r["compliance_results"]
                    if c["status"] == "fail"
                    and (not q.severity or c["severity"] == q.severity)
                    and (not q.check_id or c["check_id"] == q.check_id)
                ]
                if checks:
                    values = {"failed_checks": checks}
                    text = (
                        f"{host} ({device}) fails "
                        + ", ".join(f"{c['check_id']} ({c['severity']})" for c in checks)
                        + f" as of {ts}, according to the supplied checks."
                    )
            elif q.kind == "os_older":
                fields = ["os.platform", "os.product_version"]
                label = f"macOS major version below {q.version_major}"
                try:
                    major = int(r["os"]["product_version"].split(".")[0])
                except ValueError as exc:
                    raise FleetError("invalid_version", "Unrecognized macOS version in telemetry") from exc
                if major < q.version_major:
                    values = {"version": r["os"]["product_version"], "major_limit": q.version_major}
                    text = f"{host} ({device}) runs macOS {values['version']}, below major version {q.version_major}, as of {ts}."
                    method = "macOS-only integer major-version comparison"
            elif q.kind == "battery_service":
                fields = ["battery"]
                label = "reported battery service recommendation"
                b = r.get("battery")
                if b and b["condition"] == "Service Recommended":
                    values = {"condition": b["condition"], "full_charge_capacity": b["full_charge_capacity"]}
                    text = f"{host} ({device}) reports battery condition Service Recommended as of {ts}. Battery health percentage and remaining life cannot be calculated from the supplied fields."
            elif q.kind == "software":
                fields = ["installed_software"]
                label = f"installed software named {q.software_name}"
                software = [
                    s for s in r["installed_software"] if s["name"].casefold() == q.software_name.casefold()
                ]
                if software:
                    values = {"software": software}
                    text = (
                        f"{host} ({device}) has "
                        + ", ".join(f"{s['name']} {s['version']}" for s in software)
                        + f" as of {ts}. Presence alone does not establish a policy violation."
                    )
            if text:
                results.append(finding(principal, q.kind, text, values, [r], fields, method, device))
        # Include the scanned population even for zero matches: negative claims need evidence.
        coverage = scoped or list(latest.values())
        as_of = max(r["collected_at"] for r in coverage)
        missing = sum("battery" not in r for r in scoped) if q.kind == "battery_service" else 0
        summary = f"{len(results)} of {len(scoped)} accessible devices match {label}, using each device's latest observation on or before {as_of}."
        if q.kind == "battery_service":
            summary += f" {missing} devices have no battery reading in their latest snapshot; missing is not healthy."
        parameters = q.model_dump(mode="json")
        return [
            finding(
                principal,
                "query_summary",
                summary,
                {
                    "count": len(results),
                    "scanned_devices": len(scoped),
                    "filters": parameters,
                    "as_of": as_of,
                    "missing_battery": missing,
                },
                coverage,
                fields + ["os.platform", "device_identity.model_name"],
                "Filter latest snapshot per tenant/device; count unique matching devices",
            ),
            *results,
        ]

    def trends(self, principal: Principal, q: Trend) -> list[Finding]:
        rows = self.store.rows(principal, q.device_id)
        rows = [
            r
            for r in rows
            if (not q.start or r["collected_at"][:10] >= q.start.isoformat())
            and (not q.end or r["collected_at"][:10] <= q.end.isoformat())
        ]
        if not rows:
            raise FleetError("no_observations", "No observations in the requested window")
        groups = defaultdict(list)
        for r in rows:
            groups[r["device_id"]].append(r)
        window_start = q.start or min(date.fromisoformat(r["collected_at"][:10]) for r in rows)
        window_end = q.end or max(date.fromisoformat(r["collected_at"][:10]) for r in rows)
        expected = (window_end - window_start).days + 1
        results = []
        for device, rs in groups.items():
            host = rs[-1]["os"]["hostname"]
            # Consistency means distinct observed days; extra same-day snapshots never inflate it.
            daily = {r["collected_at"][:10]: r for r in rs}
            days = list(daily.values())
            common = {
                "observed_days": len(days),
                "span_days": expected,
                "window_start": window_start.isoformat(),
                "window_end": window_end.isoformat(),
                "start": rs[0]["collected_at"],
                "end": rs[-1]["collected_at"],
            }
            if q.kind in ("disk", "ram"):
                threshold = q.threshold if q.threshold is not None else (10 if q.kind == "disk" else 85)
                if q.kind == "ram":
                    observations = {
                        "RAM": [
                            100 * r["memory"]["used_memory_bytes"] / r["memory"]["total_memory_bytes"]
                            for r in days
                        ]
                    }
                    fields, relation = ["memory"], ">"
                else:
                    observations = {
                        mount: [
                            100 * v["available_bytes"] / v["size_bytes"]
                            for r in days
                            for v in r["disk_volumes"]
                            if v["mount_point"] == mount
                        ]
                        for mount in sorted({v["mount_point"] for r in days for v in r["disk_volumes"]})
                    }
                    fields, relation = ["disk_volumes"], "<"
                for component, series in observations.items():
                    hits = sum(v < threshold if q.kind == "disk" else v > threshold for v in series)
                    # Count missing days against eligibility; explicitly display coverage.
                    persistent = len(series) >= q.min_observations and hits / expected >= q.fraction
                    if persistent:
                        values = {
                            **common,
                            "component": component,
                            "mean_percent": mean(series),
                            "first_percent": series[0],
                            "last_percent": series[-1],
                            "hits": hits,
                            "threshold": threshold,
                            "fraction": q.fraction,
                            "min_observations": q.min_observations,
                        }
                        text = f"{host} ({device}): {component} meets persistent {q.kind} utilization criteria: {hits}/{expected} days {relation}{threshold:g}% ({len(series)} observed); mean {mean(series):.2f}%, first {series[0]:.2f}%, last {series[-1]:.2f}%, from {common['start']} to {common['end']}. Policy assumption: at least {q.fraction:.0%} of days and {q.min_observations} observations."
                        results.append(
                            finding(
                                principal,
                                f"persistent_{q.kind}",
                                text,
                                values,
                                days,
                                fields,
                                "Last observation per UTC day; missing days count against persistence; compare explicit thresholds",
                                device,
                            )
                        )
            elif q.kind == "battery":
                bs = [r for r in rs if "battery" in r]
                if len(bs) >= 2:
                    first, last = bs[0]["battery"], bs[-1]["battery"]
                    decreases = sum(
                        b["battery"]["cycle_count"] < a["battery"]["cycle_count"] for a, b in zip(bs, bs[1:])
                    )
                    decline = first["full_charge_capacity"] - last["full_charge_capacity"]
                    values = {
                        **common,
                        "first_capacity": first["full_charge_capacity"],
                        "last_capacity": last["full_charge_capacity"],
                        "capacity_drop": decline,
                        "cycle_decreases": decreases,
                        "missing_observations": len(rs) - len(bs),
                        "last_battery_at": bs[-1]["collected_at"],
                        "latest_snapshot_has_battery": "battery" in rs[-1],
                    }
                    text = f"{host} ({device}): recorded full-charge capacity {first['full_charge_capacity']} to {last['full_charge_capacity']} (units unspecified), from {bs[0]['collected_at']} to {bs[-1]['collected_at']}. {decreases} cycle-count decreases and {len(rs) - len(bs)} missing battery observations. Design capacity is absent; no health percentage or remaining-life estimate is supported."
                    results.append(
                        finding(
                            principal,
                            "battery_trend",
                            text,
                            values,
                            rs,
                            ["battery"],
                            "Compare first/last observed capacity; count adjacent observed cycle decreases without correction",
                            device,
                        )
                    )
                else:
                    results.append(
                        finding(
                            principal,
                            "battery_unknown",
                            f"{host} ({device}): insufficient battery observations ({len(bs)}) in this window for a trend; missing is not healthy.",
                            {**common, "battery_observations": len(bs)},
                            rs,
                            ["battery"],
                            "Count observed battery blocks",
                            device,
                        )
                    )
            elif q.kind in ("compliance", "software"):
                for before, after in zip(rs, rs[1:]):
                    if q.kind == "compliance":
                        old = {c["check_id"]: c["status"] for c in before["compliance_results"]}
                        for c in after["compliance_results"]:
                            if c["check_id"] in old and c["status"] != old[c["check_id"]]:
                                values = {
                                    "check_id": c["check_id"],
                                    "before": old[c["check_id"]],
                                    "after": c["status"],
                                }
                                text = f"{host} ({device}): {c['check_id']} changed from {values['before']} at {before['collected_at']} to {values['after']} at {after['collected_at']}."
                                results.append(
                                    finding(
                                        principal,
                                        "compliance_drift",
                                        text,
                                        values,
                                        [before, after],
                                        ["compliance_results"],
                                        "Compare adjacent observations for the same check",
                                        device,
                                    )
                                )
                    else:
                        old = {(s["name"], s["version"]) for s in before["installed_software"]}
                        new = {(s["name"], s["version"]) for s in after["installed_software"]}
                        if new != old:
                            values = {"added": sorted(new - old), "removed": sorted(old - new)}
                            text = f"{host} ({device}): software inventory changed between {before['collected_at']} and {after['collected_at']}: added {values['added']}, removed {values['removed']}. No software approval policy is supplied."
                            results.append(
                                finding(
                                    principal,
                                    "software_drift",
                                    text,
                                    values,
                                    [before, after],
                                    ["installed_software"],
                                    "Set difference of observed name/version pairs; not proof of installation time or policy violation",
                                    device,
                                )
                            )
        summary = f"{len(results)} {q.kind} findings across {len(groups)} devices, observed from {min(r['collected_at'] for r in rows)} to {max(r['collected_at'] for r in rows)}."
        return [
            finding(
                principal,
                "trend_summary",
                summary,
                {
                    "finding_count": len(results),
                    "devices": len(groups),
                    "parameters": q.model_dump(mode="json"),
                },
                rows,
                [
                    "collected_at",
                    {
                        "ram": "memory",
                        "disk": "disk_volumes",
                        "battery": "battery",
                        "compliance": "compliance_results",
                        "software": "installed_software",
                    }[q.kind],
                ],
                "Tenant-scoped window; see individual findings for calculations",
            ),
            *results,
        ]

    def actionable(self, principal, device_id):
        """Recompute trusted canonical findings; callers cannot supply arbitrary evidence."""
        facts = []
        for kind in ("low_disk", "compliance", "battery_service"):
            facts.extend(self.query(principal, Query(kind=kind, device_id=device_id)))
        facts.extend(self.trends(principal, Trend(kind="ram", device_id=device_id)))
        return {f.id: f for f in facts if f.device_id is not None}
