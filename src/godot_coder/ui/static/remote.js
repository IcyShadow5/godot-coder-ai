const remoteWriteControlIds = [
  "generate-button", "validate-last", "save-last", "start-training", "probe-profiles", "prepare-data",
  "build-curriculum", "validate-curriculum", "prepare-curriculum", "save-corpus-sources",
  "select-verified-expansion", "select-core-expansion", "select-max-expansion", "add-custom-source", "open-local-source-inbox", "import-local-sources",
  "corpus-fetch", "corpus-build", "corpus-validate", "corpus-audit", "corpus-tokenizer", "corpus-prepare",
  "corpus-instructions", "benchmark-model", "stop-job", "professional-audit", "professional-autotune",
  "professional-smoke", "new-file", "save-editor", "delete-editor", "validate-editor", "create-file",
  "remote-download-source", "remote-upload-source", "remote-import-sources",
];

function applyRemoteWritePolicy(remote = state.remote) {
  const locked = Boolean(remote?.is_remote && !remote?.can_write);
  document.body.classList.toggle("remote-readonly", locked);
  for (const id of remoteWriteControlIds) {
    const element = document.getElementById(id);
    if (!element) continue;
    if (locked) {
      if (!element.disabled) element.dataset.remoteWasEnabled = "true";
      element.disabled = true;
      element.title = remote?.identity_allowed
        ? "Remote write access is locked. Open the Remote tab and enter the PIN."
        : "This Tailscale identity is not allowed.";
    } else if (element.dataset.remoteWasEnabled === "true") {
      element.disabled = false;
      delete element.dataset.remoteWasEnabled;
      element.removeAttribute("title");
    }
  }
}

function renderRemoteInbox() {
  const target = $("#remote-inbox-list");
  if (!target) return;
  const local = state.corpus?.local_sources;
  const items = local?.inbox_items || [];
  target.innerHTML = items.length ? items.map((item) => `
    <div class="remote-inbox-item">
      <strong title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</strong>
      <small>${item.kind === "zip" ? formatBytes(item.size_bytes) : "Local project folder"}</small>
    </div>
  `).join("") : '<div class="empty-state">No ZIPs or project folders in the private import folder yet.</div>';
}

function renderRemoteDownloadProgress(job = state.currentJob) {
  const box = $("#remote-download-progress");
  if (!box) return;
  const relevant = job?.kind === "remote-source-download";
  if (!relevant) { box.hidden = true; return; }
  const progress = job.progress_state || {};
  const received = Number(progress.bytes_received || 0);
  const total = Number(progress.bytes_total || 0);
  const percent = total > 0 ? Math.max(0, Math.min(100, Math.round(received / total * 100))) : Math.round(Number(job.progress || 0) * 100);
  box.hidden = false;
  box.querySelector("i").style.width = `${percent}%`;
  box.querySelector("strong").textContent = job.status === "completed"
    ? `Done · ${formatBytes(received)}`
    : job.status === "failed"
      ? "Download failed"
      : `${formatBytes(received)}${total ? ` / ${formatBytes(total)}` : ""}`;
}

