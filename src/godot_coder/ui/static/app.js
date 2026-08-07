const state = {
  overview: null,
  checkpoints: [],
  configs: [],
  files: [],
  dataCatalog: null,
  dataCatalogRevision: "",
  activeCheckpoint: localStorage.getItem("godot-coder-active-checkpoint") || "",
  currentFile: null,
  currentFileOriginal: "",
  lastGenerated: "",
  lastPrompt: "",
  currentJob: null,
  curriculum: null,
  corpus: null,
  hardwareProbe: null,
  trainingReports: [],
  preflight: null,
  autotune: null,
  modalMode: "new",
  modalContent: "",
  logView: localStorage.getItem("godot-coder-log-view") || "simple",
  logAutoFollow: localStorage.getItem("godot-coder-log-auto-follow") !== "false",
  logClearMarkers: {},
  visibleLogText: "",
  remote: null,
  remoteCsrf: sessionStorage.getItem("godot-coder-remote-csrf") || "",
  deferredInstallPrompt: null,
  corpusFilter: JSON.parse(localStorage.getItem("godot-coder-corpus-filter") || '{"search":"","enabled":"all"}'),
  advancedSourcesVisible: localStorage.getItem("godot-coder-advanced-sources") !== "hidden",
};

const viewOrder = ["chat", "training", "corpus", "data", "models", "remote", "system"];

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const viewTitles = {
  chat: "Chat & Code",
  training: "Training Workspace",
  corpus: "Geführter Wissensaufbau",
  data: "Data Lab",
  models: "Checkpoint Vault",
  remote: "Secure Remote Studio",
  system: "System & Runtime",
};

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

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatNumber(value) {
  return new Intl.NumberFormat("de-DE").format(value || 0);
}

function formatDate(timestamp) {
  if (!timestamp) return "–";
  return new Intl.DateTimeFormat("de-DE", { dateStyle: "short", timeStyle: "short" }).format(new Date(timestamp * 1000));
}

function toast(message, type = "info", duration = 4200) {
  const item = document.createElement("div");
  item.className = `toast ${type}`;
  item.textContent = message;
  $("#toast-region").append(item);
  setTimeout(() => item.remove(), duration);
}

function setView(name) {
  $$(".nav-item").forEach((button) => button.classList.toggle("active", button.dataset.view === name));
  $$(".view").forEach((view) => view.classList.toggle("active", view.id === `view-${name}`));
  $("#view-title").textContent = viewTitles[name] || name;
  if (name === "models") renderModels();
  if (name === "data") refreshDataCatalog(false);
  if (name === "remote") renderRemote(state.remote);
  if (name === "system") renderSystem();
}

async function refreshAll(showToast = false) {
  try {
    // Consolidated: /api/overview now bundles checkpoints + configs + manifest + training reports
    const [overview, dataCatalog, job, curriculum, corpus, preflight, autotune] = await Promise.all([
      api("/api/overview"),
      api("/api/data/catalog"),
      api("/api/jobs/current"),
      api("/api/curriculum/status"),
      api("/api/corpus/status"),
      api("/api/preflight"),
      api("/api/hardware/autotune"),
    ]);
    state.overview = overview;
    state.checkpoints = overview.checkpoints || [];
    state.configs = overview.configs || [];
    state.hardwareProbe = overview.vram_probe || null;
    state.trainingReports = overview.latest_training_reports || [];
    const manifest = overview.manifest || null;
    state.dataCatalog = dataCatalog;
    state.dataCatalogRevision = dataCatalog?.revision || "";
    state.files = dataCatalog?.entries || [];
    state.currentJob = job;
    state.curriculum = curriculum;
    state.corpus = corpus;
    state.preflight = preflight;
    state.autotune = autotune;
    renderRuntime();
    renderCheckpointSelects();
    renderConfigs();
    renderTrainingProfiles();
    renderTrainingReports();
    renderPreflight(preflight);
    renderFiles();
    renderDataCatalogSummary();
    renderManifest(manifest);
    renderCurriculum(curriculum);
    renderCorpus(corpus);
    renderModels();
    renderSystem();
    renderJob(job);
    await refreshRemote();
    if (showToast) toast("Studio-Daten aktualisiert.");
  } catch (error) {
    toast(`Aktualisierung fehlgeschlagen: ${error.message}`, "error");
  }
}

function renderRuntime() {
  const o = state.overview;
  if (!o) return;
  const dot = $("#runtime-dot");
  dot.classList.toggle("online", Boolean(o.cuda_available && o.godot));
  $("#runtime-label").textContent = o.cuda_available ? "CUDA bereit" : "CPU-Modus";
  $("#runtime-gpu").textContent = o.gpu?.name || "Keine CUDA-GPU";
  $("#runtime-vram").textContent = o.gpu ? `${o.gpu.vram_gib} GiB VRAM` : "CPU";
  $("#runtime-meter-fill").style.width = o.cuda_available ? "100%" : "28%";
  const brandVersion = $("#brand-version");
  if (brandVersion && o.app_version) brandVersion.textContent = `v${o.app_version}`;
}

function bestDefaultCheckpoint() {
  if (!state.checkpoints.length) return "";
  const stored = state.checkpoints.find((item) => item.path === state.activeCheckpoint);
  if (stored) return stored.path;
  const best = state.checkpoints.find((item) => item.kind === "best")
    || state.checkpoints.find((item) => item.kind === "latest")
    || state.checkpoints[0];
  return best.path;
}

function renderCheckpointSelects() {
  state.activeCheckpoint = bestDefaultCheckpoint();
  if (state.activeCheckpoint) localStorage.setItem("godot-coder-active-checkpoint", state.activeCheckpoint);
  const chatSelect = $("#chat-checkpoint");
  const resumeSelect = $("#training-resume");
  const options = state.checkpoints.map((item) => {
    const label = `${item.run} · ${item.name}${item.step ? ` · step ${item.step}` : ""}`;
    return `<option value="${escapeHtml(item.path)}">${escapeHtml(label)}</option>`;
  }).join("");
  chatSelect.innerHTML = options || '<option value="">Kein Checkpoint vorhanden</option>';
  chatSelect.value = state.activeCheckpoint;
  resumeSelect.innerHTML = '<option value="">Von Zufallsgewichten starten</option>' + options;
  updateActiveModelSummary();
  $("#generate-button").disabled = !state.activeCheckpoint;
}

function updateActiveModelSummary() {
  const item = state.checkpoints.find((checkpoint) => checkpoint.path === state.activeCheckpoint);
  $("#active-model-summary").textContent = item
    ? `${item.run} · ${item.kind.toUpperCase()} · ${item.size_mb} MB · ${formatDate(item.modified_at)}`
    : "Trainiere zuerst ein Modell oder kopiere deine Checkpoints in checkpoints/.";
}

function renderConfigs() {
  const select = $("#training-config");
  const prior = select.value;
  const generated = state.configs.filter((config) => config.profile_generated);
  const profiles = state.configs.filter((config) => config.profile_id && !config.profile_generated);
  const legacy = state.configs.filter((config) => !config.profile_id);
  select.innerHTML = `${generated.length ? `<optgroup label="Hardware-Empfehlung">${generated.map((config) => `<option value="${escapeHtml(config.path)}">${escapeHtml(config.profile_title || config.name)}</option>`).join("")}</optgroup>` : ""}${profiles.length ? `<optgroup label="v0.6 · Professional Profiles">${profiles.map((config) => `<option value="${escapeHtml(config.path)}">${escapeHtml(config.profile_title || config.name)}</option>`).join("")}</optgroup>` : ""}${legacy.length ? `<optgroup label="Lern- und Legacy-Konfigurationen">${legacy.map((config) => `<option value="${escapeHtml(config.path)}">${escapeHtml(config.name)}</option>`).join("")}</optgroup>` : ""}`;
  const recommendedPath = state.autotune?.recommendation?.config;
  if (state.configs.some((item) => item.path === prior)) select.value = prior;
  else if (recommendedPath && state.configs.some((item) => item.path === recommendedPath)) select.value = recommendedPath;
  else if (state.configs.some((item) => item.name === "corpus_starter_30m")) select.value = state.configs.find((item) => item.name === "corpus_starter_30m").path;
  else if (state.configs.some((item) => item.name === "tiny_demo")) select.value = state.configs.find((item) => item.name === "tiny_demo").path;
  updateConfigMetrics();
}

function updateConfigMetrics() {
  const config = state.configs.find((item) => item.path === $("#training-config").value);
  if (!config) return;
  const parameterText = config.parameters ? `${(config.parameters / 1e6).toFixed(1)}M` : "?";
  $("#metric-profile").textContent = `${parameterText} · ${config.n_layers || "?"}L`;
  $("#metric-context").textContent = `${formatNumber(config.max_seq_len)} Tokens`;
  const stepsLabel = $("#metric-steps").closest(".metric-card")?.querySelector("span");
  if (stepsLabel) stepsLabel.textContent = "Trainingsbudget";
  $("#metric-steps").textContent = config.max_steps ? `${formatNumber(config.max_steps)} Schritte` : config.target_dataset_passes ? `${config.target_dataset_passes} Datensatzdurchläufe` : config.max_tokens ? `${formatCompact(config.max_tokens)} Tokens` : "automatisch";
  $("#metric-batch").textContent = config.tokens_per_optimizer_step ? `${formatNumber(config.tokens_per_optimizer_step)} Tok/Step` : "–";
  applyJobMetricOverride(state.currentJob);
  renderTrainingProfiles();
}

function formatCompact(value) {
  const number = Number(value || 0);
  if (number >= 1e9) return `${(number / 1e9).toFixed(2)}B`;
  if (number >= 1e6) return `${(number / 1e6).toFixed(1)}M`;
  if (number >= 1e3) return `${(number / 1e3).toFixed(1)}K`;
  return formatNumber(number);
}

const workflowJobLabels = {
  "corpus-audit": "Daten-Audit",
  "local-source-import": "Private Projekte prüfen",
  "remote-source-download": "Remote-Quelle herunterladen",
  "hardware-autotune": "Hardware-Autotuner",
  "training-smoke-50": "50-Schritte-Probelauf",
  "training": "Training",
};

function friendlyJobLabel(kind) {
  return workflowJobLabels[kind] || String(kind || "Studio-Aufgabe").replaceAll("-", " ");
}

const ProgressTools = window.GodotCoderProgress;

const projectStatusLabels = {
  waiting: "wartet",
  running: "läuft",
  passed: "bestanden",
  passed_with_warnings: "mit Warnungen bestanden",
  failed: "fehlgeschlagen",
  quarantined: "quarantänisiert",
  disabled: "deaktiviert",
  skipped: "übersprungen",
  completed: "abgeschlossen",
  stopped: "abgebrochen",
};

function projectStatusLabel(value) {
  return projectStatusLabels[value] || value || "wartet";
}

function setText(selector, value, title = null) {
  const element = $(selector);
  if (!element) return;
  element.textContent = value == null ? "–" : String(value);
  if (title != null) element.title = String(title);
}

