import { epoch, initialToken, post, request, setToken } from "./api.js";
import {
  actionLabels,
  dateLabel,
  findingRow,
  node,
  renderAnswer,
  renderProposals,
} from "./render.js";

const $ = (id) => document.getElementById(id);
let context = null;
let decision = null;
let deciding = false;

function errorAt(id, message = "") {
  $(id).textContent = message;
  $(id).hidden = !message;
}

function disconnect(message = "") {
  setToken("");
  context = null;
  decision = null;
  deciding = false;
  $("identity").hidden = true;
  $("workspace").hidden = true;
  $("connect-screen").hidden = false;
  $("answer-content").replaceChildren(
    node("p", "empty", "Ask a question or choose an example to get started."),
  );
  $("reviews-list").replaceChildren();
  $("pending-count").hidden = true;
  $("question").value = "";
  $("allow-proposals").checked = false;
  $("ask-submit").disabled = false;
  $("connect-submit").disabled = false;
  $("confirm-decision").disabled = false;
  $("cancel-decision").disabled = false;
  $("loading").hidden = true;
  $("answer").hidden = false;
  $("question-form").removeAttribute("aria-busy");
  errorAt("ask-error");
  errorAt("review-error");
  $("decision-dialog").close();
  errorAt("connect-error", message);
  $("access-token").focus();
}

async function connect(token) {
  // A new identity must never inherit the previous identity's rendered data.
  disconnect();
  setToken(token.trim());
  const started = epoch();
  $("connect-submit").disabled = true;
  errorAt("connect-error");
  try {
    context = await request("/v1/context");
    if (started !== epoch()) return;
    $("company").textContent = context.company_name;
    $("role").textContent = {
      admin: "Administrator",
      operator: "Operator",
      viewer: "Viewer",
    }[context.role];
    $("device-count").textContent = `${context.devices} devices`;
    $("observation-date").textContent =
      `Latest observation: ${dateLabel(context.latest_observation)}`;
    $("allow-proposals").disabled = context.role === "viewer";
    $("allow-proposals").title =
      context.role === "viewer"
        ? "Operator access is required to propose actions"
        : "";
    $("connect-screen").hidden = true;
    $("workspace").hidden = false;
    $("identity").hidden = false;
    $("access-token").value = "";
    showPage("copilot");
    refreshReviews();
  } catch (error) {
    if (started === epoch()) disconnect(error.message);
  } finally {
    if (started === epoch()) $("connect-submit").disabled = false;
  }
}

function showPage(page) {
  if (!context) return;
  $("copilot-page").hidden = page !== "copilot";
  $("reviews-page").hidden = page !== "reviews";
  for (const button of document.querySelectorAll("[data-page]")) {
    const selected = button.dataset.page === page;
    button.classList.toggle("selected", selected);
    if (selected) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  }
  if (page === "reviews") refreshReviews();
}

async function refreshReviews() {
  if (!context) return;
  const started = epoch();
  $("refresh-reviews").disabled = true;
  errorAt("review-error");
  try {
    const proposals = await request("/v1/proposals");
    if (started !== epoch()) return;
    renderProposals($("reviews-list"), proposals, context.role, reviewDecision);
    const count = proposals.filter((p) => p.status === "pending").length;
    $("pending-count").textContent = String(count);
    $("pending-count").hidden = !count;
  } catch (error) {
    if (started === epoch()) errorAt("review-error", error.message);
  } finally {
    if (started === epoch()) $("refresh-reviews").disabled = false;
  }
}

