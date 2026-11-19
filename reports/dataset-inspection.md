# Fleet telemetry dataset inspection

Inspected on 19 November 2026. Source: the unmodified assessment file in `data/raw/device-telemetry-dataset.ndjson`. Reproduce all measurements with `python3 scripts/profile_dataset.py`; the machine-readable profile includes source lines and snapshot identifiers.

## Verified inventory

| Company ID | Devices | Employee IDs | Snapshots | macOS / Windows devices |
| --- | ---: | ---: | ---: | ---: |
| acme-001 | 10 | 5 | 300 | 7 / 3 |
| globex-002 | 8 | 4 | 240 | 5 / 3 |
| initech-003 | 7 | 5 | 210 | 5 / 2 |
| Total | 25 | 14 | 750 | 17 / 8 |

- Exactly one snapshot per device per UTC date, for 30 consecutive dates: 14 May through 12 June 2026.
- Earliest timestamp: `2026-05-14T08:40:00Z`; latest: `2026-06-12T11:00:00Z`.
- The brief estimates about 18 employees; the file contains 14 distinct employee IDs. Several employees have multiple devices. No employee names or contact addresses are supplied.
- Models: 10 MacBook Pro, 4 MacBook Air, 3 Mac mini, 4 ThinkPad X1 Carbon, and 4 Dell XPS 15.
- File order is not globally chronological. Always sort by `collected_at` within the selected company/device.
- Identity, employee assignment, OS fields, and agent version do not change within any device's history.

These are historical observations. A response about the latest fleet state must say "as of 12 June 2026" and use each device's own most recent snapshot. It must not imply that these devices are currently in the same state in November.

## Structure and useful fields

Each line is a nested JSON object, not an element of one outer JSON array.

| Field/group | What it supports | Important interpretation |
| --- | --- | --- |
| `company_id`, `device_id`, `employee_id`, `collected_at` | Tenant filtering, joins, ownership, time windows, citations | Use `(company_id, device_id, collected_at)` as snapshot identity; bind company access outside model-generated arguments. |
| `os` | Platform/version filters, hostname display | macOS versions include `13.6`, `14.5`, `15.3`, `15.4`; Windows versions include `10 22H2`, `11 22H2`, `11 23H2`. Parse by platform, not as one numeric/lexical version sequence. |
| `device_identity` | Hardware model, processor, serial number, memory label | Useful inventory; no purchasing, warranty, or component-compatibility catalog. |
| `memory` | RAM utilization from used/total byte counts | High utilization is observable; application slowness, actual memory pressure, and its cause are not directly measured. |
| `disk_volumes[]` | Available space, capacity, encryption | One volume in every supplied snapshot, but preserve the array in the schema and retain volume identity. |
| `battery` | Condition, cycle count, charge level, full-charge capacity | Optional. No design capacity, capacity unit, or replacement history is supplied. Charge percentage is not battery health percentage. |
| `installed_software[]` | Software presence, version, first observed additions | No organizational allowlist, denylist, or vulnerability feed is supplied. |
| `compliance_results[]` | Reported check status, severity, drift | Three checks: disk encryption (high), screen lock (medium), OS up to date (medium). No underlying policy definitions are supplied. |
| `network[]` | Interface metadata | Optional. Not needed for the core health/compliance questions. |

## Data quality

Passed checks:

- All 750 lines parse as JSON; timestamps parse and include timezone information.
- No duplicate records or duplicate composite snapshot keys.
- No missing device-days or multiple snapshots for one device-day.
- No explicit JSON nulls or mixed types at the observed field paths.
- No invalid memory/disk ranges; used plus free memory equals total memory, and RAM bytes equals total memory bytes in every row.
- No invalid battery percentage/cycle-count/capacity bounds under the basic nonnegative/positive checks.
- Device ID matches serial number, and encryption telemetry agrees with the encryption compliance result throughout.
- No device ID or employee ID is shared across companies in this file. This does not replace tenant enforcement or tests using adversarial fixtures.

Missingness and anomalies:

| Finding | Verified extent | Consequence |
| --- | --- | --- |
| Battery block omitted | 100 snapshots: all 90 Mac mini snapshots, plus 10 laptop snapshots across 4 devices | Model missing observations explicitly; do not interpret a missing battery block as healthy or zero capacity. Desktop omissions are consistent with the model type, but there is no explicit `battery_present: false` record. |
| Network block omitted | 16 snapshots | Optional schema; missing interface data must not break unrelated queries. |
| Battery cycle count decreases | 205 adjacent observed-pair decreases across all 22 devices with battery observations | Flag this anomaly and avoid treating cycle counts as a reliable monotonic wear trajectory. No replacement event explains the decreases in the supplied data. |

Example anomaly: `acme-macbook-2` reports 519 cycles on 21 May, then 515 on 22 May (raw lines 8 and 9). Preserve the raw evidence rather than silently correcting it.

`acme-macbook-4` is missing battery data in its latest snapshot. Its last observed battery reading must carry its own earlier timestamp if used; it must not be presented as a reading from the latest snapshot.

## Concrete findings

The following thresholds are exploratory assumptions, not policies supplied by Rayda:

- Low disk: available bytes / size bytes **strictly below 10%**.
- High RAM utilization: used bytes / total memory bytes **strictly above 85%**.
- Persistent high RAM: above that threshold in **at least 80%** of observations in the chosen window.

### Storage

Six devices have less than 10% free space in their latest snapshot. All latest observations below are from 12 June 2026; evidence lines identify the exact timestamps.

| Company | Hostname | Free space | Low-space observations / 30 | Latest raw line |
| --- | --- | ---: | ---: | ---: |
| acme-001 | acme-thinkpad-8 | 2.59% | 15 | 90 |
| acme-001 | acme-macbook-4 | 2.00% | 30 | 240 |
| globex-002 | globex-dell-7 | 2.39% | 17 | 420 |
| globex-002 | globex-thinkpad-8 | 2.00% | 23 | 480 |
| initech-003 | initech-macbook-6 | 2.00% | 23 | 600 |
| initech-003 | initech-mac-4 | 2.00% | 30 | 660 |

For example, `acme-thinkpad-8` declines from 17.27% to 2.59% available space across the window (raw lines 61 and 90). This supports a storage investigation/remediation proposal, but does not identify which files can safely be deleted.

### Memory utilization

| Hostname | Mean used RAM | Observations above 85% / 30 | Meets exploratory persistence rule? |
| --- | ---: | ---: | --- |
| acme-dell-9 | 94.19% | 30 | Yes |
| initech-mac-3 | 91.83% | 30 | Yes |
| globex-thinkpad-8 | 87.56% | 23 | No: 76.7% of observations |
| globex-thinkpad-6 | 84.98% | 13 | No: 43.3% of observations |

Evidence windows: `acme-dell-9`, raw lines 181-210; `initech-mac-3`, lines 601-630. The distinction between mean utilization and frequency above a threshold is material. A daily snapshot does not establish that a device was constrained continuously throughout the day.

### Batteries

Three devices, all at Acme, end with the reported condition `Service Recommended` and declining recorded full-charge capacity:

| Hostname | First to last capacity (units unspecified) | Service Recommended observations / 30 | Endpoint raw lines |
| --- | ---: | ---: | --- |
| acme-macbook-1 | 4348 to 4166 | 14 | 31, 60 |
| acme-thinkpad-8 | 4043 to 3872 | 21 | 61, 90 |
| acme-dell-9 | 3406 to 3262 | 20 | 181, 210 |

This supports battery-service assessment proposals. It does not establish a precise battery health percentage, remaining useful life, or that the entire device must be replaced.

### Compliance and OS

- **Zero high-severity failures** in all 750 snapshots. Disk encryption is the only high-severity check, and it always passes. The brief's example asking for high-severity failures therefore has an empty result on this dataset.
- **10 devices** transition from passing to failing screen lock: 5 at Acme, 2 at Globex, 3 at Initech. Each remains failed through its latest observation. Example: `acme-macbook-2` changes on 22 May (raw lines 8 and 9).
- **19 devices** fail the provided `os_up_to_date` check throughout: 9 at Acme, 4 at Globex, 6 at Initech. Report this as the dataset's check result; its exact policy is not provided.
- **10 macOS devices** have a major version below 15: 4 at Acme, 2 at Globex, 4 at Initech. Windows devices are excluded from this comparison. OS versions are unchanged throughout the dataset.

### Software inventory changes

Six devices gain an additional observed application during the window: uTorrent on four devices, CleanMyMac X on one, and TeamViewer on one. The profile records the first observed addition and preceding snapshot. These are inventory changes; without an explicit policy they do not establish unauthorized software, compromise, or a compliance violation.

## Implications for the copilot

1. Use deterministic structured queries and calculations for facts, counts, thresholds, and trends. The agent can select tools and explain their results; it should not invent computed values.
2. Make every evidence reference include company, device, timestamp, field/value, and source identity. The profile already records snapshot keys and raw line numbers; a future ingestion layer can attach the file hash and computed-query parameters.
3. Distinguish latest state from historical observation counts. A device observed 30 times is still one device. Always state the as-of time and window used.
4. Treat thresholds as named, configurable policy assumptions. Do not silently invent the meaning of "low", "persistent", "old", or "end-of-life".
5. Enforce company scope from trusted application context on reads, proposals, approval, execution, and audit retrieval. The all-company totals in this developer inspection are not an authorization model for the tenant-facing agent.
6. A justified remediation ticket is feasible from observed failures. Exact upgrade orders additionally need hardware compatibility and purchasing policy; employee delivery needs a contact/integration mapping. The telemetry alone does not provide these. Local action adapters should clearly record simulated effects and require approval.
7. Build evaluation cases for the verified sample answers, empty high-severity results, version semantics, observation-vs-device counting, optional fields, stale/latest readings, threshold boundaries, cycle anomalies, unsupported software claims, tenant leakage, and approval bypass. Add clearly labeled synthetic fixtures for cases absent from the supplied data; preserve the raw dataset.

The dataset supports the assessment's grounded Q&A, historical insights, and evidence-backed action proposals. The important limitations are missing policy/compatibility information, anomalous battery cycles, optional telemetry, and historical rather than live observations.