function renderLocalProjectCards(liveProjects = []) {
  const grid = $("#local-project-grid");
  if (!grid) return;
  const reportProjects = state.corpus?.local_sources?.report?.projects || [];
  const reportByName = new Map(reportProjects.map((project) => [project.project_name, project]));
  const liveByName = new Map((liveProjects || []).map((project) => [project.project_name, project]));
  const names = [...new Set([...reportByName.keys(), ...liveByName.keys()])];
  const projects = names.map((name) => ({ ...(reportByName.get(name) || {}), ...(liveByName.get(name) || {}) }));
  if (!projects.length) {
    grid.innerHTML = '<div class="empty-state compact">Noch keine privaten Projekte importiert.</div>';
    return;
  }
  grid.innerHTML = projects.map((project) => {
    let status = project.project_status;
    if (!status) {
      if (project.enabled_for_training) status = project.static_warnings?.length ? "passed_with_warnings" : "passed";
      else if (project.validation_status === "failed") status = "failed";
      else if (project.validation_status === "not_run") status = "disabled";
      else status = "quarantined";
    }
    const scripts = project.scripts_found ?? project.gd_files ?? 0;
    const checked = project.file_index ?? (project.validation_status ? scripts : 0);
    const trainable = project.trainable_scripts ?? project.trainable_gd_files ?? 0;
    const warnings = project.warnings ?? project.static_warnings?.length ?? 0;
    const failed = project.failed ?? (project.validation_status === "failed" ? 1 : 0);
    const quarantined = project.quarantined ?? (!project.enabled_for_training ? scripts : 0);
    const phases = project.phases || [];
    const phaseRows = phases.length ? phases.map((phase) => `
      <li class="phase-${escapeHtml(phase.phase_status || "waiting")}"><span>${escapeHtml(ProgressTools.phaseLabel(phase.phase))}</span><strong>${escapeHtml(projectStatusLabel(phase.phase_status))}</strong></li>
    `).join("") : `<li><span>Projekt erkannt</span><strong>${escapeHtml(project.validation_status || "wartet")}</strong></li>`;
    return `<details class="local-project-card status-${escapeHtml(status)}" ${status === "running" ? "open" : ""}>
      <summary>
        <span class="project-card-copy"><strong title="${escapeHtml(project.project_name || "Projekt")}">${escapeHtml(project.project_name || "Projekt")}</strong><small>${formatNumber(checked)}/${formatNumber(scripts)} Skripte geprüft</small></span>
        <span class="project-status-badge">${escapeHtml(projectStatusLabel(status))}</span>
      </summary>
      <div class="project-card-details">
        <ul class="project-phase-list">${phaseRows}</ul>
        <dl class="project-card-stats">
          <div><dt>Skripte gefunden</dt><dd>${formatNumber(scripts)}</dd></div>
          <div><dt>Trainingsfähig</dt><dd>${formatNumber(trainable)}</dd></div>
          <div><dt>Warnungen</dt><dd>${formatNumber(warnings)}</dd></div>
          <div><dt>Fehler</dt><dd>${formatNumber(failed)}</dd></div>
          <div><dt>Quarantäne</dt><dd>${formatNumber(quarantined)}</dd></div>
          <div><dt>Add-ons ausgeschlossen</dt><dd>${formatNumber(project.addon_files || 0)}</dd></div>
          <div><dt>Cache/Import ausgeschlossen</dt><dd>${formatNumber(project.generated_files || 0)}</dd></div>
          <div><dt>Import aktiviert</dt><dd>${project.enabled_for_training ? "Ja" : "Nein"}</dd></div>
        </dl>
      </div>
    </details>`;
  }).join("");
}

function renderLocalProgress(job) {
  const dashboard = $("#local-progress-dashboard");
  if (!dashboard) return;
  const isLocalJob = job?.kind === "local-source-import";
  const progress = job?.progress_state || {};
  const hasProgress = isLocalJob && (progress.project_total || progress.phase || (progress.projects || []).length);
  dashboard.hidden = !hasProgress;
  if (!hasProgress) {
    renderLocalProjectCards([]);
    return;
  }
  const percent = Math.max(0, Math.min(100, Math.round(Number(job.progress ?? progress.overall_progress ?? 0) * 100)));
  const index = progress.project_index || 0;
  const total = progress.project_total || 0;
  setText("#local-progress-title", `${index}/${total} Projekte · ${percent} %`);
  setText("#local-progress-percent", `${percent} %`);
  $("#local-progress-bar").style.width = `${percent}%`;
  setText("#local-current-project", progress.project_name || (job.status === "completed" ? "Import abgeschlossen" : "Planung läuft"), progress.project_name || "");
  setText("#local-current-phase", ProgressTools.phaseLabel(progress.phase));
  const currentFile = progress.current_file || progress.message || "–";
  setText("#local-current-file", ProgressTools.shortenPath(currentFile), currentFile);
  const fileIndex = progress.file_index || 0;
  const fileTotal = progress.file_total || progress.scripts_found || 0;
  setText("#local-file-progress", `${formatNumber(fileIndex)}/${formatNumber(fileTotal)} · ${formatNumber(Math.max(0, fileTotal - fileIndex))} verbleibend`);
  setText("#local-passed", formatNumber(progress.passed || 0));
  setText("#local-warnings", formatNumber(progress.warnings || 0));
  setText("#local-failed", formatNumber(progress.failed || 0));
  setText("#local-quarantined", formatNumber(progress.quarantined || 0));
  setText("#local-addons", formatNumber(progress.addon_files || 0));
  setText("#local-generated", formatNumber(progress.generated_files || 0));
  setText("#local-elapsed", ProgressTools.formatDuration(job.elapsed_seconds ?? progress.elapsed_seconds));
  setText("#local-eta", job.status === "completed" ? "abgeschlossen" : ProgressTools.formatEta(progress));
  setText("#local-next-project", progress.next_project || (job.status === "completed" ? "Kein weiteres Projekt" : "wird ermittelt"), progress.next_project || "");
  setText("#local-next-detail", progress.next_project ? `${formatNumber(progress.next_project_scripts || 0)} erkannte Skripte` : (progress.next_phase ? ProgressTools.phaseLabel(progress.next_phase) : "–"));
  renderLocalProjectCards(progress.projects || []);
}

function updateLogFilterOptions(entries) {
  const projectSelect = $("#log-filter-project");
  const phaseSelect = $("#log-filter-phase");
  if (!projectSelect || !phaseSelect) return;
  const priorProject = projectSelect.value;
  const priorPhase = phaseSelect.value;
  const projects = [...new Set(entries.map((entry) => entry.project).filter(Boolean))].sort();
  const phases = [...new Set(entries.map((entry) => entry.phase).filter(Boolean))].sort();
  projectSelect.innerHTML = '<option value="">Alle Projekte</option>' + projects.map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`).join("");
  phaseSelect.innerHTML = '<option value="">Alle Phasen</option>' + phases.map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(ProgressTools.phaseLabel(value))}</option>`).join("");
  if (projects.includes(priorProject)) projectSelect.value = priorProject;
  if (phases.includes(priorPhase)) phaseSelect.value = priorPhase;
}

function renderLog(job) {
  const terminal = $("#training-log");
  if (!terminal) return;
  const distanceBefore = terminal.scrollHeight - terminal.scrollTop - terminal.clientHeight;
  if (!job) {
    terminal.textContent = "Studio bereit.";
    state.visibleLogText = terminal.textContent;
    return;
  }
  const allEntries = ProgressTools.normalizeLogEntries(job, state.logView);
  updateLogFilterOptions(allEntries);
  const markerKey = `${job.id}:${state.logView}`;
  const startIndex = Math.min(allEntries.length, state.logClearMarkers[markerKey] || 0);
  const entries = ProgressTools.filterLogEntries(allEntries.slice(startIndex), {
    levels: {
      info: $("#log-level-info")?.checked !== false,
      warning: $("#log-level-warning")?.checked !== false,
      error: $("#log-level-error")?.checked !== false,
    },
    project: $("#log-filter-project")?.value || "",
    phase: $("#log-filter-phase")?.value || "",
  });
  const lines = entries.map((entry) => {
    const timestamp = entry.timestamp ? (state.logView === "technical" ? entry.timestamp : new Date(entry.timestamp).toLocaleTimeString("de-DE")) : "";
    const context = [entry.project, entry.phase ? ProgressTools.phaseLabel(entry.phase) : ""].filter(Boolean).join(" · ");
    const prefix = [timestamp, entry.level?.toUpperCase(), context].filter(Boolean).map((value) => `[${value}]`).join(" ");
    return `${prefix}${prefix ? " " : ""}${entry.text}`;
  });
  terminal.textContent = lines.join("\n") || "Keine sichtbaren Logeinträge für die aktuellen Filter.";
  state.visibleLogText = terminal.textContent;
  if (state.logAutoFollow && distanceBefore <= 32) terminal.scrollTop = terminal.scrollHeight;
}

function setLogView(mode) {
  state.logView = mode === "technical" ? "technical" : "simple";
  localStorage.setItem("godot-coder-log-view", state.logView);
  $("#log-view-simple").className = `${state.logView === "simple" ? "secondary-button active" : "ghost-button"} compact`;
  $("#log-view-technical").className = `${state.logView === "technical" ? "secondary-button active" : "ghost-button"} compact`;
  $("#log-view-simple").setAttribute("aria-selected", state.logView === "simple" ? "true" : "false");
  $("#log-view-technical").setAttribute("aria-selected", state.logView === "technical" ? "true" : "false");
  renderLog(state.currentJob);
}

function clearVisibleLog() {
  const job = state.currentJob;
  if (!job) return;
  const entries = ProgressTools.normalizeLogEntries(job, state.logView);
  state.logClearMarkers[`${job.id}:${state.logView}`] = entries.length;
  renderLog(job);
  toast("Nur die aktuelle Loganzeige wurde geleert. Gespeicherte Reports bleiben erhalten.");
}

function exportJobLog(format) {
  const job = state.currentJob;
  if (!job) return toast("Kein Joblog vorhanden.", "error");
  const link = document.createElement("a");
  link.href = `/api/jobs/${encodeURIComponent(job.id)}/export?format=${encodeURIComponent(format)}`;
  link.download = "";
  document.body.append(link);
  link.click();
  link.remove();
}

function parseJobJson(job, prefix) {
  const lines = job?.logs || [];
  for (let index = lines.length - 1; index >= 0; index -= 1) {
    const line = lines[index];
    if (!line.startsWith(prefix)) continue;
    try { return JSON.parse(line.slice(prefix.length)); } catch { return null; }
  }
  return null;
}

function smokeReportAvailable() {
  return (state.trainingReports || []).some((report) => report?.status === "completed" && Number(report?.max_steps) === 50);
}

function setWorkflowStep(name, { completed = false, active = false, label = "Offen" } = {}) {
  const button = $(`[data-workflow-step="${name}"]`);
  const stateLabel = $(`#professional-${name}-state`);
  if (!button || !stateLabel) return;
  button.classList.toggle("completed", completed);
  button.classList.toggle("active", active);
  stateLabel.textContent = label;
}

function renderWorkflowSteps(job = state.currentJob) {
  const active = job && ["starting", "running", "stopping"].includes(job.status);
  const activeKind = active ? job.kind : null;
  const auditReady = Boolean(state.corpus?.audit);
  const autotuneReady = Boolean(state.autotune?.recommendation);
  const smokeReady = smokeReportAvailable() || (job?.kind === "training-smoke-50" && job?.status === "completed");
  const preflightStatus = state.preflight?.status;
  setWorkflowStep("audit", { completed: auditReady, active: activeKind === "corpus-audit", label: activeKind === "corpus-audit" ? "Läuft" : auditReady ? "Erledigt" : "Offen" });
  setWorkflowStep("autotune", { completed: autotuneReady, active: activeKind === "hardware-autotune", label: activeKind === "hardware-autotune" ? "Läuft" : autotuneReady ? "Erledigt" : "Offen" });
  setWorkflowStep("smoke", { completed: smokeReady, active: activeKind === "training-smoke-50", label: activeKind === "training-smoke-50" ? "Läuft" : smokeReady ? "Bestanden" : "Offen" });
  setWorkflowStep("preflight", { completed: preflightStatus === "green", active: false, label: preflightStatus === "green" ? "Freigegeben" : preflightStatus === "yellow" ? "Warnung" : preflightStatus === "red" ? "Blockiert" : "Offen" });

  // Auto-advance hint: show what to do next when the current step completes.
  // Never auto-start training — the user must click "Training starten" explicitly.
  const canStartTraining = preflightStatus === "green" || preflightStatus === "yellow";
  $("#start-training").disabled = !canStartTraining;
  if (!active && !canStartTraining) {
    const nextStep = !auditReady ? "audit" : !autotuneReady ? "autotune" : !smokeReady ? "smoke" : "preflight";
    const names = { audit: "Daten prüfen", autotune: "Hardware einstellen", smoke: "50-Schritte-Probelauf", preflight: "Vorprüfung" };
    $("#start-training").title = `Zuerst: ${names[nextStep] || "Vorprüfung abschließen"}`;
  } else if (canStartTraining) {
    $("#start-training").title = "Training mit dieser Konfiguration starten";
  }
}

function applyJobMetricOverride(job) {
  if (!job || job.kind !== "training-smoke-50") return;
  const stepsLabel = $("#metric-steps").closest(".metric-card")?.querySelector("span");
  if (stepsLabel) stepsLabel.textContent = "Probelauf";
  const summary = parseJobJson(job, "TRAINING_SUMMARY_JSON=");
  const runHeader = parseJobJson(job, "RUN_HEADER_JSON=");
  const steps = Number(summary?.run_steps_completed || job.max_steps || 50);
  const passes = Number(summary?.equivalent_dataset_passes_seen ?? runHeader?.token_accounting?.equivalent_dataset_passes_planned);
  $("#metric-steps").textContent = Number.isFinite(passes) ? `${formatNumber(steps)} Schritte · ${passes.toFixed(2)}× Daten` : `${formatNumber(steps)} Smoke-Schritte`;
}