function renderRemote(remote) {
  if (!$("#remote-state-card")) return;
  renderRemoteInbox();
  renderRemoteDownloadProgress(state.currentJob);
  if (!remote) return;
  const isRemote = Boolean(remote.is_remote);
  const identity = remote.identity?.login || remote.identity?.display_name || "–";
  const tailscale = remote.tailscale || {};
  let stateName = "loading";
  let title = "Local only";
  let detail = remote.configured ? "Remote access is configured, but this view runs locally." : "Run python -m godot_coder.remote_access configure on the PC.";
  if (isRemote && !remote.can_read) {
    stateName = "blocked"; title = "Access not allowed"; detail = "This Tailscale identity is not on the local allowlist.";
  } else if (isRemote && remote.can_write) {
    stateName = "ready"; title = "Remote write access active"; detail = "Jobs and imports still run entirely on the PC.";
  } else if (isRemote) {
    stateName = "locked"; title = "Safe read mode"; detail = "Status and logs are visible. Write actions require the PIN.";
  } else if (remote.enabled && tailscale.online) {
    stateName = "ready"; title = "Remote Studio prepared"; detail = tailscale.serve_url || "Tailscale is online. Check the serve configuration.";
  } else if (remote.enabled) {
    stateName = "locked"; title = "Remote configured"; detail = "Tailscale is currently not detected as online.";
  }
  $("#remote-state-card").dataset.state = stateName;
  setText("#remote-state-title", title);
  setText("#remote-state-detail", detail);
  setText("#remote-access-kind", isRemote ? "Tailscale Serve" : "Local browser");
  setText("#remote-identity", identity, identity);
  setText("#remote-tailscale-state", tailscale.online ? "Online" : tailscale.installed ? (tailscale.backend_state || "Not connected") : "Not found");
  const serveUrl = tailscale.serve_url || (isRemote ? location.origin : "–");
  setText("#remote-serve-url", serveUrl, serveUrl);
  setText("#remote-serve-command", remote.serve_command || "tailscale serve --bg http://127.0.0.1:8765");

  const lockState = $("#remote-lock-state");
  const unlockForm = $("#remote-unlock-form");
  const lockButton = $("#remote-lock");
  if (!isRemote) {
    lockState.textContent = remote.enabled ? "On your phone, the Studio opens in read mode first. There the PIN can be entered." : "Remote access is not configured locally yet.";
    unlockForm.hidden = true; lockButton.hidden = true;
  } else if (!remote.identity_allowed) {
    lockState.textContent = "This Tailscale identity is not allowed.";
    unlockForm.hidden = true; lockButton.hidden = true;
  } else if (remote.can_write) {
    lockState.textContent = `Unlocked for ${identity}. The session expires automatically.`;
    unlockForm.hidden = true; lockButton.hidden = false;
  } else {
    lockState.textContent = `Read mode for ${identity}. Enter the PIN only in this private HTTPS session.`;
    unlockForm.hidden = false; lockButton.hidden = true;
  }

  const banner = $("#remote-access-banner");
  banner.hidden = !isRemote || remote.can_write;
  if (isRemote && !remote.can_write) {
    setText("#remote-banner-title", remote.identity_allowed ? "Remote read mode" : "Remote access blocked");
    setText("#remote-banner-detail", remote.identity_allowed ? "Write actions require the PIN." : "Identity not allowed.");
  }
  applyRemoteWritePolicy(remote);
}

async function refreshRemote(showToast = false) {
  try {
    state.remote = await api("/api/remote/status");
    if (state.remote?.csrf_token) {
      state.remoteCsrf = state.remote.csrf_token;
      sessionStorage.setItem("godot-coder-remote-csrf", state.remoteCsrf);
    } else if (state.remote?.is_remote && !state.remote?.authenticated) {
      state.remoteCsrf = "";
      sessionStorage.removeItem("godot-coder-remote-csrf");
    }
    renderRemote(state.remote);
    if (showToast) toast("Remote status updated.");
  } catch (error) {
    if (showToast) toast(error.message, "error");
  }
}

async function runRemoteSelfCheck() {
  const resultBox = $("#remote-self-check-result");
  const button = $("#remote-self-check");
  if (!resultBox || !button) return;
  button.disabled = true;
  resultBox.hidden = false;
  resultBox.dataset.status = "running";
  resultBox.textContent = "Checking the remote connection locally …";
  try {
    const report = await api("/api/remote/self-check");
    resultBox.dataset.status = report.ok ? "passed" : "failed";
    resultBox.innerHTML = (report.checks || []).map((check) =>
      `<div><strong>${check.passed ? "✓" : "×"} ${escapeHtml(check.name)}</strong><span>${escapeHtml(check.detail || "")}</span></div>`
    ).join("") || "No check results received.";
    toast(report.ok ? "Remote self-test passed." : "Remote self-test found problems.", report.ok ? "info" : "error", 6500);
  } catch (error) {
    resultBox.dataset.status = "failed";
    resultBox.textContent = error.message;
    toast(error.message, "error");
  } finally {
    button.disabled = false;
  }
}

