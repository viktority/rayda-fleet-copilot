// All model, telemetry and API strings are textContent. Never render data as HTML.
export function node(tag, className = "", text = "") {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text) element.textContent = text;
  return element;
}

export function dateLabel(value) {
  return new Intl.DateTimeFormat("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(value));
}

export function evidence(finding) {
  const details = node("details");
  details.append(node("summary", "", "View evidence"));
  const content = node("div", "evidence");
  content.append(node("p", "method", finding.method));
  content.append(
    node(
      "p",
      "",
      `Finding ${finding.id} · ${finding.evidence.length} source observations`,
    ),
  );
  if (finding.evidence.length)
    content.append(
      node("p", "", `Source SHA-256: ${finding.evidence[0].source_sha256}`),
    );
  const table = node("table");
  const head = node("thead");
  const row = node("tr");
  for (const label of ["Device", "Observed (UTC)", "Source line", "Fields"]) {
    const th = node("th", "", label);
    th.scope = "col";
    row.append(th);
  }
  head.append(row);
  table.append(head);
  const body = node("tbody");
  for (const source of finding.evidence) {
    const tr = node("tr");
    for (const value of [
      source.device_id,
      source.collected_at,
      String(source.source_line),
      source.fields.join(", "),
    ])
      tr.append(node("td", "", value));
    body.append(tr);
  }
  table.append(body);
  content.append(table);
  details.append(content);
  return details;
}

const pct = (value, digits = 1) => `${Number(value).toFixed(digits)}%`;
const count = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

// Short, scannable facts derived from the server's computed values. The canonical
// sentence is still shown beneath them; chips never add facts of their own.
export function chips(finding) {
  const v = finding.values || {};
  const warn = (label) => ({ label, warn: true });
  const plain = (label) => ({ label, warn: false });
  switch (finding.kind) {
    case "low_disk":
      return (v.volumes || []).map((x) => warn(`${pct(x.free_percent, 2)} free on ${x.mount_point}`));
    case "high_ram":
      return [warn(`${pct(v.used_percent)} RAM used`)];
    case "compliance":
      return (v.failed_checks || []).map((c) => warn(`${c.check_id} failed · ${c.severity}`));
    case "os_older":
      return [warn(`macOS ${v.version}`)];
    case "battery_service":
      return [warn("Service Recommended")];
    case "software":
      return (v.software || []).map((x) => plain(`${x.name} ${x.version}`));
    case "inventory":
      return [plain(v.model), plain(`${v.os} ${v.version}`), plain(v.employee_id)];
    case "persistent_ram":
      return [
        warn(`>${v.threshold}% RAM on ${v.hits}/${v.span_days} days`),
        plain(`mean ${pct(v.mean_percent)}`),
      ];
    case "persistent_disk":
      return [
        warn(`<${v.threshold}% free on ${v.hits}/${v.span_days} days (${v.component})`),
        plain(`latest ${pct(v.last_percent, 2)} free`),
      ];
    case "battery_eol_risk":
      return [
        warn(v.latest_condition),
        warn(`${pct(-v.capacity_drop_percent)} capacity`),
        (v.signals?.high_cycle_count ? warn : plain)(
          `${Number(v.latest_cycle_count).toLocaleString("en-GB")} cycles`,
        ),
        plain(`${v.signals_met}/3 risk signals`),
      ];
    case "battery_trend":
      return [plain(`capacity ${v.first_capacity} → ${v.last_capacity}`)];
    case "battery_unknown":
      return [warn("No battery readings")];
    case "compliance_drift":
      return [(v.after === "fail" ? warn : plain)(`${v.check_id}: ${v.before} → ${v.after}`)];
    case "software_drift":
      return [plain(`${(v.added || []).length} added`), plain(`${(v.removed || []).length} removed`)];
    default:
      return [];
  }
}

export function findingRow(finding) {
  if (!finding.device_id) {
    const row = node("article", "finding summary");
    row.append(node("p", "finding-text", finding.text), evidence(finding));
    return row;
  }
  const row = node("article", "finding");
  const head = node("div", "finding-head");
  head.append(
    node("strong", "finding-title", finding.values?.hostname || finding.device_id),
    node("span", "finding-device", finding.device_id),
  );
  const facts = node("div", "chips");
  for (const chip of chips(finding)) facts.append(node("span", `chip${chip.warn ? " warn" : ""}`, chip.label));
  const last = finding.evidence.at(-1);
  if (last) facts.append(node("span", "chip date", `as of ${dateLabel(last.collected_at)}`));
  row.append(head, facts, node("p", "finding-text", finding.text), evidence(finding));
  return row;
}

export const statusLabels = {
  answered: "Answered",
  insufficient_data: "Insufficient data",
  clarification: "Needs clarification",
  refused: "Refused",
  incomplete: "Incomplete",
};

const humanize = (code) => String(code || "error").replaceAll("_", " ");

function argumentSummary(step) {
  const args = step.arguments;
  if (!args) return "";
  if (step.tool === "make_plan") return count(args.steps.length, "step");
  if (step.tool === "finish") return `${args.status} · ${count(args.finding_ids.length, "finding")}`;
  return Object.entries(args)
    .filter(([, value]) => value !== null && value !== undefined)
    .map(([key, value]) => `${key}: ${Array.isArray(value) ? value.join(", ") : value}`)
    .join(" · ");
}

export function agentTrace(result) {
  const errors = result.trace.filter((t) => !t.ok).length;
  const recovered = errors && result.trace.at(-1)?.ok ? ", recovered" : "";
  const trace = node("details", "trace");
  trace.open = true;
  const summary = node("summary", "", "Agent steps");
  summary.append(
    node(
      "span",
      "trace-meta",
      `${count(result.trace.length, "tool call")}${errors ? ` · ${count(errors, "rejected call")}${recovered}` : ""}`,
    ),
  );
  trace.append(summary);
  if (result.plan.length) {
    trace.append(node("h3", "trace-heading", "Plan"));
    const plan = node("ol", "plan");
    for (const step of result.plan) plan.append(node("li", "", step));
    trace.append(plan);
  }
  trace.append(node("h3", "trace-heading", "Tool calls"));
  const timeline = node("ol", "timeline");
  for (const step of result.trace) {
    const item = node("li", `step ${step.ok ? "ok" : "rejected"}`);
    const icon = node("span", "step-icon", step.ok ? "✓" : "✗");
    icon.setAttribute("aria-label", step.ok ? "Succeeded" : "Rejected");
    const body = node("div", "step-body");
    body.append(node("code", "step-tool", step.tool));
    const args = argumentSummary(step);
    if (args) body.append(node("span", "step-args", args));
    if (!step.ok)
      body.append(
        node(
          "p",
          "step-error",
          `Rejected by the server (${humanize(step.error)}). The error was returned to the model as an observation.`,
        ),
      );
    item.append(icon, body);
    timeline.append(item);
  }
  trace.append(
    timeline,
    node("p", "hint", `Run ${result.run_id} · ${result.model} · ${result.elapsed_seconds}s`),
  );
  return trace;
}

export function renderAnswer(container, result, showReviews) {
  container.replaceChildren();
  const header = node("div", "answer-status");
  header.append(
    node("span", `badge status-${result.status}`, statusLabels[result.status] || humanize(result.status)),
    node(
      "span",
      "answer-meta",
      `${count(result.findings.filter((f) => f.device_id).length, "device finding")} · ${count(result.trace.length, "tool call")}`,
    ),
  );
  container.append(header);
  if (result.status !== "answered") {
    // Answer text is server-authored and remains plain text even for refusals.
    container.append(node("p", "status-note", result.answer || "No supported answer was returned."));
  }
  for (const finding of result.findings) container.append(findingRow(finding));
  if (result.proposals.length) {
    const button = node(
      "button",
      "button proposal-link",
      `Review ${result.proposals.length} proposed action${result.proposals.length === 1 ? "" : "s"}`,
    );
    button.addEventListener("click", showReviews);
    container.append(button);
  }
  container.append(agentTrace(result));
}

export const actionLabels = {
  open_remediation_ticket: "Remediation ticket",
  flag_device_for_replacement: "Replacement assessment",
  notify_employee: "Employee notification",
  create_upgrade_order: "Upgrade order",
};

export function renderProposals(container, proposals, role, onDecision) {
  container.replaceChildren();
  if (!proposals.length) {
    const empty = node("div", "review-empty");
    empty.append(
      node("h2", "", "No actions to review"),
      node(
        "p",
        "",
        "Enable action proposals when asking the copilot to suggest a next step. Every proposal will appear here for review.",
      ),
    );
    container.append(empty);
    return;
  }
  const sorted = [...proposals].sort(
    (a, b) =>
      Number(b.status === "pending") - Number(a.status === "pending") ||
      b.created_at.localeCompare(a.created_at),
  );
  for (const proposal of sorted) {
    const item = node("article", "proposal");
    const head = node("div", "proposal-head");
    head.append(
      node(
        "h2",
        "",
        `${actionLabels[proposal.action] || proposal.action} · ${proposal.hostname || proposal.device_id}`,
      ),
    );
    const expired = new Date(proposal.expires_at) <= new Date();
    const label =
      proposal.status === "executed"
        ? "Simulated"
        : proposal.status === "pending" && expired
          ? "Expired"
          : proposal.status;
    head.append(node("span", "proposal-status", label));
    item.append(
      head,
      node(
        "p",
        "proposal-meta",
        `${proposal.device_id} · ${proposal.id} · Created ${dateLabel(proposal.created_at)}`,
      ),
    );
    for (const finding of proposal.findings) item.append(findingRow(finding));
    item.append(node("p", "hint", proposal.scope));
    if (proposal.status === "pending") {
      if (role === "admin") {
        const actions = node("div", "proposal-actions");
        const approve = node("button", "button primary", "Approve simulation");
        approve.disabled = expired;
        approve.addEventListener("click", () =>
          onDecision(proposal.id, "approve"),
        );
        const reject = node("button", "button", "Reject");
        reject.addEventListener("click", () =>
          onDecision(proposal.id, "reject"),
        );
        actions.append(approve, reject);
        item.append(actions);
        if (expired)
          item.append(
            node(
              "p",
              "hint",
              "This proposal has expired. Ask the copilot for a fresh evidence review.",
            ),
          );
      } else
        item.append(
          node(
            "p",
            "hint",
            "An administrator must approve or reject this proposal.",
          ),
        );
    } else if (proposal.decided_by)
      item.append(node("p", "hint", `Reviewed by ${proposal.decided_by}`));
    container.append(item);
  }
}
