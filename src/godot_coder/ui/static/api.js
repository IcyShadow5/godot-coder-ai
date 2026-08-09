/* ── DOM shortcuts ──────────────────────────────────────────────── */
const $ = (selector, root = document) => root.querySelector(selector);

const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

/* ── API client ────────────────────────────────────────────────── */
async function api(path, options = {}) {
  const method = String(options.method || "GET").toUpperCase();
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (!["GET", "HEAD", "OPTIONS"].includes(method) && state.remoteCsrf) {
    headers["X-Godot-Coder-CSRF"] = state.remoteCsrf;
  }
  const response = await fetch(path, {
    credentials: "same-origin",
    ...options,
    method,
    headers,
  });
  const text = await response.text();
  let payload = null;
  if (text) {
    try { payload = JSON.parse(text); } catch { payload = text; }
  }
  if (!response.ok) {
    const detail = payload?.detail || payload || `${response.status} ${response.statusText}`;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return payload;
}

/* ── Formatters ────────────────────────────────────────────────── */
function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatNumber(value) {
  return new Intl.NumberFormat("en-US").format(value || 0);
}

function formatDate(timestamp) {
  if (!timestamp) return "–";
  return new Intl.DateTimeFormat("en-US", { dateStyle: "short", timeStyle: "short" }).format(new Date(timestamp * 1000));
}

function toast(message, type = "info", duration = 4200) {
  const item = document.createElement("div");
  item.className = `toast ${type}`;
  item.textContent = message;
  $("#toast-region").append(item);
  setTimeout(() => item.remove(), duration);
}

function formatCompact(value) {
  const number = Number(value || 0);
  if (number >= 1e9) return `${(number / 1e9).toFixed(2)}B`;
  if (number >= 1e6) return `${(number / 1e6).toFixed(1)}M`;
  if (number >= 1e3) return `${(number / 1e3).toFixed(1)}K`;
  return formatNumber(number);
}

/* ── UI helpers ─────────────────────────────────────────────────── */
const workflowJobLabels = {
  "corpus-audit": "Data audit",
  "local-source-import": "Check private projects",
  "remote-source-download": "Download remote source",
  "hardware-autotune": "Hardware autotuner",
  "training-smoke-50": "50-step probe run",
  "training": "Training",
};

function setText(selector, value, title = null) {
  const element = $(selector);
  if (!element) return;
  element.textContent = value == null ? "–" : String(value);
  if (title != null) element.title = String(title);
}

function formatBytes(value) {
  const number = Number(value || 0);
  if (number >= 1024 ** 3) return `${(number / 1024 ** 3).toFixed(1)} GiB`;
  if (number >= 1024 ** 2) return `${(number / 1024 ** 2).toFixed(1)} MiB`;
  if (number >= 1024) return `${(number / 1024).toFixed(1)} KiB`;
  return `${number} B`;
}
