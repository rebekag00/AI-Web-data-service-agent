const form = document.querySelector("#run-form");
const button = document.querySelector("#run-button");
const emptyState = document.querySelector("#empty-state");
const content = document.querySelector("#result-content");
const resultTitle = document.querySelector("#result-title");
const stateBadge = document.querySelector("#run-state");
const excelLink = document.querySelector("#excel-link");
const message = document.querySelector("#message");
const records = document.querySelector("#records");
const attempts = document.querySelector("#attempts");
const validation = document.querySelector("#validation");

function setState(text, style) {
  stateBadge.textContent = text.replaceAll("_", " ");
  stateBadge.className = `state state-${style}`;
}

function textElement(tag, className, value) {
  const element = document.createElement(tag);
  element.className = className;
  element.textContent = value;
  return element;
}

function check(label, value) {
  const item = document.createElement("span");
  item.className = value ? "check check-pass" : "check check-fail";
  item.textContent = `${value ? "✓" : "×"} ${label}`;
  return item;
}

function renderResult(result) {
  emptyState.classList.add("hidden");
  content.classList.remove("hidden");
  document.querySelector("#company").textContent = result.intent?.company || "Not parsed";
  document.querySelector("#year").textContent = result.intent?.year || "—";
  const promptLanguage = (result.intent?.language || "—").toUpperCase();
  const searchLanguage = (result.intent?.search_language || result.intent?.language || "—").toUpperCase();
  const jurisdiction = result.intent?.jurisdiction && result.intent.jurisdiction !== "unknown"
    ? ` · ${result.intent.jurisdiction}` : "";
  document.querySelector("#language").textContent = `${promptLanguage} → ${searchLanguage}${jurisdiction}`;
  document.querySelector("#attempt-count").textContent = result.search_attempts.length;
  resultTitle.textContent = result.delivery_status === "original_report_verified" ? "Original report verified" :
    result.selected_source ? "Verified source found with a limitation" : "No verified source found";

  const style = result.state === "completed" ? "success" : result.state === "failed" ? "failed" : "warning";
  setState(result.state, style);

  attempts.replaceChildren(textElement("h3", "subheading", "Search and retry loop"));
  for (const item of result.search_attempts) {
    const card = document.createElement("article");
    card.className = "attempt";
    const head = document.createElement("div");
    head.className = "attempt-head";
    head.append(textElement("strong", "", `Attempt ${item.attempt}`));
    head.append(textElement("span", "", item.outcome));
    card.append(head, textElement("code", "query", item.query));
    const accepted = item.candidates.filter((candidate) => candidate.accepted).length;
    card.append(textElement("p", "candidate-count", `${item.candidates.length} candidates checked · ${accepted} passed all core checks`));
    attempts.append(card);
  }

  validation.replaceChildren();
  if (result.selected_source) {
    validation.classList.remove("hidden");
    validation.append(textElement("h3", "subheading", "Selected source validation"));
    const checks = document.createElement("div");
    checks.className = "checks";
    checks.append(
      check("Company", result.selected_source.company_match),
      check("Year", result.selected_source.year_match),
      check("Report context", result.selected_source.report_match),
      check("Original PDF", result.selected_source.original_document),
    );
    validation.append(checks);
  } else {
    validation.classList.add("hidden");
  }

  records.replaceChildren();
  if (result.selected_source) {
    const card = document.createElement("article");
    card.className = "record selected-record";
    card.append(textElement("p", "section-label", "SELECTED SOURCE"));
    card.append(textElement("h3", "", result.selected_source.title || result.selected_source.url));
    const link = document.createElement("a");
    link.href = result.selected_source.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = result.selected_source.url;
    card.append(link);
    if (result.selected_source.reasons?.length) card.append(textElement("p", "source-note", result.selected_source.reasons.join(" · ")));
    records.append(card);
  }

  const notices = result.errors.map((item) => item.error);
  if (result.excel_error) notices.push(result.excel_error);
  if (notices.length || result.delivery_status !== "original_report_verified") {
    message.textContent = notices.join(" ") || "The company, year, and report context were verified, but the original report file was not publicly downloadable. The Excel clearly records this limitation.";
    message.classList.remove("hidden");
  } else {
    message.classList.add("hidden");
  }

  if (result.excel_download) {
    excelLink.href = result.excel_download;
    excelLink.classList.remove("hidden");
  } else {
    excelLink.classList.add("hidden");
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  button.disabled = true;
  button.textContent = "Searching and validating…";
  resultTitle.textContent = "Agent is working";
  setState("running", "running");
  message.classList.add("hidden");
  try {
    const response = await fetch("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt: document.querySelector("#prompt").value.trim() }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "The search could not start.");
    renderResult(result);
  } catch (error) {
    emptyState.classList.add("hidden");
    content.classList.remove("hidden");
    resultTitle.textContent = "Search failed";
    setState("failed", "failed");
    message.textContent = error.message;
    message.classList.remove("hidden");
    attempts.replaceChildren();
    validation.classList.add("hidden");
    records.replaceChildren();
    excelLink.classList.add("hidden");
  } finally {
    button.disabled = false;
    button.textContent = "Find and verify report";
  }
});
