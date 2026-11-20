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

export function findingRow(finding) {
  const row = node(
    "article",
    `finding${finding.kind.endsWith("summary") ? " summary" : ""}`,
  );
  row.append(node("p", "finding-text", finding.text), evidence(finding));
  return row;
}

export function renderAnswer(container, result, showReviews) {
  container.replaceChildren();
  if (result.status !== "answered") {
    // Answer text is server-authored and remains plain text even for refusals.
    container.append(
      node(
        "p",
        "status-note",
        result.answer || "No supported answer was returned.",
      ),
    );
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
  const trace = node("details", "trace");
  trace.append(node("summary", "", "Agent steps"));
  const list = node("ol");
  for (const step of result.plan) list.append(node("li", "", step));
  trace.append(list);
  const tools = node(
    "p",
    "method",
    result.trace
      .map((t) => `${t.tool}${t.ok ? "" : ` (${t.error})`}`)
      .join(" → "),
  );
  trace.append(
    tools,
    node(
      "p",
      "hint",
      `Run ${result.run_id} · ${result.model} · ${result.elapsed_seconds}s`,
    ),
  );
  container.append(trace);
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
        `${actionLabels[proposal.action] || proposal.action} · ${proposal.device_id}`,
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
        `${proposal.id} · Created ${dateLabel(proposal.created_at)}`,
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