function renderProfessionalRun(job) {
  const box = $("#professional-run");
  if (!box) return;
  const relevant = job && ["corpus-audit", "hardware-autotune", "training-smoke-50", "training"].includes(job.kind);
  if (!relevant) { box.hidden = true; return; }
  box.hidden = false;
  box.classList.toggle("completed", job.status === "completed");
  box.classList.toggle("failed", job.status === "failed" || job.status === "stopped");
  const percent = job.max_steps ? Math.round((job.progress || 0) * 100) : job.status === "completed" ? 100 : 0;
  $("#professional-run-progress").style.width = `${percent}%`;
  $("#professional-run-percent").textContent = job.max_steps || job.status === "completed" ? `${percent} %` : job.status;
  $("#professional-run-title").textContent = `${friendlyJobLabel(job.kind)} · ${job.status === "completed" ? "abgeschlossen" : job.status === "failed" ? "fehlgeschlagen" : job.status === "stopped" ? "gestoppt" : "läuft"}`;
  const summary = parseJobJson(job, "TRAINING_SUMMARY_JSON=");
  if (summary) {
    const speed = Math.round(summary.average_training_tokens_per_second || summary.average_tokens_per_second || 0);
    const peak = Number(summary.peak_vram_reserved_gib || 0).toFixed(2);
    const val = summary.final_val_loss == null ? "–" : Number(summary.final_val_loss).toFixed(4);
    $("#professional-run-detail").textContent = `${formatNumber(summary.run_steps_completed || 0)} Schritte · ${formatNumber(speed)} Tok/s · ${peak} GiB Peak · Val ${val}`;
  } else {
    $("#professional-run-detail").textContent = job.step && job.max_steps ? `Schritt ${formatNumber(job.step)} von ${formatNumber(job.max_steps)}` : "Status wird live aktualisiert.";
  }
}

function profileProbeEntry(profileId) {
  return state.hardwareProbe?.profiles?.find((item) => item.profile_id === profileId) || null;
}

function renderTrainingProfiles() {
  const target = $("#training-profile-grid");
  if (!target) return;
  const profiles = state.configs.filter((item) => item.profile_id && !item.profile_generated);
  const selectedPath = $("#training-config")?.value;
  if (!profiles.length) {
    target.innerHTML = '<div class="empty-state">Noch keine Hauptprofile gefunden.</div>';
    return;
  }
  target.innerHTML = profiles.map((config) => {
    const probe = profileProbeEntry(config.profile_id);
    const result = probe?.configured_result;
    const statusClass = result?.status === "pass" ? "passed" : result ? "failed" : "";
    const probeCopy = result?.status === "pass"
      ? `<div class="profile-probe-result pass">✓ Probelauf bestanden · ${result.peak_reserved_gib ?? "?"} GiB Peak · ${formatNumber(Math.round(result.tokens_per_second || 0))} Tok/s</div>`
      : result
        ? `<div class="profile-probe-result fail">× ${escapeHtml(result.status)} · ${escapeHtml(result.error || "Profil passte nicht")}</div>`
        : '<div class="profile-probe-result">Noch nicht auf dieser GPU gemessen.</div>';
    return `<article class="profile-card ${config.path === selectedPath ? "selected" : ""} ${config.profile_recommended ? "recommended" : ""} ${statusClass}" data-profile-config="${escapeHtml(config.path)}">
      <div class="profile-title">${escapeHtml(config.profile_title || config.name)}</div>
      <div class="profile-method">${escapeHtml(config.profile_method || "Training")}</div>
      <div class="profile-description">${escapeHtml(config.profile_description || "")}</div>
      <div class="profile-stats">
        <div><span>Parameter</span><strong>${formatCompact(config.parameters)}</strong></div>
        <div><span>Kontext</span><strong>${formatNumber(config.max_seq_len)}</strong></div>
        <div><span>AMP-Compute</span><strong>${escapeHtml(config.dtype)}</strong></div>
        <div><span>Speichertechnik</span><strong>${config.gradient_checkpointing ? "Checkpointing" : "Direkt"}</strong></div>
      </div>${probeCopy}
    </article>`;
  }).join("");
  $$('[data-profile-config]').forEach((card) => card.addEventListener("click", () => {
    $("#training-config").value = card.dataset.profileConfig;
    updateConfigMetrics();
  }));

  const summary = $("#probe-summary");
  const tuned = state.autotune?.recommendation;
  if (tuned) {
    const speed = formatNumber(Math.round(tuned.tokens_per_second || 0));
    const peak = Number(tuned.peak_reserved_gib || 0).toFixed(2);
    summary.innerHTML = `<strong>Lokale Hardware-Empfehlung: ${escapeHtml(tuned.matrix_label || "Autotuned")}</strong><br>Batch ${formatNumber(tuned.batch_size || 0)} · Kontext ${formatNumber(tuned.context || 0)} · ${peak} GiB Peak · ${speed} Tok/s. Die drei Karten darüber bleiben Kapazitätsvergleiche.`;
    return;
  }
  if (!state.hardwareProbe) {
    summary.textContent = "Noch kein Hardware-Probelauf vorhanden. Er misst alle drei Profile mit echten Forward-, Backward- und Optimizer-Schritten.";
    return;
  }
  const recommendation = state.hardwareProbe.recommendation || {};
  const created = formatDate(state.hardwareProbe.created_at);
  summary.innerHTML = recommendation.profile_id
    ? `<strong>Kapazitätsempfehlung: ${escapeHtml(recommendation.profile_title || recommendation.profile_id)}</strong><br>${escapeHtml(recommendation.reason || "")} · Messung ${created}`
    : `<strong>Kein Profil sicher empfohlen.</strong><br>${escapeHtml(recommendation.reason || "Prüfe den Live-Log.")} · Messung ${created}`;
}

function renderTrainingReports() {
  const target = $("#training-report-list");
  if (!target) return;
  const reports = state.trainingReports || [];
  if (!reports.length) {
    target.innerHTML = '<div class="empty-state">Noch kein Trainingsbericht vorhanden. Der nächste Lauf schreibt automatisch einen Abschlussbericht.</div>';
    return;
  }
  target.innerHTML = reports.slice(0, 6).map((report) => {
    const profile = report.profile?.title || report.run_id || "Training";
    const bestLoss = report.best_val_loss == null ? "–" : Number(report.best_val_loss).toFixed(4);
    const peak = report.peak_vram_reserved_gib == null ? "CPU" : `${Number(report.peak_vram_reserved_gib).toFixed(2)} GiB`;
    return `<article class="training-report-card ${report.status === "failed" ? "failed" : ""}">
      <div class="report-name"><span>Lauf</span><strong>${escapeHtml(profile)}</strong></div>
      <div><span>Status</span><strong>${escapeHtml(report.status || "?")}</strong></div>
      <div><span>Tokens gesehen</span><strong>${formatCompact(report.cumulative_tokens_seen)}</strong></div>
      <div><span>Ø Durchsatz</span><strong>${formatNumber(Math.round(report.average_training_tokens_per_second || report.average_tokens_per_second || 0))} Tok/s</strong></div>
      <div><span>Best Val</span><strong>${bestLoss}</strong></div>
      <div><span>Peak VRAM</span><strong>${peak}</strong></div>
    </article>`;
  }).join("");
}

function renderPreflight(report) {
  const light = $("#preflight-light");
  if (!light) return;
  const status = report?.status || "red";
  light.dataset.status = status;
  light.querySelector("strong").textContent = status === "green" ? "Bereit" : status === "yellow" ? "Mit Warnungen" : "Blockiert";
  const blockers = report?.blockers || [];
  const warnings = report?.warnings || [];
  const modeLabel = report?.mode === "smoke" ? "Smoke-Test" : "Langtraining";
  $("#preflight-summary").innerHTML = blockers.length
    ? `<strong>${escapeHtml(modeLabel)} blockiert · ${blockers.length} Grund/Gründe:</strong> ${blockers.map(escapeHtml).join(" · ")}`
    : warnings.length
      ? `<strong>${escapeHtml(modeLabel)} technisch möglich, aber prüfen:</strong> ${warnings.map(escapeHtml).join(" · ")}`
      : report ? `<strong>${escapeHtml(modeLabel)} freigegeben.</strong> Alle Pflichtprüfungen sind aktuell.` : "Noch keine vollständige Vorprüfung vorhanden.";
  const audit = report?.audit || {};
  const validation = report?.validation || {};
  const data = report?.dataset || {};
  const freshness = report?.freshness || {};
  const plan = report?.training_plan || {};
  const hardware = report?.hardware_recommendation || {};
  $("#preflight-details").innerHTML = `
    Modus / Profil: ${escapeHtml(modeLabel)} / ${escapeHtml(report?.profile_id || "–")}<br>
    Projektvalidierung: ${formatNumber(validation.prepared || 0)} vorbereitet · ${formatNumber(validation.failed || 0)} echte Fehler · ${formatNumber(validation.context_warnings || 0)} Kontextwarnungen<br>
    Akzeptierte Dateien: ${formatNumber((audit.accepted || 0) + (audit.warning || 0))}<br>
    Projekte Train/Val/Test: ${formatNumber(audit.train_projects || 0)} / ${formatNumber(audit.val_projects || 0)} / ${formatNumber(audit.test_projects || 0)}<br>
    Parserquote: ${audit.parser_pass_rate == null ? "–" : Math.round(audit.parser_pass_rate * 100) + " %"}<br>
    Trainingstokens: ${formatNumber(data.train_tokens || 0)} / empfohlen mindestens ${formatNumber(plan.minimum_recommended_tokens || 0)}<br>
    Tokenstream: ${freshness.stale ? "VERALTET" : data.manifest_path ? "aktuell" : "fehlt"}${freshness.newest_input ? ` · neueste Eingabe ${escapeHtml(freshness.newest_input)}` : ""}<br>
    Geplante Durchläufe: ${plan.dataset_passes ?? "–"}<br>
    Hardwareprofil: ${escapeHtml(hardware.matrix_label || hardware.profile_title || "noch nicht gewählt")}
  `;
  renderWorkflowSteps(state.currentJob);
}


function renderManifest(manifest) {
  const target = $("#manifest-mini");
  if (!manifest) {
    target.textContent = "Noch kein verarbeiteter Datensatz vorhanden.";
    return;
  }
  target.innerHTML = `
    <strong>${formatNumber(manifest.train_tokens)} Train</strong> + ${formatNumber(manifest.val_tokens)} Validation Tokens<br>
    ${formatNumber(manifest.train_files?.length)} Trainingsdateien · ${formatNumber(manifest.val_files?.length)} Validierungsdateien<br>
    Vokabular: ${formatNumber(manifest.vocab_size)} Tokens
  `;
}


function renderCurriculum(status) {
  const summary = $("#curriculum-summary");
  const list = $("#curriculum-list");
  const validation = $("#curriculum-validation");
  if (!status?.manifest) {
    summary.textContent = "Lehrplan noch nicht erzeugt. Schritt 1 legt 192 kontrollierte Lektionen an.";
    list.innerHTML = "";
    validation.textContent = "Godot-Prüfung steht aus.";
    return;
  }
  const manifest = status.manifest;
  const splits = manifest.split_counts || {};
  summary.innerHTML = `<strong>${formatNumber(manifest.total_lessons)} Lektionen</strong><br>${formatNumber(splits.train)} Train · ${formatNumber(splits.val)} Validation · ${formatNumber(splits.test)} Test`;
  const topicCounts = manifest.topic_counts || {};
  const maxCount = Math.max(1, ...Object.values(topicCounts));
  list.innerHTML = (manifest.topics || []).map((topic) => {
    const count = topicCounts[topic.slug] || 0;
    const percent = Math.round((count / maxCount) * 100);
    return `<div><span>${escapeHtml(topic.label)} · ${formatNumber(count)}</span><i style="--p: ${percent}%"></i></div>`;
  }).join("");
  if (status.validation) {
    const rate = Math.round((status.validation.pass_rate || 0) * 100);
    validation.textContent = `Godot: ${formatNumber(status.validation.passed)}/${formatNumber(status.validation.total)} bestanden · ${rate} %`;
  } else {
    validation.textContent = "Godot-Prüfung steht aus.";
  }
}



function renderLocalSources(local) {
  const statusElement = $("#local-source-status");
  const grid = $("#local-project-grid");
  if (!statusElement || !grid) return;
  const items = local?.inbox_items || [];
  const report = local?.report;
  const summary = report?.summary;
  statusElement.innerHTML = `<strong>${items.length} Datei(en)/Ordner im Importordner</strong><span>${escapeHtml(local?.inbox || "data/local_sources/inbox")}</span>${summary ? `<small>${summary.projects} Projekte geprüft · ${summary.enabled} aktiviert · ${summary.quarantined} quarantänisiert · ${summary.failed || 0} fehlgeschlagen · ~${formatNumber(summary.estimated_bpe_tokens)} Tokens</small>` : ""}`;
  renderLocalProjectCards(state.currentJob?.kind === "local-source-import" ? (state.currentJob.progress_state?.projects || []) : []);
}