async function reviewDecision(id, kind) {
  const started = epoch();
  errorAt("review-error");
  try {
    const proposal = await request(`/v1/proposals/${encodeURIComponent(id)}`);
    if (started !== epoch()) return;
    if (proposal.status !== "pending") {
      await refreshReviews();
      return;
    }
    decision = { proposal, kind, started };
    $("decision-title").textContent =
      kind === "approve" ? "Approve this simulation?" : "Reject this proposal?";
    $("decision-summary").textContent =
      `${actionLabels[proposal.action]} for ${proposal.device_id}. ${kind === "approve" ? "This records a simulated action only." : "This proposal will not be executed."}`;
    $("decision-details").replaceChildren();
    for (const finding of proposal.findings)
      $("decision-details").append(findingRow(finding));
    $("decision-details").append(
      node("p", "digest", `Review digest: ${proposal.digest}`),
    );
    $("confirm-decision").textContent =
      kind === "approve" ? "Confirm simulation" : "Confirm rejection";
    $("confirm-decision").classList.toggle("danger", kind === "reject");
    errorAt("decision-error");
    $("decision-dialog").showModal();
    $("cancel-decision").focus();
  } catch (error) {
    if (started === epoch()) errorAt("review-error", error.message);
  }
}

$("connect-form").addEventListener("submit", (event) => {
  event.preventDefault();
  connect($("access-token").value);
});
$("disconnect").addEventListener("click", () => disconnect());
window.addEventListener("fleet-unauthorized", () =>
  disconnect(
    "Your connection expired. Reopen the local launcher or reconnect.",
  ),
);
for (const button of document.querySelectorAll("[data-page]"))
  button.addEventListener("click", () => showPage(button.dataset.page));
for (const button of document.querySelectorAll("[data-question]"))
  button.addEventListener("click", () => {
    $("question").value = button.dataset.question;
    $("question").focus();
  });
$("refresh-reviews").addEventListener("click", refreshReviews);
$("question-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const question = $("question").value.trim();
  if (!question || !context) return;
  const started = epoch();
  $("ask-submit").disabled = true;
  $("loading").hidden = false;
  $("answer").hidden = true;
  $("question-form").setAttribute("aria-busy", "true");
  errorAt("ask-error");
  try {
    const result = await post("/v1/ask", {
      question,
      allow_proposals: $("allow-proposals").checked,
    });
    if (started !== epoch()) return;
    renderAnswer($("answer-content"), result, () => showPage("reviews"));
    refreshReviews();
  } catch (error) {
    if (started === epoch()) errorAt("ask-error", error.message);
  } finally {
    if (started === epoch()) {
      $("ask-submit").disabled = false;
      $("loading").hidden = true;
      $("answer").hidden = false;
      $("question-form").removeAttribute("aria-busy");
    }
  }
});
$("question").addEventListener("keydown", (event) => {
  if (
    (event.metaKey || event.ctrlKey) &&
    event.key === "Enter" &&
    !$("ask-submit").disabled
  )
    $("question-form").requestSubmit();
});
$("cancel-decision").addEventListener("click", () => {
  if (!deciding) {
    $("decision-dialog").close();
    decision = null;
  }
});
$("decision-dialog").addEventListener("cancel", (event) => {
  if (deciding) event.preventDefault();
  else decision = null;
});
$("decision-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!decision || deciding) return;
  const current = decision;
  deciding = true;
  $("confirm-decision").disabled = true;
  $("cancel-decision").disabled = true;
  errorAt("decision-error");
  try {
    await post(
      `/v1/proposals/${encodeURIComponent(current.proposal.id)}/decision`,
      {
        decision: current.kind,
        expected_digest: current.proposal.digest,
        confirmed: true,
      },
    );
    if (current.started !== epoch()) return;
    $("decision-dialog").close();
    decision = null;
    await refreshReviews();
  } catch (error) {
    if (current.started === epoch()) errorAt("decision-error", error.message);
  } finally {
    if (current.started === epoch()) {
      deciding = false;
      $("confirm-decision").disabled = false;
      $("cancel-decision").disabled = false;
    }
  }
});

const saved = initialToken();
if (saved) connect(saved);
window.addEventListener("hashchange", () => {
  if (new URLSearchParams(location.hash.slice(1)).has("access"))
    connect(initialToken());
});