async function unlockRemote(event) {
  event?.preventDefault();
  const pin = $("#remote-pin").value.trim();
  if (!/^\d{6,12}$/.test(pin)) return toast("The PIN must consist of 6 to 12 digits.", "error");
  try {
    const result = await api("/api/remote/unlock", { method: "POST", body: JSON.stringify({ pin }) });
    state.remoteCsrf = result.csrf_token;
    sessionStorage.setItem("godot-coder-remote-csrf", state.remoteCsrf);
    $("#remote-pin").value = "";
    await refreshRemote();
    await refreshAll();
    toast("Remote write access unlocked.");
  } catch (error) { toast(error.message, "error"); }
}

async function lockRemote() {
  try {
    await api("/api/remote/lock", { method: "POST", body: "{}" });
  } catch (error) {
    if (!/locked/i.test(error.message)) toast(error.message, "error");
  }
  state.remoteCsrf = "";
  sessionStorage.removeItem("godot-coder-remote-csrf");
  await refreshRemote();
  toast("Remote write access locked.");
}

async function startRemoteSourceDownload() {
  const url = $("#remote-source-url").value.trim();
  const confirmed = $("#confirm-remote-link-ownership").checked;
  if (!url) return toast("Enter a repository or ZIP link.", "error");
  if (!confirmed) return toast("First confirm the local usage permission.", "error");
  try {
    const job = await api("/api/jobs/remote/source-download", {
      method: "POST",
      body: JSON.stringify({ url, confirm_owned: true }),
    });
    state.currentJob = job;
    renderJob(job);
    toast("The PC downloads and checks the remote source.");
  } catch (error) { toast(error.message, "error"); }
}

function uploadRemoteSource() {
  const file = $("#remote-source-file").files?.[0];
  if (!file) return toast("Select a ZIP first.", "error");
  if (!file.name.toLowerCase().endsWith(".zip")) return toast("Only ZIP files are accepted.", "error");
  if (!$("#confirm-remote-upload-ownership").checked) return toast("First confirm the local usage permission.", "error");
  const box = $("#remote-upload-progress");
  box.hidden = false;
  box.querySelector("i").style.width = "0%";
  box.querySelector("strong").textContent = "0 %";
  const xhr = new XMLHttpRequest();
  xhr.open("POST", `/api/remote/sources/upload?filename=${encodeURIComponent(file.name)}&confirm_owned=true`);
  xhr.withCredentials = true;
  xhr.setRequestHeader("Content-Type", "application/octet-stream");
  if (state.remoteCsrf) xhr.setRequestHeader("X-Godot-Coder-CSRF", state.remoteCsrf);
  xhr.upload.onprogress = (event) => {
    if (!event.lengthComputable) return;
    const percent = Math.round(event.loaded / event.total * 100);
    box.querySelector("i").style.width = `${percent}%`;
    box.querySelector("strong").textContent = `${percent} % · ${formatBytes(event.loaded)}`;
  };
  xhr.onerror = () => toast("Upload connection interrupted.", "error");
  xhr.onload = async () => {
    let payload = null;
    try { payload = JSON.parse(xhr.responseText || "null"); } catch { payload = xhr.responseText; }
    if (xhr.status < 200 || xhr.status >= 300) {
      return toast(payload?.detail || payload || `Upload failed (${xhr.status}).`, "error");
    }
    box.querySelector("i").style.width = "100%";
    box.querySelector("strong").textContent = `Done · ${formatBytes(payload.size_bytes)}`;
    $("#remote-source-file").value = "";
    setText("#remote-file-name", "No file chosen");
    state.corpus = await api("/api/corpus/status");
    renderCorpus(state.corpus);
    renderRemote(state.remote);
    toast(`${payload.name} is now in the private import folder.`);
  };
  xhr.send(file);
}

async function importRemoteSources() {
  if (!$("#confirm-remote-import-ownership").checked) return toast("First confirm the ownership and usage statement.", "error");
  try {
    const job = await api("/api/jobs/corpus/local-import", { method: "POST", body: JSON.stringify({ confirm_owned: true }) });
    state.currentJob = job;
    renderJob(job);
    setView("corpus");
    toast("Private sources are checked and imported on the PC.");
  } catch (error) { toast(error.message, "error"); }
}