async function openLocalSourceInbox() {
  try {
    const result = await api("/api/corpus/local/open", { method: "POST", body: "{}" });
    toast(`Importordner geöffnet: ${result.path}`);
  } catch (error) { toast(error.message, "error"); }
}

async function importLocalSources() {
  const confirmed = Boolean($("#confirm-local-ownership")?.checked);
  if (!confirmed) return toast("Bestätige zuerst, dass du den Quellcode verwenden darfst.", "error");
  try {
    const job = await api("/api/jobs/corpus/local-import", { method: "POST", body: JSON.stringify({ confirm_owned: true }) });
    state.currentJob = job;
    renderJob(job);
    toast("Private Projekte werden sicher geprüft und importiert.");
  } catch (error) { toast(error.message, "error"); }
}

function corpusEnabledSources() {
  return (state.corpus?.registry?.sources || []).filter((source) => source.enabled);
}

function renderCorpus(status) {
  if (!$("#corpus-source-grid")) return;
  const allSources = status?.registry?.sources || [];
  renderLocalSources(status?.local_sources);
  const downloads = new Map((status?.downloads || []).map((item) => [item.id, item]));
  if (!allSources.length) {
    $("#corpus-source-grid").innerHTML = '<div class="empty-state">Keine Quellen konfiguriert.</div>';
  } else {
  $("#corpus-source-grid").innerHTML = allSources.map((source) => {
    const download = downloads.get(source.id);
    const ref = source.ref || source.branch || "main";
    const localSize = download?.size_mb == null ? "lokal" : `${download.size_mb} MB lokal`;
    const downloadState = download?.needs_refresh ? "Ref geändert – erneut laden" : download?.downloaded ? localSize : "noch nicht geladen";
    const isPrivate = source.catalog_tier === "local-private";
    const tier = source.catalog_tier === "official" ? "Offiziell" : source.catalog_tier === "verified-community" ? "Verifizierte Community" : isPrivate ? "Privat · lokal" : "Eigene Quelle";
    const expansion = source.expansion_tier === "core-5m" ? "5M-Kandidat" : source.expansion_tier === "extended-20m" ? "20M-Erweiterung" : "";
    const estimate = Number(source.estimated_unique_tokens || 0);
    const licenseState = isPrivate
      ? (download?.license_verified ? "✓ Eigentum bestätigt · nicht weitergeben" : "⚠ Eigentumsbestätigung fehlt")
      : download?.downloaded ? (download?.license_verified ? `✓ Lizenz lokal: ${download.license_file || source.license}` : "⚠ Lizenz nicht bestätigt") : `Deklariert: ${source.license}`;
    return `<label class="source-card ${source.enabled ? "enabled" : ""}" data-source-id="${escapeHtml(source.id)}" data-source-title="${escapeHtml(source.title || "")}" data-source-enabled="${source.enabled}">
      <input type="checkbox" data-corpus-source="${escapeHtml(source.id)}" ${source.enabled ? "checked" : ""}>
      <div><div class="source-title">${escapeHtml(source.title)}</div>
      <div class="source-description">${escapeHtml(source.description || "Godot-Datenquelle")}</div>
      <div class="source-meta"><span class="${download?.downloaded && !download?.license_verified ? "license-warning" : "license-ok"}">${escapeHtml(licenseState)}</span><span>${escapeHtml(tier)}</span>${expansion ? `<span>${escapeHtml(expansion)}</span>` : ""}${estimate ? `<span>Schätzung ~${formatNumber(estimate)} Tokens</span>` : ""}<span>${escapeHtml(source.kind === "godot_projects" ? "GDScript-Projekte" : "Dokumentationsbeispiele")}</span><span>Ref: ${escapeHtml(ref)}</span><span>${escapeHtml(downloadState)}</span></div></div>
    </label>`;
  }).join(""); }
  $$('[data-corpus-source]').forEach((box) => box.addEventListener("change", () => box.closest(".source-card").classList.toggle("enabled", box.checked)));

  const enabled = allSources.filter((source) => source.enabled);
  const readyEnabled = enabled.filter((source) => downloads.get(source.id)?.downloaded);
  const missingEnabled = enabled.filter((source) => !downloads.get(source.id)?.downloaded);
  const downloaded = readyEnabled.length > 0;
  const scanned = Boolean(status?.manifest);
  const validated = Boolean(status?.validation && status?.prepared_exists);
  const audited = Boolean(status?.audit);
  const tokenizer = Boolean(status?.tokenizer);
  const processed = Boolean(status?.processed);
  const instructions = Boolean(status?.instructions);
  const flags = [downloaded, scanned, validated, audited, tokenizer, processed, instructions];
  const completed = flags.filter(Boolean).length;
  const percent = Math.round(completed / 7 * 100);
  const labels = ["Quellen lokal", "Korpus gescannt", "Godot geprüft", "Audit abgeschlossen", "BPE bereit", "Tokens vorbereitet", "Aufgabendaten vorbereitet"];
  $("#corpus-readiness").innerHTML = `<strong>${percent} %</strong><span>${completed ? labels[completed - 1] : "Noch nicht begonnen"}</span>`;

  const summary = status?.manifest?.summary || {};
  const validation = status?.validation || {};
  const processedManifest = status?.processed || {};
  $("#corpus-stat-grid").innerHTML = [
    ["Quellen", `${enabled.length}`],
    ["Beispiele", formatNumber(summary.records || 0)],
    ["Godot bestanden", formatNumber(validation.passed || 0)],
    ["Kontextwarnungen", formatNumber(validation.context_warnings || 0)],
    ["Echte Ausschlüsse", formatNumber(validation.failed || 0)],
    ["Train-Tokens", formatNumber(processedManifest.train_tokens || 0)],
    ["Vokabular", formatNumber(status?.tokenizer?.vocab_size || 0)],
    ["Audit akzeptiert", formatNumber((status?.audit?.summary?.accepted || 0) + (status?.audit?.summary?.warning || 0))],
    ["Quarantäne", formatNumber(status?.audit?.summary?.quarantine || 0)],
    ["Aufgaben", formatNumber(status?.instructions?.total_tasks || 0)],
  ].map(([label, value]) => `<div class="corpus-stat"><span>${label}</span><strong>${value}</strong></div>`).join("");

  const next = !enabled.length ? "Aktiviere mindestens eine erlaubte Quelle und speichere die Auswahl."
    : !downloaded ? "Nächster Schritt: Quellen herunterladen. Das kann je nach Internetverbindung dauern."
    : !scanned ? "Nächster Schritt: Quellen scannen und Duplikate entfernen."
    : !validated ? "Nächster Schritt: projektbezogene Godot-Prüfung starten. Nur eindeutig fehlerhafte oder inkompatible Skripte werden hart ausgeschlossen."
    : !audited ? "Nächster Schritt: professionellen Corpus-Audit ausführen."
    : !tokenizer ? "Nächster Schritt: den Code-Tokenizer aus den auditierten Dateien lernen lassen."
    : !processed ? "Nächster Schritt: feste Train-, Validation- und Test-Tokenströme vorbereiten."
    : !instructions ? "Optionaler nächster Schritt: geprüfte Aufgabendaten für die spätere Instruction-Tuning-Phase erzeugen."
    : "Basis- und Aufgabendaten sind vorbereitet. Für einen ernsthaften 91M-Lauf sollte der einzigartige Corpus zunächst mehrere Millionen Tokens erreichen.";
  $("#corpus-next-action").textContent = next;
  const filterBox = $("#corpus-filter-summary");
  if (filterBox) {
    const validationReasons = Object.entries(validation.classifications || {}).filter(([, count]) => Number(count) > 0).sort((a, b) => Number(b[1]) - Number(a[1])).slice(0, 5);
    const auditReasons = Object.entries(status?.audit?.reason_counts || {}).filter(([, count]) => Number(count) > 0).sort((a, b) => Number(b[1]) - Number(a[1])).slice(0, 5);
    const sourceCount = (validation.source_results || []).length;
    filterBox.innerHTML = validationReasons.length || auditReasons.length
      ? `<strong>Transparenz:</strong> ${sourceCount ? `${formatNumber(sourceCount)} Quellen ausgewertet. ` : ""}${validationReasons.length ? `Validierung: ${validationReasons.map(([reason, count]) => `${escapeHtml(reason)} ${formatNumber(count)}`).join(" · ")}. ` : ""}${auditReasons.length ? `Audit: ${auditReasons.map(([reason, count]) => `${escapeHtml(reason)} ${formatNumber(count)}`).join(" · ")}.` : ""}`
      : "Ausschlussgründe erscheinen nach Validierung und Audit.";
  }
  const plan = status?.scale_plan;
  const goal = plan?.targets?.find((item) => item.target_unique_tokens === 20000000);
  if ($("#corpus-scale-goal")) {
    const progress = goal ? Math.round((goal.progress || 0) * 1000) / 10 : 0;
    $("#corpus-scale-goal").innerHTML = `<span>20-Mio.-Corpus-Ziel</span><strong>${formatNumber(plan?.current_train_tokens || 0)} / 20.000.000 einzigartige Tokens</strong><i style="--p:${Math.min(100, progress)}%"></i><small>${goal ? `${formatNumber(goal.missing_unique_tokens)} fehlen · geschätzt ${goal.estimated_training_hours ?? "?"} h für 4 Durchläufe nach aktuellem Autotune` : "Noch keine Planung"}</small>`;
  }

  const stepStates = [downloaded, scanned, validated, audited, tokenizer, processed, instructions];
  $$(".wizard-step").forEach((element, index) => {
    element.classList.toggle("done", stepStates[index]);
    element.classList.toggle("active", !stepStates[index] && stepStates.slice(0, index).every(Boolean));
  });
}

async function selectVerifiedExpansion() {
  const candidates = (state.corpus?.registry?.sources || []).filter((source) => source.verified && source.catalog_tier === "verified-community");
  for (const source of candidates) {
    const box = document.querySelector(`[data-corpus-source="${CSS.escape(source.id)}"]`);
    if (box) { box.checked = true; box.closest(".source-card")?.classList.add("enabled"); }
  }
  if (!candidates.length) return toast("Keine verifizierten Erweiterungsquellen gefunden.", "error");
  await saveCorpusSources();
  toast(`${candidates.length} verifizierte Erweiterungsquellen aktiviert.`);
}

async function selectExpansionTier(mode) {
  const sources = state.corpus?.registry?.sources || [];
  const accepted = new Set(mode === "max" ? ["core-5m", "extended-20m"] : ["core-5m"]);
  const candidates = sources.filter((source) => source.verified && accepted.has(source.expansion_tier));
  for (const source of sources) {
    const box = document.querySelector(`[data-corpus-source="${CSS.escape(source.id)}"]`);
    if (!box) continue;
    let checked = box.checked;
    if (source.catalog_tier === "official") checked = true;
    if (["core-5m", "extended-20m"].includes(source.expansion_tier)) checked = candidates.includes(source);
    box.checked = checked;
    box.closest(".source-card")?.classList.toggle("enabled", checked);
  }
  if (!candidates.length) return toast("Keine passenden Ausbauquellen gefunden.", "error");
  await saveCorpusSources();
  const estimated = candidates.reduce((sum, source) => sum + Number(source.estimated_unique_tokens || 0), 0);
  toast(`${candidates.length} Ausbauquellen aktiviert · Katalogschätzung ~${formatNumber(estimated)} Tokens. Der echte Wert steht erst nach Audit und Tokenisierung fest.`, "info", 7500);
}

async function saveCorpusSources() {
  const sources = structuredClone(state.corpus?.registry?.sources || []);
  const checks = new Map($$('[data-corpus-source]').map((box) => [box.dataset.corpusSource, box.checked]));
  for (const source of sources) source.enabled = Boolean(checks.get(source.id));
  try {
    const registry = await api("/api/corpus/sources", { method: "PUT", body: JSON.stringify({ sources }) });
    state.corpus.registry = registry;
    renderCorpus(state.corpus);
    toast("Quellenauswahl gespeichert.");
  } catch (error) { toast(error.message, "error"); }
}

