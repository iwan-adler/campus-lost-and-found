// Campus Lost & Found web UI. Plain JavaScript that talks to the JSON API under /api.
// User-provided text is always inserted with textContent (never innerHTML) to prevent XSS.

const PAGE_SIZE = 12;
const $ = (selector) => document.querySelector(selector);

const state = { offset: 0, currentItem: null, lastCreatedId: null };

// ---------- API helper ----------

async function api(path, { method = "GET", body, token } = {}) {
  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (token) headers["X-Manage-Token"] = token;

  const response = await fetch(`/api${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json().catch(() => null);
  if (!response.ok) throw new Error(errorMessage(data, response.status));
  return data;
}

// FastAPI returns either {"detail": "text"} or {"detail": [{loc, msg}, ...]} for validation errors.
function errorMessage(data, statusCode) {
  const detail = data?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((e) => `${e.loc[e.loc.length - 1].replaceAll("_", " ")}: ${e.msg}`)
      .join("; ");
  }
  return `Request failed (${statusCode})`;
}

// ---------- token storage (convenience only; the server is the real gatekeeper) ----------

function loadTokens() {
  try {
    return JSON.parse(localStorage.getItem("manageTokens") || "{}");
  } catch {
    return {};
  }
}

function saveToken(itemId, token) {
  try {
    const tokens = loadTokens();
    tokens[itemId] = token;
    localStorage.setItem("manageTokens", JSON.stringify(tokens));
  } catch {
    /* storage unavailable (private mode): the user still has the token on screen */
  }
}

// ---------- helpers ----------

function setMessage(el, text, kind = "error") {
  el.textContent = text;
  el.className = `msg ${kind}`;
}

function formatDate(iso) {
  return new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

function fillSelect(select, values) {
  for (const value of values) select.append(new Option(value, value));
}

function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

// ---------- search & results ----------

function searchParams() {
  const form = new FormData($("#search-form"));
  const params = new URLSearchParams();
  for (const [key, value] of form) if (value.trim()) params.set(key, value.trim());
  params.set("limit", PAGE_SIZE);
  params.set("offset", state.offset);
  return params;
}

async function runSearch({ append = false } = {}) {
  if (!append) state.offset = 0;
  try {
    const page = await api(`/items?${searchParams()}`);
    const list = $("#results");
    if (!append) list.replaceChildren();
    page.items.forEach((item) => list.append(renderCard(item)));

    state.offset = page.offset + page.items.length;
    $("#more-btn").hidden = state.offset >= page.total;
    $("#result-count").textContent =
      page.total === 0 ? "No items match your search." : `${page.total} item${page.total === 1 ? "" : "s"}`;
  } catch (err) {
    $("#result-count").textContent = `Could not load items: ${err.message}`;
  }
}

function renderCard(item) {
  const card = $("#card-tpl").content.firstElementChild.cloneNode(true);
  const kind = card.querySelector(".kind");
  kind.textContent = item.kind;
  kind.classList.add(item.kind);
  card.querySelector(".status").textContent = item.status;
  card.querySelector(".title").textContent = item.title;
  card.querySelector(".meta").textContent =
    `${item.category} · ${item.location} · ${formatDate(item.event_date)}`;
  if (item.pending_claims > 0) {
    card.querySelector(".claims-count").textContent =
      `${item.pending_claims} pending claim${item.pending_claims === 1 ? "" : "s"}`;
  }
  const open = () => openDetail(item.id);
  card.addEventListener("click", open);
  card.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      open();
    }
  });
  return card;
}

// ---------- item detail ----------

async function openDetail(itemId) {
  setMessage($("#detail-msg"), "");
  let item;
  try {
    item = await api(`/items/${itemId}`);
  } catch (err) {
    alert(err.message);
    return;
  }
  state.currentItem = item;
  renderDetail(item);

  $("#claim-form").reset();
  $("#manage-tools").hidden = true;
  const savedToken = loadTokens()[item.id];
  $("#token-form").token.value = savedToken || "";
  $("#manage-details").open = Boolean(savedToken);
  if (savedToken) await unlockManage(savedToken);

  const dialog = $("#detail-dialog");
  if (!dialog.open) dialog.showModal();
}

function renderDetail(item) {
  const box = $("#detail");
  const badges = el("div", undefined, "badges");
  badges.append(el("span", item.kind, `badge ${item.kind}`), el("span", item.status, "badge"));
  box.replaceChildren(
    badges,
    el("h2", item.title),
    el("p", item.description, "desc"),
    el(
      "p",
      `${item.kind === "lost" ? "Lost" : "Found"} at ${item.location} on ${formatDate(item.event_date)} · ${item.category}`,
      "muted small",
    ),
  );
  $("#claim-section").hidden = !(item.kind === "found" && item.status === "open");
  for (const button of document.querySelectorAll("[data-status]")) {
    button.setAttribute("aria-pressed", String(button.dataset.status === item.status));
  }
}

async function unlockManage(token) {
  const item = state.currentItem;
  try {
    const claims = await api(`/items/${item.id}/claims`, { token });
    saveToken(item.id, token);
    renderClaims(claims, token);
    $("#manage-tools").hidden = false;
  } catch (err) {
    $("#manage-tools").hidden = true;
    setMessage($("#detail-msg"), err.message);
  }
}

function renderClaims(claims, token) {
  const list = $("#claims");
  list.replaceChildren();
  if (claims.length === 0) {
    list.append(el("li", "No claims yet.", "muted small"));
    return;
  }
  for (const claim of claims) {
    const li = el("li");
    const heading = el("p");
    heading.append(el("strong", claim.claimant_name), ` · ${claim.status}`);
    li.append(heading, el("p", `Contact: ${claim.contact}`, "small"), el("p", claim.details, "small muted"));

    if (claim.status === "pending") {
      const actions = el("p");
      for (const decision of ["approve", "reject"]) {
        const button = el("button", decision === "approve" ? "Approve" : "Reject");
        button.addEventListener("click", () => decideClaim(claim.id, decision, token));
        actions.append(button, " ");
      }
      li.append(actions);
    }
    list.append(li);
  }
}

async function decideClaim(claimId, decision, token) {
  const item = state.currentItem;
  try {
    await api(`/items/${item.id}/claims/${claimId}`, { method: "PATCH", body: { decision }, token });
    await openDetail(item.id);
    setMessage($("#detail-msg"), `Claim ${decision === "approve" ? "approved" : "rejected"}.`, "ok");
    runSearch();
  } catch (err) {
    setMessage($("#detail-msg"), err.message);
  }
}

async function changeStatus(newStatus) {
  const item = state.currentItem;
  const token = loadTokens()[item.id] || $("#token-form").token.value;
  try {
    await api(`/items/${item.id}/status`, { method: "PATCH", body: { status: newStatus }, token });
    await openDetail(item.id);
    setMessage($("#detail-msg"), `Status changed to ${newStatus}.`, "ok");
    runSearch();
  } catch (err) {
    setMessage($("#detail-msg"), err.message);
  }
}

async function submitClaim(event) {
  event.preventDefault();
  const form = event.target;
  const body = Object.fromEntries(new FormData(form));
  try {
    await api(`/items/${state.currentItem.id}/claims`, { method: "POST", body });
    await openDetail(state.currentItem.id); // refresh claim count / owner view
    $("#claim-section").hidden = true;
    setMessage($("#detail-msg"), "Claim submitted. The finder will contact you if it checks out.", "ok");
    runSearch();
  } catch (err) {
    setMessage($("#detail-msg"), err.message);
  }
}

// ---------- reporting ----------

function openReport() {
  const form = $("#report-form");
  form.reset();
  form.hidden = false;
  form.event_date.value = new Date().toLocaleDateString("en-CA"); // YYYY-MM-DD, local time
  form.event_date.max = form.event_date.value;
  $("#report-done").hidden = true;
  setMessage($("#report-msg"), "");
  $("#report-dialog").showModal();
}

async function submitReport(event) {
  event.preventDefault();
  const body = Object.fromEntries(new FormData(event.target));
  try {
    const created = await api("/items", { method: "POST", body });
    saveToken(created.id, created.manage_token);
    state.lastCreatedId = created.id;
    $("#new-token").textContent = created.manage_token;
    event.target.hidden = true;
    $("#report-done").hidden = false;
    runSearch();
  } catch (err) {
    setMessage($("#report-msg"), err.message);
  }
}

// ---------- wiring ----------

async function init() {
  try {
    const options = await api("/options");
    fillSelect($("#f-category"), options.categories);
    fillSelect($("#f-location"), options.locations);
    fillSelect($("#r-category"), options.categories);
    fillSelect($("#r-location"), options.locations);
  } catch (err) {
    $("#result-count").textContent = `Could not reach the server: ${err.message}`;
    return;
  }

  $("#search-form").addEventListener("submit", (e) => {
    e.preventDefault();
    runSearch();
  });
  for (const select of document.querySelectorAll("#search-form select")) {
    select.addEventListener("change", () => runSearch());
  }
  $("#more-btn").addEventListener("click", () => runSearch({ append: true }));

  $("#report-btn").addEventListener("click", openReport);
  $("#report-form").addEventListener("submit", submitReport);
  $("#copy-token").addEventListener("click", () =>
    navigator.clipboard?.writeText($("#new-token").textContent),
  );
  $("#view-new").addEventListener("click", () => {
    $("#report-dialog").close();
    openDetail(state.lastCreatedId);
  });

  $("#claim-form").addEventListener("submit", submitClaim);
  $("#token-form").addEventListener("submit", (e) => {
    e.preventDefault();
    setMessage($("#detail-msg"), "");
    unlockManage(e.target.token.value.trim());
  });
  for (const button of document.querySelectorAll("[data-status]")) {
    button.addEventListener("click", () => changeStatus(button.dataset.status));
  }

  runSearch();
}

init();
