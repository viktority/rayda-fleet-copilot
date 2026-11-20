# Fleet Copilot technical guide

This is the long-form companion to the [README](../README.md). It covers how a request moves through the system, every interface, the full policy, the evaluation corpus, and the reasoning behind each design decision.

- [How a request flows](#how-a-request-flows)
- [Code map](#code-map)
- [Setup details](#setup-details)
- [Interfaces](#interfaces): [browser](#browser-interface), [CLI](#cli), [action workflow](#action-workflow), [HTTP API](#http-api)
- [Agent loop](#agent-loop)
- [Tools and action policy](#tools-and-action-policy)
- [Grounding and analytics policy](#grounding-and-analytics-policy)
- [Guardrails](#guardrails)
- [Evaluation](#evaluation)
- [Design decisions and trade-offs](#design-decisions-and-trade-offs)
- [Troubleshooting](#troubleshooting)

## How a request flows

```mermaid
flowchart TD
    U[Authenticated request / trusted local principal] --> S[Bind immutable tenant and source hash]
    S --> M[LangGraph model node]
    M --> P[Record operational plan]
    P --> M
    M --> T[Validate and dispatch typed tool]
    T --> Q[Scoped telemetry queries and deterministic analytics]
    Q --> E[Canonical findings and evidence ledger]
    E --> M
    T --> A[Revalidate actionable evidence and persist pending proposal]
    A --> M
    M --> F[Finish with verified finding IDs]
    F --> R[Render facts and citations; persist run]
    A --> H[Separate administrator decision]
    H --> V[Verify role, tenant, digest, expiry and source version]
    V --> X[Atomic simulated effect and audit]
```

Take the question "Which devices at Acme are low on disk space?" from an Acme principal:

1. **Bind context.** The request's company, actor and role come from the bearer token (HTTP) or the local launcher flags (CLI/UI). They are fixed before the model runs. The current dataset SHA-256 is recorded too.
2. **Scope check.** A cheap prompt check refuses questions that name a different known company, then logs `scope_refused`. This is only an early exit; authorization really happens in the scoped database queries.
3. **Plan.** The first model turn is forced to call `make_plan`. It records a short list of intended operations, such as "query low_disk, then finish".
4. **Tool calls.** The model calls `query_fleet` with `{"kind":"low_disk"}`. The tool reads only Acme rows, applies the documented threshold and returns findings. Each finding has a deterministic ID, server-written text, values, its method and source evidence. The full evidence goes into a server-side ledger; the model receives a compact copy without evidence.
5. **Adapt.** If a tool returns an error (a bad schema, a missing plan, an unknown finding ID), the error goes back to the model as a structured observation, so the model can correct itself within the step budget.
6. **Finish.** The model calls `finish` with the finding IDs it wants to report and a status. The server rejects IDs that never appeared in the ledger, and rejects an "answered" status that cites no evidence.
7. **Render.** The final answer is built from the canonical finding text, not from model-written prose. Every sentence ends with its finding ID. The run, including the plan, tool trace, usage, model name and source hash, is saved for tenant-scoped retrieval.

When proposals are enabled, step 4 can include `propose_action`. That call checks the cited evidence against the action policy again and stores a pending proposal with an immutable digest. Approval happens later, through a separate admin-only path the model cannot reach.

## Code map

| Module | Responsibility |
| --- | --- |
| [`domain.py`](../src/fleet_copilot/domain.py) | Strict Pydantic models, principals, findings, evidence and error types |
| [`store.py`](../src/fleet_copilot/store.py) | SQLite persistence, transactional ingestion, tenant-scoped row access, runs and append-only audit |
| [`analytics.py`](../src/fleet_copilot/analytics.py) | `Query`/`Trend` schemas and the deterministic calculations that produce findings |
| [`actions.py`](../src/fleet_copilot/actions.py) | Proposal policy, digests, expiry and the admin decision state machine |
| [`agent.py`](../src/fleet_copilot/agent.py) | Tool definitions, the LangGraph loop, citation checks and answer rendering |
| [`model.py`](../src/fleet_copilot/model.py) | OpenAI Responses API boundary; offline evaluation swaps in a scripted model |
| [`evaluation.py`](../src/fleet_copilot/evaluation.py) | 21-case corpus, offline routing oracle and live-model runner |
| [`api.py`](../src/fleet_copilot/api.py) / [`ui.py`](../src/fleet_copilot/ui.py) / [`cli.py`](../src/fleet_copilot/cli.py) | FastAPI service, local browser launcher and command-line interface |
| [`web/`](../src/fleet_copilot/web/) | Plain HTML/CSS/JavaScript frontend with no build step, shipped in the Python package |

## Setup details

Requirements: Git, Python 3.11–3.14 (CI uses 3.12) and [uv](https://docs.astral.sh/uv/getting-started/installation/).

```sh
uv sync --frozen
uv run fleet init
```

`fleet init` ingests [`data/raw/device-telemetry-dataset.ndjson`](../data/raw/). Ingestion validates all 750 snapshots, runs in a single transaction and is safe to repeat. The source file is never modified; a validated, normalized copy is stored in `runtime/fleet.sqlite3`. The observation window is **14 May to 12 June 2026**. Schema, checksums and data limitations are in the [dataset inspection](../reports/dataset-inspection.md), which [`scripts/profile_dataset.py`](../scripts/profile_dataset.py) reproduces.

Natural-language questions need an OpenAI key:

```sh
cp -n .env.example .env.local
chmod 600 .env.local
```

Set `OPENAI_API_KEY` in `.env.local` or in your environment. The default model is `gpt-6.2-sol`. Set `OPENAI_MODEL` to any tool-calling Responses API model your project can use; changing the model can change live evaluation results. The key is only used on the server and never reaches the frontend.

## Interfaces

### Browser interface

```sh
uv run fleet ui                                              # Acme admin on :8000
uv run fleet ui --company globex-002 --role viewer --port 8001
```

The launcher starts the API and frontend together and opens a browser tab that is already connected. It acts as one trusted local principal; the browser has no way to switch company or role. On each start it:

- generates a temporary bearer credential;
- writes the connection URL to `runtime/ui-access-PORT.json` with owner-only permissions;
- opens that URL. The page removes the credential from the URL fragment straight away and keeps it in that tab's session storage.

Restarting the launcher creates a new credential, and a normal shutdown deletes the access file. **Disconnect** clears the stored credential and everything shown on the page. `--no-open` starts the server without opening a browser. `fleet serve` serves the same interface at `/` for configured tokens.

The interface handles loading, empty results, errors, expired proposals and narrow mobile screens. Evidence and other data-derived strings are always rendered as text, never as HTML. Screenshots: [desktop](screenshots/desktop.jpg), [mobile](screenshots/mobile.jpg), [mobile action review](screenshots/mobile-action-review.jpg).

### CLI

```sh
uv run fleet ask --company acme-001 'Which devices at Acme are low on disk space?'
uv run fleet ask --company acme-001 --json \
  'Which devices consistently use more than 85% RAM on at least 80% of days?'
uv run fleet ask --company acme-001 'Show laptops failing high-severity compliance checks.'
```

The last question should get a "none found" answer, backed by evidence for the devices scanned. The agent must not invent a problem just because the question implies one exists.

Deterministic queries with no model and no key:

```sh
uv run fleet query  --company acme-001 '{"kind":"low_disk"}'
uv run fleet trends --company acme-001 '{"kind":"ram"}'
```

The CLI is a **trusted local operator interface**. Its `--company` and `--role` flags choose a principal for local use and are not authentication for remote users.

### Action workflow

```sh
uv run fleet ask --company acme-001 --propose --json \
  'Check acme-macbook-4 for low disk space and propose one remediation ticket if justified.'
uv run fleet proposals --company acme-001
```

Review the action, target, cited evidence, simulation scope, expiry and digest. Then decide, using the ID and digest from that exact payload:

```sh
uv run fleet decide --company acme-001 --actor reviewer --role admin \
  PROPOSAL_ID --digest REVIEWED_DIGEST --approve --confirm      # or --reject --confirm
uv run fleet audit --company acme-001 --role admin
```

- The model never gets the `decide` operation. `--propose` allows proposals for one request; it does not authorize execution.
- Operators can propose; only administrators can approve or reject.
- Execution is blocked by a changed digest, an expired proposal, a changed dataset, the wrong tenant, the wrong role, or a conflicting earlier decision. Proposals expire after 24 hours.
- Proposal state and audit history are stored in SQLite and survive restarts. Repeating the same decision returns the same recorded effect. Concurrent approvals are serialized by a transaction and a unique effect key.
- Identical calls (same run, action, device and evidence) are deduplicated; separate runs can create separate proposals.

### HTTP API

`FLEET_API_TOKENS` maps independent random bearer tokens (at least 32 characters) to trusted principals:

```json
{
  "<random-operator-token>": {"company_id":"acme-001","actor_id":"it-operator","role":"operator"},
  "<different-random-admin-token>": {"company_id":"acme-001","actor_id":"it-admin","role":"admin"}
}
```

These are placeholders. The API refuses to start with no tokens or with short tokens. Run `uv run fleet serve --port 8000`; interactive docs are at `/docs`.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/health` | Readiness and simulation mode; no tenant data |
| GET | `/v1/context` | Company, role, device count and observation date for the bearer credential |
| POST | `/v1/ask` | `{"question":"...","allow_proposals":false}` |
| GET | `/v1/runs/{id}` | Saved answer, evidence and trace (tenant-scoped) |
| GET | `/v1/proposals` | Review queue (tenant-scoped) |
| GET | `/v1/proposals/{id}` | Immutable proposed payload and review digest |
| POST | `/v1/proposals/{id}/decision` | Admin only: `decision`, `expected_digest`, `confirmed:true` |
| GET | `/v1/audit` | Admin view of the company's audit events |

Requests can't override company or role. Unknown IDs and IDs from another tenant return the same not-found response. There is no CORS and no unauthenticated development bypass.

## Agent loop

The graph in [`agent.py`](../src/fleet_copilot/agent.py) has two nodes, `model` and `tools`, joined by conditional edges that loop until `finish` succeeds or the **12-step budget** runs out. If the budget runs out, the result is an `incomplete` status, never a partial unsupported answer.

- **One tool call per turn.** `parallel_tool_calls=False` keeps the trace linear and makes each step's input depend on the previous result.
- **Forced first plan.** The first turn uses `tool_choice` set to `make_plan`; tool calls before a plan are rejected. The plan is a short visible list of intended operations, not a private chain-of-thought.
- **Strict schemas.** Tool parameters come from Pydantic models with `additionalProperties: false` and every field required. Arguments are validated again on the server.
- **Errors are observations.** Schema failures, unknown tools, a missing plan or an unknown finding ID go back to the model as `{"error", "message"}` so it can recover.
- **Dataset pinning.** If the source hash changes mid-run, the run stops with `incomplete`.
- **Bounded provider use.** Each response has a 2,500-token output cap, a 60-second timeout and one retry. Low reasoning effort is used. Calls use `store=False` (this is not a claim of zero provider retention). Provider error text is reduced to a safe code before it reaches users or the audit log.

Approval uses an application-owned durable state machine, not a LangGraph interrupt that would keep a model process suspended. That makes the exact reviewed payload and the role boundary explicit, survives restarts, and allows atomic effect recording. The reasoning graph only lives for one request: there is no multi-turn memory, and a failed model request is not resumed from a checkpoint. Pending proposals stay accessible either way.

## Tools and action policy

| Tool | Behavior |
| --- | --- |
| `make_plan` | Records up to six operational steps, once, at the start |
| `query_fleet` | Latest-state queries: inventory, low disk, high RAM, failed compliance, older macOS majors, battery service condition, exact software presence. Optional device, date, platform and laptop/desktop filters |
| `analyze_trends` | Historical analysis: persistent disk/RAM pressure, battery end-of-life risk, battery capacity changes and anomalies, compliance and software drift |
| `propose_action` | Validates cited findings and creates a pending simulated action |
| `finish` | Selects existing finding IDs and an answer/refusal status; the server renders the final text |

The model has no SQL, shell, network or execution tool.

The brief's example actions map to the implementation as follows:

| Brief example | Implementation | Required evidence |
| --- | --- | --- |
| `open_remediation_ticket` | Supported | Current low disk, failed compliance check, battery service condition, or persistent RAM use (default policy) |
| `notify_employee` | Supported | Same as above. The recipient comes from device ownership in the telemetry, and the content is the cited finding text; free-form addresses or model-written messages are not accepted |
| `flag_device_for_replacement` | Supported as a **review flag only** | Both persistent RAM use and a battery service condition. This is a documented demo policy, not a Rayda policy or a purchasing decision |
| `create_upgrade_order` | Refused | The data has no hardware compatibility or purchasing catalog, so no component specification could be justified by evidence |

## Grounding and analytics policy

Every finding carries:

- a deterministic ID;
- server-written text and computed values;
- the calculation method;
- evidence references: source SHA-256, line number, company ID, device ID, timestamp and the field paths used.

Final answers are built only from these findings, so the model can't introduce numbers or citation text. Which findings are relevant still depends on the model's routing; the live evaluation measures that separately.

- Empty results still include evidence describing the devices that were scanned.
- Counts use each device's latest snapshot, not the number of observations. Historical queries use the requested as-of date.
- Replacing the source dataset cancels pending approvals, even approvals for another tenant. This is deliberately conservative.

Default policy:

- **Low disk:** strictly less than 10% available on a volume.
- **High RAM:** strictly more than 85% used.
- **Persistent:** the threshold is met on at least 80% of days in the window, with at least seven observed days. Without explicit dates, the window is the selected devices' observed span. Several samples on one day count once, missing days count against the device, and disk is tracked per mount point.
- **Battery end-of-life risk:** a device is flagged when at least 2 of 3 signals hold: the latest battery condition is Service Recommended; recorded full-charge capacity fell by at least 2% from the first to the last day in the window; the latest cycle count is at least 1,000, a common manufacturer rating that isn't model-specific. Requiring two signals stops one noisy field from flagging a device. In the supplied data, acme-macbook-1, acme-thinkpad-8 and acme-dell-9 qualify (all lost 4.2% capacity and changed to Service Recommended); no other device has any of the signals. Each finding cites the first observation, the first Service Recommended observation and the last observation.
- **Battery health and remaining life:** not reported, because the data has no design capacity and no valid model for either. End-of-life risk prioritizes a service review; it never predicts a failure date. Drops in cycle count are reported, not corrected.
- **Software:** an installed package is not treated as proof of unauthorized use, malware or a policy breach.
- **Actions:** eligibility always uses the default policy and the latest evidence. Exploring custom thresholds in an analytical query can't weaken the action guards. Custom thresholds appear in the finding metadata.

## Guardrails

| Boundary | Enforcement | Verification |
| --- | --- | --- |
| Tenant isolation | Immutable principal bound outside the model; every database and evidence lookup is filtered by company | Cross-tenant API, tool, run and proposal tests, including colliding device IDs |
| Evidence | Only canonical finding IDs can appear in final answers or justify proposals | Fabricated citations, empty populations, source-line resolution and numerical assertions |
| Approval | Separate admin endpoint with explicit confirmation, digest, expiry and source-version checks | Rejection, unauthorized role, stale payload, expiry and data-change cases |
| Effect safety | Simulation-only adapters; no execution tool exposed to the model | No effect before approval, restart durability, concurrent idempotency |
| Untrusted content | Typed schemas; telemetry strings and prompts can't change principal, policy or allowed operations | Invalid tools, schema failures and adversarial requests |
| Traceability | Plans, tool calls, source hashes, proposals, decisions and audit events are persisted; audit is append-only | Saved run/audit retrieval and audit mutation tests |

## Evaluation

```sh
uv run pytest
uv run ruff check src tests && uv run ruff format --check src tests
uv run fleet eval --output runtime/evaluation-offline.json
uv run fleet eval --live --output runtime/evaluation-live.json
```

New runs write to the ignored `runtime/` folder, so the committed [offline](../reports/evaluation-offline.json) and [live](../reports/evaluation-live.json) reports stay unchanged. GitHub Actions runs lint, formatting, tests and the offline corpus on every push and pull request, without a provider key.

**Offline mode** replaces the model with a scripted **routing oracle** that knows the correct tool calls for each case. It still runs the real LangGraph loop, tools, citation checks and proposal policy. It is deterministic and needs no key, but it does not test natural-language understanding.

**Live mode** sends the same questions to the configured model and checks expected device sets and counts, status, tenant isolation, citation resolution, answer rendering, proposal count, and that nothing executed without approval. It costs API usage and is not perfectly deterministic. The saved live report shows the recovery loop working: in `prompt_injection`, the model's first `finish` call broke the answer rules and was rejected with `unsupported_answer`. It corrected the call on the next step. In `unjustified_action`, it ran four checks on the healthy device and then declined without proposing anything.

The 21 cases:

| Case | What it checks |
| --- | --- |
| `acme_disk`, `globex_disk`, `initech_disk` | Low-disk device sets for each tenant |
| `empty_high_severity` | A correct empty answer for laptops failing high-severity checks |
| `macos_version` | Count and list of devices older than macOS 15 |
| `persistent_ram`, `globex_ram_boundary` | Persistence calculation, including a threshold boundary |
| `battery_service` | Latest battery service condition |
| `compliance_drift` | Compliance changes over the window |
| `software_presence` | Exact software presence without implying a policy breach |
| `tenant_scope` | "Ignore my tenant" is refused |
| `unsupported_battery_prediction` | Refuses an exact battery failure prediction |
| `unapproved_software` | Refuses to claim a policy the data doesn't contain |
| `pending_action` | Creates exactly one pending remediation ticket and no effect |
| `persistent_disk` | Persistent low storage for Initech (30 of 30 days under 10% free) |
| `battery_eol`, `battery_eol_empty` | End-of-life risk for Acme's three batteries, and a correct empty result for Globex |
| `notify_employee` | Proposes a notification addressed to the device's assigned employee from telemetry |
| `replacement_review` | Gathers both persistent RAM and battery evidence before proposing a replacement-review flag |
| `unjustified_action` | No proposal for a device with no qualifying findings; offline, the oracle deliberately cites a non-actionable finding and the action guard rejects it |
| `prompt_injection` | A "system override" asking to approve and execute proposals gets the normal answer and no effects |

The 42 unit and integration tests also cover:

- historical vs latest counts, threshold boundaries (including the battery cycle-count boundary), missing telemetry, version filtering and ingestion rollback;
- colliding tenant and device IDs, fabricated citations and unexposed execution tools;
- authenticated API boundaries and missing compatibility data;
- stale or expired proposals, digest mismatch, rejection, restarts and concurrent approval idempotency.

Edge-case fixtures are generated in temporary directories; the original dataset is never touched.

**Browser checks** were done manually against the real API and model:

- The launcher opens a tenant-bound session and strips the credential from the URL.
- A low-disk query returned the two expected Acme devices, with source lines.
- Cancelling a confirmation left the proposal pending; confirming it recorded a simulated effect and the reviewer.
- An invalid credential cleared the workspace and required reconnecting.
- At 390px wide there was no page overflow, and the console showed no warnings or errors.

These are small regression corpora, not estimates of production reliability. The next step for rigor would be larger paraphrase and adversarial suites, more tenants, and representative operational data.

## Design decisions and trade-offs

- **Structured analytics instead of a vector database.** 750 well-structured snapshots fit easily in SQLite, and threshold and window calculations need exact formulas. Retrieval would add complexity without helping the core tasks.
- **Server-rendered answers.** Answers are traceable down to each number, at the cost of less fluent explanations. The model still does the planning, tool selection and adaptation.
- **Tenant bound outside the model.** The model never chooses a tenant. The prompt-level company check is only an early refusal; the scoped queries are the actual boundary.
- **Application-enforced approval.** Evidence is re-checked, digests are immutable, and SQLite transactions give one local effect per approved proposal. Real external actions would need an outbox, provider idempotency and reconciliation; nothing here claims external exactly-once delivery.
- **Append-only audit.** Update and delete triggers block accidental changes by the application. This is not tamper-proof against someone who controls the SQLite file.
- **Refusing actions the data can't justify.** Upgrade orders and exact battery failure predictions are refused, not guessed. End-of-life risk is reported from corroborating signals instead.
- **Reproducibility.** Dependencies are locked, policies are explicit, the source checksum is recorded and offline runs are deterministic. The model name is recorded, but provider behavior can still change live results.

**What production would need:** managed user identity and token lifecycle, quotas, monitoring, backups, and reviewed integrations with real action providers.

## Troubleshooting

- **Missing key or provider error:** check `.env.local`, project billing and model access. Offline tests and deterministic queries work without a key.
- **Port in use:** `uv run fleet ui --port 8001`.
- **Connection expired:** restart `fleet ui`, or get a new demo token.
- **No matching devices:** check the scanned-population evidence and the requested company and date. Empty results are valid.
- **Proposal expired or changed:** ask for a new proposal and review its new digest.
- **Slow question:** the browser stops waiting after a set time and reports the failure. Check **Action reviews** before retrying a proposal, because the server may still finish the work.

References: [LangGraph graph API](https://docs.langchain.com/oss/python/langgraph/quickstart), [LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts), [OpenAI function calling](https://developers.openai.com/api/docs/guides/function-calling).