async function addCustomSource() {
  const id = $("#custom-source-id").value.trim().toLowerCase();
  const url = $("#custom-source-url").value.trim();
  const ref = $("#custom-source-ref").value.trim() || "main";
  if (!id || !url) return toast("Kurzname und Git-URL sind erforderlich.", "error");
  const sources = structuredClone(state.corpus?.registry?.sources || []);
  sources.push({
    id, title: id.replaceAll("-", " "), description: "Benutzerdefinierte erlaubte Quelle",
    url, branch: ref, ref, kind: $("#custom-source-kind").value,
    license: $("#custom-source-license").value, attribution: "Custom source contributors",
    enabled: true, beginner_recommended: false,
  });
  try {
    const registry = await api("/api/corpus/sources", { method: "PUT", body: JSON.stringify({ sources }) });
    state.corpus.registry = registry;
    renderCorpus(state.corpus);
    $("#custom-source-id").value = ""; $("#custom-source-url").value = ""; $("#custom-source-ref").value = "";
    toast("Eigene Quelle hinzugefügt. Prüfe Lizenz und Attribution vor einer Veröffentlichung.");
  } catch (error) { toast(error.message, "error"); }
}

async function runProfessionalAudit() {
  return startCorpusJob("/api/jobs/corpus/audit", "Professioneller Daten-Audit gestartet.");
}

async function runAutotune() {
  try {
    const job = await api("/api/jobs/hardware/autotune", { method: "POST", body: "{}" });
    state.currentJob = job; renderJob(job); setView("training"); toast("Hardware-Autotuner gestartet. Jeder Test läuft isoliert.");
  } catch (error) { toast(error.message, "error"); }
}

async function runSmoke50() {
  const config = $("#training-config").value;
  try {
    const preflight = await api(`/api/preflight?config=${encodeURIComponent(config)}&mode=smoke`);
    state.preflight = preflight; renderPreflight(preflight);
    if (!preflight.can_start) return toast("Smoke-Test ist durch die Vorprüfung blockiert.", "error", 6500);
    const job = await api("/api/jobs/train-smoke", { method: "POST", body: JSON.stringify({ config, resume: null }) });
    state.currentJob = job; renderJob(job); toast("50-Schritte-Probelauf gestartet.");
  } catch (error) { toast(error.message, "error"); }
  applyCorpusFilter();
}

function applyCorpusFilter() {
  const grid = $("#corpus-source-grid");
  if (!grid) return;
  const f = state.corpusFilter;
  const query = (f.search || "").trim().toLowerCase();
  const allSources = (state.corpus?.registry?.sources || []).length;
  let visible = 0;
  $$(".source-card").forEach(card => {
    const title = (card.dataset.sourceTitle || "").toLowerCase();
    const sid = (card.dataset.sourceId || "").toLowerCase();
    const enabled = card.dataset.sourceEnabled === "true";
    let show = true;
    if (f.enabled === "enabled" && !enabled) show = false;
    if (f.enabled === "disabled" && enabled) show = false;
    if (query && !title.includes(query) && !sid.includes(query)) show = false;
    card.style.display = show ? "" : "none";
    if (show) visible++;
  });
  const existing = $(".empty-state", grid);
  if (visible === 0 && allSources > 0) {
    if (!existing) {
      const msg = query ? "Keine Quellen gefunden für „" + f.search + "“." : "Keine Quellen im aktuellen Filter.";
      grid.insertAdjacentHTML("beforeend", '<div class=\"empty-state\">' + msg + '</div>');
    }
  } else {
    if (existing) existing.remove();
  }
}

async function refreshPreflight() {
  try {
    const config = $("#training-config").value;
    const report = await api(`/api/preflight?config=${encodeURIComponent(config)}`);
    state.preflight = report; renderPreflight(report);
    toast(report.status === "green" ? "Nachttraining ist freigegeben." : "Vorprüfung aktualisiert.", report.status === "red" ? "error" : "info");
  } catch (error) { toast(error.message, "error"); }
}

async function startCorpusJob(path, message, payload = {}) {
  try {
    const job = await api(path, { method: "POST", body: JSON.stringify(payload) });
    state.currentJob = job;
    renderJob(job);
    setView("training");
    toast(message);
  } catch (error) { toast(error.message, "error"); }
}

function dataEntryMatchesFilter(entry, filter) {
  if (filter === "all") return true;
  if (["train", "val", "test"].includes(filter)) return entry.split === filter && entry.kind === "training";
  return entry.kind === filter;
}

function renderFiles() {
  const query = $("#file-search").value.trim().toLowerCase();
  const filter = $("#data-kind-filter")?.value || "all";
  const files = (state.files || []).filter((file) => dataEntryMatchesFilter(file, filter) && file.path.toLowerCase().includes(query));
  const activeTokens = files.reduce((sum, item) => sum + Number(item.tokens || 0), 0);
  $("#file-count").textContent = `${formatNumber(files.length)} Einträge${activeTokens ? ` · ${formatNumber(activeTokens)} Tokens` : ""}`;
  $("#file-list").innerHTML = files.map((file) => {
    const openPath = file.storage_path || "";
    const detail = [file.kind === "training" ? file.split?.toUpperCase() : file.kind === "instruction" ? `${formatNumber(file.tasks || 0)} Aufgaben` : "Rohdatei", file.status === "pending" ? "noch nicht vorbereitet" : ""].filter(Boolean).join(" · ");
    const token = file.tokens != null ? `${formatNumber(file.tokens)} T` : file.kind === "raw" ? "RAW" : "";
    return `<button class="file-item ${state.currentFile === openPath ? "active" : ""} ${file.status === "pending" ? "pending" : ""}" data-file="${escapeHtml(openPath)}" ${openPath ? "" : "disabled"} title="${escapeHtml(file.path)}">
      <span class="file-icon">‹›</span><span class="file-main"><span class="file-name">${escapeHtml(file.path.replace(/^training\//, ""))}</span><span class="file-detail">${escapeHtml(detail)}</span></span><span class="token-badge">${escapeHtml(token)}</span>
    </button>`;
  }).join("") || '<div class="empty-state">Keine Daten für diesen Filter gefunden.</div>';
  $$(".file-item[data-file]").forEach((button) => button.addEventListener("click", () => { if (button.dataset.file) openFile(button.dataset.file); }));
}

function renderDataCatalogSummary() {
  const catalog = state.dataCatalog;
  const summary = catalog?.summary || {};
  $("#dataset-token-count").textContent = formatNumber(summary.train_tokens || 0);
  $("#data-token-breakdown").innerHTML = [
    ["Train", summary.train_tokens], ["Validation", summary.val_tokens], ["Test", summary.test_tokens],
    ["Aktive Dokumente", summary.training_documents], ["Neu/pending", summary.pending_documents], ["Aufgaben", summary.instruction_tasks],
  ].map(([label, value]) => `<div><span>${label}</span><strong>${formatNumber(value || 0)}</strong></div>`).join("");
  const live = $("#data-live-status");
  const stateBox = $("#data-catalog-state");
  if (!catalog?.manifest) {
    live.textContent = "Live · Rohdateien sichtbar · noch kein aktiver Tokenstream";
    stateBox.textContent = "Nach Änderungen zuerst Audit/Tokenizer und Trainingsdaten vorbereiten.";
    stateBox.classList.remove("stale");
    return;
  }
  const path = catalog.manifest.manifest_path || "manifest.json";
  live.textContent = catalog.stale ? "Live · Quelldaten geändert · Tokenstream ist veraltet" : `Live · ${formatNumber(summary.entries)} Einträge synchron`;
  live.classList.toggle("stale", Boolean(catalog.stale));
  stateBox.textContent = catalog.stale
    ? "Neue oder gelöschte Rohdaten sind bereits sichtbar. Die angezeigten aktiven Tokens stammen aber noch aus dem letzten vorbereiteten Manifest. Führe die Pipeline ab dem betroffenen Schritt erneut aus."
    : `Aktiver Datensatz: ${path}. Alle ${formatNumber(summary.total_tokens || 0)} vorbereiteten Train-/Validation-/Test-Tokens werden aufgelistet.`;
  stateBox.classList.toggle("stale", Boolean(catalog.stale));
}

async function openFile(path) {
  if (isEditorDirty() && !confirm("Ungespeicherte Änderungen verwerfen?")) return;
  try {
    const data = await api(`/api/data/file?path=${encodeURIComponent(path)}`);
    state.currentFile = data.path;
    state.currentFileOriginal = data.content;
    $("#data-editor").value = data.content;
    $("#data-editor").disabled = !data.editable;
    $("#save-editor").disabled = !data.editable;
    $("#delete-editor").disabled = !data.deletable;
    $("#validate-editor").disabled = !data.path.endsWith(".gd");
    $("#editor-path").textContent = data.path;
    $("#editor-meta").textContent = `${new Blob([data.content]).size} Bytes · UTF-8 · ${data.editable ? "bearbeitbar" : "nur lesen"}`;
    updateEditorStats();
    renderFiles();
  } catch (error) { toast(error.message, "error"); }
}

function resetDataEditor() {
  state.currentFile = null;
  state.currentFileOriginal = "";
  $("#data-editor").value = "";
  $("#data-editor").disabled = true;
  $("#save-editor").disabled = true;
  $("#delete-editor").disabled = true;
  $("#validate-editor").disabled = true;
  $("#editor-path").textContent = "Keine Datei geöffnet";
  $("#editor-meta").textContent = "Wähle links eine Datei.";
  updateEditorStats();
}

function isEditorDirty() { return state.currentFile && !$("#data-editor").disabled && $("#data-editor").value !== state.currentFileOriginal; }

function updateEditorStats() {
  const content = $("#data-editor").value;
  $("#editor-lines").textContent = `${content ? content.split("\n").length : 0} Zeilen`;
  $("#editor-save-state").textContent = isEditorDirty() ? "Ungespeichert" : $("#data-editor").disabled && state.currentFile ? "Nur lesen" : "Gespeichert";
}

async function saveEditor() {
  if (!state.currentFile || $("#data-editor").disabled) return;
  try {
    const content = $("#data-editor").value;
    const result = await api("/api/data/file", { method: "PUT", body: JSON.stringify({ path: state.currentFile, content }) });
    state.currentFileOriginal = content;
    updateEditorStats();
    toast(result.backup ? "Datei gespeichert; Backup wurde angelegt." : "Datei gespeichert.");
    await refreshDataCatalog(true);
  } catch (error) { toast(`Speichern fehlgeschlagen: ${error.message}`, "error"); }
}

async function deleteCurrentFile() {
  if (!state.currentFile || $("#delete-editor").disabled) return;
  const path = state.currentFile;
  if (!confirm(`Datei wirklich löschen?\n\n${path}\n\nVor dem Löschen wird ein Backup angelegt. Der vorbereitete Tokenstream gilt danach als veraltet.`)) return;
  try {
    const result = await api(`/api/data/file?path=${encodeURIComponent(path)}`, { method: "DELETE" });
    resetDataEditor();
    await refreshDataCatalog(true);
    toast(`Datei gelöscht. Backup: ${result.backup}`);
  } catch (error) { toast(`Löschen fehlgeschlagen: ${error.message}`, "error", 6500); }
}

async function refreshDataCatalog(force = false) {
  try {
    const catalog = await api("/api/data/catalog");
    const changed = force || catalog?.revision !== state.dataCatalogRevision;
    state.dataCatalog = catalog;
    state.dataCatalogRevision = catalog?.revision || "";
    state.files = catalog?.entries || [];
    if (changed) {
      if (state.currentFile && !state.files.some((item) => item.storage_path === state.currentFile) && !isEditorDirty()) resetDataEditor();
      renderFiles();
      renderDataCatalogSummary();
    }
  } catch { /* transient live refresh */ }
}

async function refreshFilesOnly() { await refreshDataCatalog(true); }

function addMessage(role, content, options = {}) {
  const article = document.createElement("article");
  article.className = `message ${role === "user" ? "user-message" : "assistant-message"}`;
  const avatar = role === "user" ? "YOU" : "AI";
  const code = options.code;
  article.innerHTML = `
    <div class="message-avatar">${avatar}</div>
    <div class="message-body">
      <div class="message-meta">${role === "user" ? "Dein Prompt" : "Godot Coder · lokal"}</div>
      ${code ? `<pre class="message-code">${escapeHtml(content)}</pre>` : `<p>${escapeHtml(content).replaceAll("\n", "<br>")}</p>`}
  ${options.loading ? '<div class="message-meta loading-dots" style="margin-top:9px">Modell arbeitet</div>' : ""}
  ${code && !options.skipTools ? '<div class="message-tools"><button data-copy-message>Code kopieren</button><button data-validate-message>Godot prüfen</button></div>' : ""}
    </div>
  `;
  $("#chat-feed").append(article);
  $("#chat-feed").scrollTop = $("#chat-feed").scrollHeight;
  $("[data-copy-message]", article)?.addEventListener("click", () => navigator.clipboard.writeText(content).then(() => toast("Code kopiert.")));
  $("[data-validate-message]", article)?.addEventListener("click", () => validateCode(content));
  return article;
}

async function generate() {
  const prompt = $("#chat-input").value;
  if (!prompt.trim()) return toast("Gib zuerst einen Prompt ein.", "error");
  if (!state.activeCheckpoint) return toast("Kein Checkpoint ausgewählt.", "error");
  addMessage("user", prompt, { code: true });
  const loading = addMessage("assistant", "", { loading: true });
  $("#generate-button").disabled = true;
  try {
    // Use streaming SSE endpoint
    const response = await fetch("/api/chat/generate-stream", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        checkpoint: state.activeCheckpoint,
        prompt,
        max_new_tokens: Number($("#max-tokens").value),
        temperature: Number($("#temperature").value),
        top_k: Number($("#top-k").value),
      }),
    });
    if (!response.ok) {
      const err = await response.text();
      let detail = err;
      try { detail = JSON.parse(err)?.detail || err; } catch {}
      throw new Error(detail);
    }
    loading.remove();
    const msg = addMessage("assistant", "", { code: true, skipTools: true });
    const pre = msg.querySelector(".message-code");
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";
      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        const payload = line.slice(6);
        if (payload === "[DONE]") continue;
        try {
          const parsed = JSON.parse(payload);
          if (parsed.token) pre.textContent += parsed.token;
          if (parsed.error) { pre.textContent += `\n[Fehler: ${parsed.error}]`; break; }
        } catch {}
      }
      $("#chat-feed").scrollTop = $("#chat-feed").scrollHeight;
    }
    const result = pre.textContent;
    state.lastPrompt = prompt;
    state.lastGenerated = result;
    setupMessageTools(msg, result);
    $("#validate-last").disabled = false;
    $("#save-last").disabled = false;
  } catch (error) {
    if (loading.parentNode) loading.remove();
    toast(`Generierung fehlgeschlagen: ${error.message}`, "error", 7000);
  } finally {
    $("#generate-button").disabled = !state.activeCheckpoint;
    $("#chat-input").value = "";
    $("#chat-input").focus();
  }
}

function setupMessageTools(msg, content) {
  $("[data-copy-message]", msg)?.addEventListener("click", () => navigator.clipboard.writeText(content).then(() => toast("Code kopiert.")));
  $("[data-validate-message]", msg)?.addEventListener("click", () => validateCode(content));
}

function setValidationState(result) {
  const target = $("#validation-state");
  target.classList.remove("passed", "failed");
  if (!result) {
    target.innerHTML = '<span class="validation-icon">○</span><div><strong>Noch nicht geprüft</strong><small>Generierten Code direkt parsen lassen.</small></div>';
    return;
  }
  target.classList.add(result.passed ? "passed" : "failed");
  target.innerHTML = `<span class="validation-icon">${result.passed ? "✓" : "×"}</span><div><strong>${result.passed ? "Parser bestanden" : "Parserfehler"}</strong><small>${escapeHtml((result.output || "Keine Ausgabe").split("\n").slice(-2).join(" · "))}</small></div>`;
}

async function validateCode(code) {
  if (!code) return toast("Kein Code zum Prüfen.", "error");
  try {
    const result = await api("/api/chat/validate", { method: "POST", body: JSON.stringify({ code }) });
    setValidationState(result);
    toast(result.passed ? "Godot-Parser bestanden." : "Godot hat einen Parserfehler gefunden.", result.passed ? "info" : "error", 5500);
    if (!result.passed && result.output) addMessage("assistant", result.output, { code: true });
    return result;
  } catch (error) {
    toast(`Godot-Prüfung fehlgeschlagen: ${error.message}`, "error");
  }
}

function clearChat() {
  $$("#chat-feed .message:not(.intro-message)").forEach((item) => item.remove());
  state.lastGenerated = "";
  state.lastPrompt = "";
  $("#validate-last").disabled = true;
  $("#save-last").disabled = true;
  setValidationState(null);
}

async function probeProfiles() {
  try {
    const job = await api("/api/jobs/hardware/probe", { method: "POST", body: "{}" });
    state.currentJob = job;
    renderJob(job);
    setView("training");
    toast("Hardware-Probelauf für alle drei Profile gestartet.");
  } catch (error) { toast(error.message, "error"); }
}

async function startTraining() {
  const config = $("#training-config").value;
  if (!config) return toast("Keine Konfiguration ausgewählt.", "error");
  const selected = state.configs.find((item) => item.path === config);
  if (selected?.profile_id) {
    if (!selected.data_ready || !selected.tokenizer_ready) return toast("Dieses Profil braucht zuerst den vollständigen Wissensaufbau: Quellen, Prüfung, BPE und Trainingsdaten.", "error", 6500);
    if (selected.profile_generated) {
      const recommendation = state.autotune?.recommendation;
      if (!recommendation || recommendation.config !== selected.path) return toast("Diese automatisch erzeugte Konfiguration hat keinen passenden Autotune-Bericht mehr. Hardware erneut einstellen.", "error", 6500);
    } else {
      const result = profileProbeEntry(selected.profile_id)?.configured_result;
      if (!result || result.status !== "pass") return toast("Führe vor diesem großen Lauf zuerst den Hardware-Probelauf aus.", "error", 6000);
    }
  }
  try {
    const preflight = await api(`/api/preflight?config=${encodeURIComponent(config)}&mode=full`);
    state.preflight = preflight; renderPreflight(preflight);
    if (!preflight.can_start) return toast("Langtraining ist durch die Vorprüfung blockiert.", "error", 7000);
    const job = await api("/api/jobs/train", {
      method: "POST",
      body: JSON.stringify({ config, resume: $("#training-resume").value || null }),
    });
    state.currentJob = job;
    renderJob(job);
    toast("Training gestartet.");
  } catch (error) { toast(error.message, "error"); }
}

async function prepareData() {
  try {
    const job = await api("/api/jobs/prepare", { method: "POST", body: JSON.stringify({}) });
    state.currentJob = job;
    renderJob(job);
    toast("Datenaufbereitung gestartet.");
  } catch (error) { toast(error.message, "error"); }
}

async function startSimpleJob(path, message) {
  try {
    const job = await api(path, { method: "POST", body: "{}" });
    state.currentJob = job;
    renderJob(job);
    toast(message);
  } catch (error) { toast(error.message, "error"); }
}

async function benchmarkModel() {
  if (!state.activeCheckpoint) return toast("Kein Checkpoint ausgewählt.", "error");
  try {
    const job = await api("/api/jobs/benchmark", {
      method: "POST",
      body: JSON.stringify({ checkpoint: state.activeCheckpoint }),
    });
    state.currentJob = job;
    renderJob(job);
    setView("training");
    toast("Fester Parser-Benchmark gestartet.");
  } catch (error) { toast(error.message, "error"); }
}

async function stopJob() {
  try {
    const job = await api("/api/jobs/stop", { method: "POST", body: "{}" });
    state.currentJob = job;
    renderJob(job);
    toast("Stop-Signal gesendet.");
  } catch (error) { toast(error.message, "error"); }
}

function renderJob(job) {
  const active = job && ["starting", "running", "stopping"].includes(job.status);
  $("#start-training").disabled = active;
  $("#probe-profiles").disabled = active;
  $("#prepare-data").disabled = active;
  $("#build-curriculum").disabled = active;
  $("#validate-curriculum").disabled = active;
  $("#prepare-curriculum").disabled = active;
  $("#benchmark-model").disabled = active || !state.activeCheckpoint;
  $("#stop-job").disabled = !active;
  $("#generate-button").disabled = active || !state.activeCheckpoint;
  if ($("#import-local-sources")) $("#import-local-sources").disabled = active;
  ["#professional-audit", "#professional-autotune", "#professional-smoke", "#professional-preflight"].forEach((selector) => {
    const button = $(selector);
    if (button) button.disabled = active;
  });
  const pill = $("#job-pill");
  pill.classList.toggle("running", active);
  pill.classList.toggle("failed", job?.status === "failed");
  $("#job-pill-text").textContent = !job ? "Bereit" : `${friendlyJobLabel(job.kind)} · ${projectStatusLabel(job.status)}`;
  renderWorkflowSteps(job);
  renderProfessionalRun(job);
  renderLocalProgress(job);
  renderLog(job);
  renderRemote(state.remote);
  if (!job) {
    $("#training-status").textContent = "Kein aktiver Lauf";
    $("#training-progress").style.width = "0%";
    $("#training-progress-label").textContent = "0 %";
    $("#log-job-id").textContent = "Noch kein Job gestartet";
    updateConfigMetrics();
    return;
  }
  const progress = job.progress_state || {};
  const phase = progress.phase ? ` · ${ProgressTools.phaseLabel(progress.phase)}` : (job.step ? ` · Schritt ${job.step}` : "");
  const lastSuccess = job.status === "failed" && job.last_successful_step?.phase_label
    ? ` · Letzter Erfolg: ${job.last_successful_step.phase_label}`
    : "";
  $("#training-status").textContent = `${friendlyJobLabel(job.kind)} · ${projectStatusLabel(job.status)}${phase}${lastSuccess}`;
  const percent = Math.max(0, Math.min(100, Math.round(Number(job.progress || 0) * 100)));
  $("#training-progress").style.width = `${percent}%`;
  $("#training-progress-label").textContent = job.progress != null || job.max_steps ? `${percent} %` : projectStatusLabel(job.status);
  $("#log-job-id").textContent = `Job ${job.id} · PID ${job.pid || "–"} · ${ProgressTools.formatDuration(job.elapsed_seconds)}${job.return_code == null ? "" : ` · Exitcode ${job.return_code}`}`;
  updateConfigMetrics();
}

async function pollJob() {
  try {
    const job = await api("/api/jobs/current");
    const priorStatus = state.currentJob?.status;
    state.currentJob = job;
    renderJob(job);
    if (priorStatus && ["starting", "running", "stopping"].includes(priorStatus) && job && ["completed", "failed", "stopped"].includes(job.status)) {
      toast(`Job ${job.status}: ${job.kind}`, job.status === "completed" ? "info" : "error");
      const [checkpoints, manifest, curriculum, dataCatalog, corpus, hardwareProbe, trainingReports, configs, preflight, autotune] = await Promise.all([
        api("/api/checkpoints"), api("/api/data/manifest"), api("/api/curriculum/status"), api("/api/data/catalog"), api("/api/corpus/status"),
        api("/api/hardware/probe"), api("/api/training/reports"), api("/api/configs"), api("/api/preflight"), api("/api/hardware/autotune")
      ]);
      state.checkpoints = checkpoints;
      state.curriculum = curriculum;
      state.dataCatalog = dataCatalog;
      state.dataCatalogRevision = dataCatalog?.revision || "";
      state.files = dataCatalog?.entries || [];
      state.corpus = corpus;
      state.hardwareProbe = hardwareProbe;
      state.trainingReports = trainingReports;
      state.preflight = preflight;
      state.autotune = autotune;
      state.configs = configs;
      const recommendedPath = autotune?.recommendation?.config;
      renderCheckpointSelects();
      renderManifest(manifest);
      renderCurriculum(curriculum);
      renderCorpus(corpus);
      renderConfigs();
      if (job.kind === "hardware-autotune" && recommendedPath && configs.some((item) => item.path === recommendedPath)) {
        $("#training-config").value = recommendedPath;
        updateConfigMetrics();
      }
      renderTrainingProfiles();
      renderTrainingReports();
      renderPreflight(preflight);
      renderFiles();
      renderDataCatalogSummary();
      renderModels();
    }
  } catch { /* transient server refresh */ }
}

function renderModels() {
  const target = $("#model-grid");
  if (!state.checkpoints.length) {
    target.innerHTML = '<div class="empty-state">Noch keine Checkpoints vorhanden. Starte im Training-Tab den ersten Lauf.</div>';
    return;
  }
  target.innerHTML = state.checkpoints.map((item) => `
    <article class="model-card ${item.path === state.activeCheckpoint ? "active" : ""}">
      <div class="model-head"><div class="model-name">${escapeHtml(item.run)} / ${escapeHtml(item.name)}</div><span class="model-badge">${escapeHtml(item.kind)}</span></div>
      <div class="model-path">${escapeHtml(item.path)}</div>
      <div class="model-stats"><span>${item.size_mb} MB</span><span>${item.step ? `Step ${formatNumber(item.step)}` : formatDate(item.modified_at)}</span></div>
      <button class="${item.path === state.activeCheckpoint ? "secondary-button" : "ghost-button"} compact" data-use-model="${escapeHtml(item.path)}">${item.path === state.activeCheckpoint ? "Aktiv" : "Im Chat verwenden"}</button>
    </article>
  `).join("");
  $$('[data-use-model]').forEach((button) => button.addEventListener("click", () => setActiveCheckpoint(button.dataset.useModel)));
}

function setActiveCheckpoint(path) {
  state.activeCheckpoint = path;
  localStorage.setItem("godot-coder-active-checkpoint", path);
  $("#chat-checkpoint").value = path;
  updateActiveModelSummary();
  renderModels();
  toast("Aktiver Checkpoint geändert.");
}

function renderSystem() {
  const o = state.overview;
  if (!o) return;
  const items = [
    ["Python", o.python],
    ["PyTorch", o.torch],
    ["CUDA Build", o.torch_cuda || "CPU"],
    ["GPU", o.gpu?.name || "Nicht aktiv"],
    ["VRAM", o.gpu ? `${o.gpu.vram_gib} GiB` : "–"],
    ["Godot", o.godot_version || "Nicht gefunden"],
    ["Datendateien", formatNumber(o.dataset_file_count)],
    ["Checkpoints", formatNumber(o.checkpoint_count)],
  ];
  $("#system-cards").innerHTML = items.map(([label, value]) => `<div class="system-card"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`).join("");
  $("#project-root").textContent = o.project_root;
}

function openNewFileModal(mode = "new") {
  state.modalMode = mode;
  const stamp = new Date().toISOString().replaceAll(/[-:TZ.]/g, "").slice(0, 14);
  $("#new-file-path").value = mode === "generated"
    ? `data/raw/user_lessons/generated_${stamp}.gd`
    : "data/raw/curriculum/01_basics/new_example.gd";
  state.modalContent = mode === "generated" ? state.lastGenerated : "extends Node\n\nfunc _ready() -> void:\n    pass\n";
  $("#new-file-modal").hidden = false;
  $("#new-file-path").focus();
}

async function createFile() {
  const path = $("#new-file-path").value.trim();
  if (!path) return;
  try {
    await api("/api/data/file", { method: "PUT", body: JSON.stringify({ path, content: state.modalContent }) });
    $("#new-file-modal").hidden = true;
    await refreshFilesOnly();
    await openFile(path);
    setView("data");
    toast("Trainingsdatei angelegt.");
  } catch (error) { toast(error.message, "error"); }
}


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
        ? "Remote-Schreibzugriff ist gesperrt. Öffne den Remote-Tab und gib die PIN ein."
        : "Diese Tailscale-Identität ist nicht freigegeben.";
    } else if (element.dataset.remoteWasEnabled === "true") {
      element.disabled = false;
      delete element.dataset.remoteWasEnabled;
      element.removeAttribute("title");
    }
  }
}

function formatBytes(value) {
  const number = Number(value || 0);
  if (number >= 1024 ** 3) return `${(number / 1024 ** 3).toFixed(1)} GiB`;
  if (number >= 1024 ** 2) return `${(number / 1024 ** 2).toFixed(1)} MiB`;
  if (number >= 1024) return `${(number / 1024).toFixed(1)} KiB`;
  return `${number} B`;
}

function renderRemoteInbox() {
  const target = $("#remote-inbox-list");
  if (!target) return;
  const local = state.corpus?.local_sources;
  const items = local?.inbox_items || [];
  target.innerHTML = items.length ? items.map((item) => `
    <div class="remote-inbox-item">
      <strong title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</strong>
      <small>${item.kind === "zip" ? formatBytes(item.size_bytes) : "Lokaler Projektordner"}</small>
    </div>
  `).join("") : '<div class="empty-state">Noch keine ZIPs oder Projektordner im privaten Importordner.</div>';
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
    ? `Fertig · ${formatBytes(received)}`
    : job.status === "failed"
      ? "Download fehlgeschlagen"
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
  let title = "Nur lokal erreichbar";
  let detail = remote.configured ? "Remote-Zugriff ist konfiguriert, aber diese Ansicht läuft lokal." : "Führe CONFIGURE_REMOTE_STUDIO.bat am PC aus.";
  if (isRemote && !remote.can_read) {
    stateName = "blocked"; title = "Zugriff nicht freigegeben"; detail = "Diese Tailscale-Identität steht nicht in der lokalen Freigabeliste.";
  } else if (isRemote && remote.can_write) {
    stateName = "ready"; title = "Remote-Schreibzugriff aktiv"; detail = "Jobs und Importe laufen weiterhin vollständig auf dem PC.";
  } else if (isRemote) {
    stateName = "locked"; title = "Sicherer Lesemodus"; detail = "Status und Logs sind sichtbar. Schreibaktionen benötigen die PIN.";
  } else if (remote.enabled && tailscale.online) {
    stateName = "ready"; title = "Remote Studio vorbereitet"; detail = tailscale.serve_url || "Tailscale ist online. Prüfe die Serve-Konfiguration.";
  } else if (remote.enabled) {
    stateName = "locked"; title = "Remote konfiguriert"; detail = "Tailscale ist aktuell nicht als online erkannt.";
  }
  $("#remote-state-card").dataset.state = stateName;
  setText("#remote-state-title", title);
  setText("#remote-state-detail", detail);
  setText("#remote-access-kind", isRemote ? "Tailscale Serve" : "Lokaler Browser");
  setText("#remote-identity", identity, identity);
  setText("#remote-tailscale-state", tailscale.online ? "Online" : tailscale.installed ? (tailscale.backend_state || "Nicht verbunden") : "Nicht gefunden");
  const serveUrl = tailscale.serve_url || (isRemote ? location.origin : "–");
  setText("#remote-serve-url", serveUrl, serveUrl);
  setText("#remote-serve-command", remote.serve_command || "tailscale serve --bg http://127.0.0.1:8765");

  const lockState = $("#remote-lock-state");
  const unlockForm = $("#remote-unlock-form");
  const lockButton = $("#remote-lock");
  if (!isRemote) {
    lockState.textContent = remote.enabled ? "Am Handy öffnet das Studio zuerst im Lesemodus. Dort kann die PIN eingegeben werden." : "Remote-Zugriff ist noch nicht lokal konfiguriert.";
    unlockForm.hidden = true; lockButton.hidden = true;
  } else if (!remote.identity_allowed) {
    lockState.textContent = "Diese Tailscale-Identität ist nicht freigegeben.";
    unlockForm.hidden = true; lockButton.hidden = true;
  } else if (remote.can_write) {
    lockState.textContent = `Entsperrt für ${identity}. Die Sitzung läuft automatisch ab.`;
    unlockForm.hidden = true; lockButton.hidden = false;
  } else {
    lockState.textContent = `Lesemodus für ${identity}. PIN nur in dieser privaten HTTPS-Sitzung eingeben.`;
    unlockForm.hidden = false; lockButton.hidden = true;
  }

  const banner = $("#remote-access-banner");
  banner.hidden = !isRemote || remote.can_write;
  if (isRemote && !remote.can_write) {
    setText("#remote-banner-title", remote.identity_allowed ? "Remote-Lesemodus" : "Remote-Zugriff blockiert");
    setText("#remote-banner-detail", remote.identity_allowed ? "Schreibaktionen benötigen die PIN." : "Identität nicht freigegeben.");
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
    if (showToast) toast("Remote-Status aktualisiert.");
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
  resultBox.textContent = "Remote-Verbindung wird lokal geprüft …";
  try {
    const report = await api("/api/remote/self-check");
    resultBox.dataset.status = report.ok ? "passed" : "failed";
    resultBox.innerHTML = (report.checks || []).map((check) =>
      `<div><strong>${check.passed ? "✓" : "×"} ${escapeHtml(check.name)}</strong><span>${escapeHtml(check.detail || "")}</span></div>`
    ).join("") || "Keine Prüfergebnisse erhalten.";
    toast(report.ok ? "Remote-Selbsttest bestanden." : "Remote-Selbsttest hat Probleme gefunden.", report.ok ? "info" : "error", 6500);
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
  if (!/^\d{6,12}$/.test(pin)) return toast("Die PIN muss aus 6 bis 12 Ziffern bestehen.", "error");
  try {
    const result = await api("/api/remote/unlock", { method: "POST", body: JSON.stringify({ pin }) });
    state.remoteCsrf = result.csrf_token;
    sessionStorage.setItem("godot-coder-remote-csrf", state.remoteCsrf);
    $("#remote-pin").value = "";
    await refreshRemote();
    await refreshAll();
    toast("Remote-Schreibzugriff entsperrt.");
  } catch (error) { toast(error.message, "error"); }
}

async function lockRemote() {
  try {
    await api("/api/remote/lock", { method: "POST", body: "{}" });
  } catch (error) {
    if (!/gesperrt/i.test(error.message)) toast(error.message, "error");
  }
  state.remoteCsrf = "";
  sessionStorage.removeItem("godot-coder-remote-csrf");
  await refreshRemote();
  toast("Remote-Schreibzugriff gesperrt.");
}

async function startRemoteSourceDownload() {
  const url = $("#remote-source-url").value.trim();
  const confirmed = $("#confirm-remote-link-ownership").checked;
  if (!url) return toast("Gib einen Repository- oder ZIP-Link ein.", "error");
  if (!confirmed) return toast("Bestätige zuerst die lokale Nutzungsberechtigung.", "error");
  try {
    const job = await api("/api/jobs/remote/source-download", {
      method: "POST",
      body: JSON.stringify({ url, confirm_owned: true }),
    });
    state.currentJob = job;
    renderJob(job);
    toast("Der PC lädt und prüft die Remote-Quelle.");
  } catch (error) { toast(error.message, "error"); }
}

function uploadRemoteSource() {
  const file = $("#remote-source-file").files?.[0];
  if (!file) return toast("Wähle zuerst ein ZIP aus.", "error");
  if (!file.name.toLowerCase().endsWith(".zip")) return toast("Nur ZIP-Dateien werden angenommen.", "error");
  if (!$("#confirm-remote-upload-ownership").checked) return toast("Bestätige zuerst die lokale Nutzungsberechtigung.", "error");
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
  xhr.onerror = () => toast("Upload-Verbindung ist abgebrochen.", "error");
  xhr.onload = async () => {
    let payload = null;
    try { payload = JSON.parse(xhr.responseText || "null"); } catch { payload = xhr.responseText; }
    if (xhr.status < 200 || xhr.status >= 300) {
      return toast(payload?.detail || payload || `Upload fehlgeschlagen (${xhr.status}).`, "error");
    }
    box.querySelector("i").style.width = "100%";
    box.querySelector("strong").textContent = `Fertig · ${formatBytes(payload.size_bytes)}`;
    $("#remote-source-file").value = "";
    setText("#remote-file-name", "Keine Datei gewählt");
    state.corpus = await api("/api/corpus/status");
    renderCorpus(state.corpus);
    renderRemote(state.remote);
    toast(`${payload.name} liegt jetzt im privaten Importordner.`);
  };
  xhr.send(file);
}

async function importRemoteSources() {
  if (!$("#confirm-remote-import-ownership").checked) return toast("Bestätige zuerst die Eigentums- und Nutzungsangabe.", "error");
  try {
    const job = await api("/api/jobs/corpus/local-import", { method: "POST", body: JSON.stringify({ confirm_owned: true }) });
    state.currentJob = job;
    renderJob(job);
    setView("corpus");
    toast("Private Quellen werden auf dem PC geprüft und importiert.");
  } catch (error) { toast(error.message, "error"); }
}

function registerPwa() {
  const stateElement = $("#pwa-state");
  const installButton = $("#install-pwa");
  if (!("serviceWorker" in navigator)) {
    stateElement.textContent = "Dieser Browser unterstützt keine Service Worker.";
    return;
  }
  navigator.serviceWorker.register("/sw.js").then(() => {
    stateElement.textContent = window.matchMedia("(display-mode: standalone)").matches
      ? "Das Studio läuft bereits als installierte App."
      : "Die mobile Oberfläche ist offline-fest als App-Shell vorbereitet; Live-Daten benötigen weiterhin den PC.";
  }).catch((error) => { stateElement.textContent = `PWA konnte nicht registriert werden: ${error.message}`; });
  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    state.deferredInstallPrompt = event;
    installButton.disabled = false;
  });
  installButton.addEventListener("click", async () => {
    if (!state.deferredInstallPrompt) return;
    state.deferredInstallPrompt.prompt();
    await state.deferredInstallPrompt.userChoice;
    state.deferredInstallPrompt = null;
    installButton.disabled = true;
  });
}

function updateNetworkState() {
  document.body.classList.toggle("offline", !navigator.onLine);
  if (!navigator.onLine && state.remote?.is_remote) {
    setText("#remote-state-detail", "Handy ist offline oder die Verbindung zum PC wurde unterbrochen.");
  }
}

function bindEvents() {
  $$(".nav-item").forEach((button) => button.addEventListener("click", () => setView(button.dataset.view)));
  // Keyboard shortcuts: 1-7 switch views, Ctrl+Enter already handled in chat input
  document.addEventListener("keydown", (event) => {
    if (event.target.tagName === "INPUT" || event.target.tagName === "TEXTAREA" || event.target.tagName === "SELECT") return;
    const key = Number(event.key);
    if (key >= 1 && key <= 7 && !event.ctrlKey && !event.metaKey) {
      const name = viewOrder[key - 1];
      if (name) setView(name);
    }
  });
  $("#remote-banner-open").addEventListener("click", () => setView("remote"));
  $("#refresh-remote").addEventListener("click", () => refreshRemote(true));
  $("#remote-self-check").addEventListener("click", runRemoteSelfCheck);
  $("#remote-unlock-form").addEventListener("submit", unlockRemote);
  $("#remote-lock").addEventListener("click", lockRemote);
  $("#remote-download-source").addEventListener("click", startRemoteSourceDownload);
  $("#remote-upload-source").addEventListener("click", uploadRemoteSource);
  $("#remote-import-sources").addEventListener("click", importRemoteSources);
  $("#remote-source-file").addEventListener("change", (event) => setText("#remote-file-name", event.target.files?.[0]?.name || "Keine Datei gewählt", event.target.files?.[0]?.name || ""));
  $("#refresh-all").addEventListener("click", () => refreshAll(true));
  $("#refresh-models").addEventListener("click", () => refreshAll(true));
  $("#chat-checkpoint").addEventListener("change", (event) => setActiveCheckpoint(event.target.value));
  $("#training-config").addEventListener("change", updateConfigMetrics);
  $("#generate-button").addEventListener("click", generate);
  $("#chat-input").addEventListener("keydown", (event) => {
    if (event.ctrlKey && event.key === "Enter") { event.preventDefault(); generate(); }
  });
  $("#clear-chat").addEventListener("click", clearChat);
  $("#validate-last").addEventListener("click", () => validateCode(state.lastGenerated));
  $("#save-last").addEventListener("click", () => openNewFileModal("generated"));
  $("#start-training").addEventListener("click", startTraining);
  $("#probe-profiles").addEventListener("click", probeProfiles);
  $("#prepare-data").addEventListener("click", prepareData);
  $("#open-corpus").addEventListener("click", () => setView("corpus"));
  $("#build-curriculum").addEventListener("click", () => startSimpleJob("/api/jobs/curriculum/build", "Lehrplan-Erzeugung gestartet."));
  $("#validate-curriculum").addEventListener("click", () => startSimpleJob("/api/jobs/curriculum/validate", "Godot-Prüfung aller Lektionen gestartet."));
  $("#prepare-curriculum").addEventListener("click", () => startSimpleJob("/api/jobs/curriculum/prepare", "Lehrplan-Tokenisierung gestartet."));
  $("#save-corpus-sources").addEventListener("click", saveCorpusSources);
  $("#select-verified-expansion").addEventListener("click", selectVerifiedExpansion);
  $("#select-core-expansion").addEventListener("click", () => selectExpansionTier("core"));
  $("#select-max-expansion").addEventListener("click", () => selectExpansionTier("max"));
  $("#add-custom-source").addEventListener("click", addCustomSource);
  $("#open-local-source-inbox").addEventListener("click", openLocalSourceInbox);
  $("#import-local-sources").addEventListener("click", importLocalSources);
  $("#corpus-fetch").addEventListener("click", () => startCorpusJob("/api/jobs/corpus/fetch", "Quellen-Download gestartet."));
  $("#corpus-build").addEventListener("click", () => startCorpusJob("/api/jobs/corpus/build", "Korpus-Scan gestartet."));
  $("#corpus-validate").addEventListener("click", () => startCorpusJob("/api/jobs/corpus/validate", "Godot-Prüfung gestartet."));
  $("#corpus-audit").addEventListener("click", runProfessionalAudit);
  $("#corpus-tokenizer").addEventListener("click", () => startCorpusJob("/api/jobs/corpus/tokenizer", "BPE-Tokenizer-Training gestartet.", { vocab_size: Number($("#bpe-vocab").value), min_frequency: Number($("#bpe-frequency").value) }));
  $("#corpus-prepare").addEventListener("click", () => startCorpusJob("/api/jobs/corpus/prepare", "Korpus-Tokenisierung gestartet."));
  $("#corpus-instructions").addEventListener("click", () => startCorpusJob("/api/jobs/corpus/instructions", "Aufgabendaten werden aus dem auditierten Corpus erzeugt."));
  $("#bpe-vocab").addEventListener("input", (event) => $("#bpe-vocab-output").textContent = event.target.value);
  $("#bpe-frequency").addEventListener("input", (event) => $("#bpe-frequency-output").textContent = event.target.value);
  const corpusSearch = $("#corpus-source-search");
  if (corpusSearch) {
    corpusSearch.addEventListener("input", () => {
      state.corpusFilter.search = corpusSearch.value;
      localStorage.setItem("godot-coder-corpus-filter", JSON.stringify(state.corpusFilter));
      applyCorpusFilter();
    });
  }
  $$("#corpus-filter-chips .filter-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      $$("#corpus-filter-chips .filter-chip").forEach((c) => c.classList.remove("active"));
      chip.classList.add("active");
      state.corpusFilter.enabled = chip.dataset.filter;
      localStorage.setItem("godot-coder-corpus-filter", JSON.stringify(state.corpusFilter));
      applyCorpusFilter();
    });
  });

  /* Toggle advanced sources visibility */
  const advToggle = $("#toggle-advanced-sources");
  const advBox = document.querySelector(".source-panel > .advanced-box");
  if (advToggle && advBox) {
    const updateAdvToggle = () => {
      const vis = state.advancedSourcesVisible;
      advBox.style.display = vis ? "" : "none";
      advToggle.classList.toggle("active", vis);
    };
    updateAdvToggle();
    advToggle.addEventListener("click", () => {
      state.advancedSourcesVisible = !state.advancedSourcesVisible;
      localStorage.setItem("godot-coder-advanced-sources", state.advancedSourcesVisible ? "visible" : "hidden");
      updateAdvToggle();
    });
  }

  /* Restore filter UI from localStorage on page load */
  (function restoreCorpusFilterUI() {
    const f = state.corpusFilter;
    const searchEl = $("#corpus-source-search");
    if (searchEl) searchEl.value = f.search || "";
    $$("#corpus-filter-chips .filter-chip").forEach((c) => {
      c.classList.toggle("active", c.dataset.filter === (f.enabled || "all"));
    });
  })();

  $("#benchmark-model").addEventListener("click", benchmarkModel);
  $("#stop-job").addEventListener("click", stopJob);
  $("#professional-audit").addEventListener("click", runProfessionalAudit);
  $("#professional-autotune").addEventListener("click", runAutotune);
  $("#professional-smoke").addEventListener("click", runSmoke50);
  $("#professional-preflight").addEventListener("click", refreshPreflight);
  $("#log-view-simple").addEventListener("click", () => setLogView("simple"));
  $("#log-view-technical").addEventListener("click", () => setLogView("technical"));
  $("#log-auto-follow").checked = state.logAutoFollow;
  $("#log-auto-follow").addEventListener("change", (event) => {
    state.logAutoFollow = event.target.checked;
    localStorage.setItem("godot-coder-log-auto-follow", String(state.logAutoFollow));
    if (state.logAutoFollow) {
      const terminal = $("#training-log");
      terminal.scrollTop = terminal.scrollHeight;
    }
  });
  ["#log-level-info", "#log-level-warning", "#log-level-error", "#log-filter-project", "#log-filter-phase"].forEach((selector) => {
    $(selector).addEventListener("change", () => renderLog(state.currentJob));
  });
  $("#training-log").addEventListener("scroll", (event) => {
    if (!state.logAutoFollow) return;
    const terminal = event.currentTarget;
    const distance = terminal.scrollHeight - terminal.scrollTop - terminal.clientHeight;
    if (distance > 32) {
      state.logAutoFollow = false;
      $("#log-auto-follow").checked = false;
      localStorage.setItem("godot-coder-log-auto-follow", "false");
    }
  });
  $("#clear-log-view").addEventListener("click", clearVisibleLog);
  $("#copy-log").addEventListener("click", () => navigator.clipboard.writeText(state.visibleLogText || $("#training-log").textContent).then(() => toast("Sichtbares Log kopiert.")));
  $("#export-log-text").addEventListener("click", () => exportJobLog("text"));
  $("#export-log-jsonl").addEventListener("click", () => exportJobLog("jsonl"));
  $("#file-search").addEventListener("input", renderFiles);
  $("#data-kind-filter").addEventListener("change", renderFiles);
  $("#data-editor").addEventListener("input", updateEditorStats);
  $("#data-editor").addEventListener("keydown", (event) => {
    if (event.key === "Tab") {
      event.preventDefault();
      const area = event.target;
      const start = area.selectionStart;
      area.value = `${area.value.slice(0, start)}    ${area.value.slice(area.selectionEnd)}`;
      area.selectionStart = area.selectionEnd = start + 4;
      updateEditorStats();
    }
    if (event.ctrlKey && event.key.toLowerCase() === "s") { event.preventDefault(); saveEditor(); }
  });
  $("#save-editor").addEventListener("click", saveEditor);
  $("#delete-editor").addEventListener("click", deleteCurrentFile);
  $("#validate-editor").addEventListener("click", () => validateCode($("#data-editor").value));
  $("#new-file").addEventListener("click", () => openNewFileModal("new"));
  $("#create-file").addEventListener("click", createFile);
  $$('[data-close-modal]').forEach((button) => button.addEventListener("click", () => $("#new-file-modal").hidden = true));
  $("#new-file-modal").addEventListener("click", (event) => { if (event.target === event.currentTarget) event.currentTarget.hidden = true; });
  $$(".prompt-chips button").forEach((button) => button.addEventListener("click", () => { $("#chat-input").value = button.dataset.prompt; $("#chat-input").focus(); }));
  [
    ["#max-tokens", "#tokens-output", (v) => v],
    ["#temperature", "#temperature-output", (v) => Number(v).toFixed(2)],
    ["#top-k", "#top-k-output", (v) => v],
  ].forEach(([inputSelector, outputSelector, formatter]) => {
    $(inputSelector).addEventListener("input", (event) => { $(outputSelector).textContent = formatter(event.target.value); });
  });
  window.addEventListener("beforeunload", (event) => {
    if (isEditorDirty()) { event.preventDefault(); event.returnValue = ""; }
  });
}

function startParticles() {
  const canvas = $("#ice-particles");
  const context = canvas.getContext("2d");
  const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
  let particles = [];
  function resize() {
    const ratio = Math.min(devicePixelRatio || 1, 2);
    canvas.width = innerWidth * ratio;
    canvas.height = innerHeight * ratio;
    canvas.style.width = `${innerWidth}px`;
    canvas.style.height = `${innerHeight}px`;
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    const count = reducedMotion ? 18 : Math.min(75, Math.floor(innerWidth / 22));
    particles = Array.from({ length: count }, () => ({
      x: Math.random() * innerWidth,
      y: Math.random() * innerHeight,
      r: Math.random() * 1.4 + .25,
      vx: (Math.random() - .5) * .08,
      vy: Math.random() * .16 + .035,
      a: Math.random() * .35 + .08,
    }));
  }
  function draw() {
    context.clearRect(0, 0, innerWidth, innerHeight);
    for (const p of particles) {
      p.x += p.vx; p.y += p.vy;
      if (p.y > innerHeight + 5) { p.y = -5; p.x = Math.random() * innerWidth; }
      if (p.x < -5) p.x = innerWidth + 5;
      if (p.x > innerWidth + 5) p.x = -5;
      context.beginPath();
      context.fillStyle = `rgba(139, 228, 255, ${p.a})`;
      context.arc(p.x, p.y, p.r, 0, Math.PI * 2);
      context.fill();
    }
    requestAnimationFrame(draw);
  }
  addEventListener("resize", resize);
  resize(); draw();
}

async function init() {
  bindEvents();
  setLogView(state.logView);
  startParticles();
  registerPwa();
  updateNetworkState();
  window.addEventListener("online", updateNetworkState);
  window.addEventListener("offline", updateNetworkState);
  await refreshAll();
  setInterval(pollJob, 900);
  setInterval(() => refreshRemote(false), 15000);
  setInterval(() => { if ($("#view-data")?.classList.contains("active") && !document.hidden) refreshDataCatalog(false); }, 2500);
  window.addEventListener("focus", () => { if ($("#view-data")?.classList.contains("active")) refreshDataCatalog(false); });
}

document.addEventListener("DOMContentLoaded", init);
