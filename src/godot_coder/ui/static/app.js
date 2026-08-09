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


const viewTitles = {
  chat: "Chat & Code",
  training: "Training Workspace",
  corpus: "Knowledge Building",
  data: "Data Lab",
  models: "Checkpoint Vault",
  remote: "Secure Remote Studio",
  system: "System & Runtime",
};

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
    if (showToast) toast("Studio data updated.");
  } catch (error) {
    toast(`Refresh failed: ${error.message}`, "error");
  }
}

function renderRuntime() {
  const o = state.overview;
  if (!o) return;
  const dot = $("#runtime-dot");
  const accelerator = o.rocm_available ? "ROCm" : o.cuda_available ? "CUDA" : o.mps_available ? "MPS" : null;
  dot.classList.toggle("online", Boolean(accelerator && o.godot));
  $("#runtime-label").textContent = accelerator ? `${accelerator} ready` : "CPU mode";
  $("#runtime-gpu").textContent = o.gpu?.name || "No GPU";
  $("#runtime-vram").textContent = o.gpu?.vram_gib ? `${o.gpu.vram_gib} GiB VRAM` : accelerator ? "Unified memory" : "CPU";
  $("#runtime-meter-fill").style.width = accelerator ? "100%" : "28%";
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
  chatSelect.innerHTML = options || '<option value="">No checkpoint available</option>';
  chatSelect.value = state.activeCheckpoint;
  resumeSelect.innerHTML = '<option value="">Start from random weights</option>' + options;
  updateActiveModelSummary();
  $("#generate-button").disabled = !state.activeCheckpoint;
}

function updateActiveModelSummary() {
  const item = state.checkpoints.find((checkpoint) => checkpoint.path === state.activeCheckpoint);
  $("#active-model-summary").textContent = item
    ? `${item.run} · ${item.kind.toUpperCase()} · ${item.size_mb} MB · ${formatDate(item.modified_at)}`
    : "Train a model first or copy your checkpoints into checkpoints/.";
}

function renderConfigs() {
  const select = $("#training-config");
  const prior = select.value;
  const generated = state.configs.filter((config) => config.profile_generated);
  const profiles = state.configs.filter((config) => config.profile_id && !config.profile_generated);
  const legacy = state.configs.filter((config) => !config.profile_id);
  select.innerHTML = `${generated.length ? `<optgroup label="Hardware recommendation">${generated.map((config) => `<option value="${escapeHtml(config.path)}">${escapeHtml(config.profile_title || config.name)}</option>`).join("")}</optgroup>` : ""}${profiles.length ? `<optgroup label="Recommended profiles">${profiles.map((config) => `<option value="${escapeHtml(config.path)}">${escapeHtml(config.profile_title || config.name)}</option>`).join("")}</optgroup>` : ""}${legacy.length ? `<optgroup label="Learning and legacy configurations">${legacy.map((config) => `<option value="${escapeHtml(config.path)}">${escapeHtml(config.name)}</option>`).join("")}</optgroup>` : ""}`;
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
  $("#metric-context").textContent = `${formatNumber(config.max_seq_len)} tokens`;
  const stepsLabel = $("#metric-steps").closest(".metric-card")?.querySelector("span");
  if (stepsLabel) stepsLabel.textContent = "Training budget";
  $("#metric-steps").textContent = config.max_steps ? `${formatNumber(config.max_steps)} steps` : config.target_dataset_passes ? `${config.target_dataset_passes} dataset passes` : config.max_tokens ? `${formatCompact(config.max_tokens)} tokens` : "automatic";
  $("#metric-batch").textContent = config.tokens_per_optimizer_step ? `${formatNumber(config.tokens_per_optimizer_step)} Tok/Step` : "–";
  applyJobMetricOverride(state.currentJob);
  renderTrainingProfiles();
}

function friendlyJobLabel(kind) {
  return workflowJobLabels[kind] || String(kind || "Studio task").replaceAll("-", " ");
}

const ProgressTools = window.GodotCoderProgress;

const projectStatusLabels = {
  waiting: "waiting",
  running: "running",
  passed: "passed",
  passed_with_warnings: "passed with warnings",
  failed: "failed",
  quarantined: "quarantined",
  disabled: "disabled",
  skipped: "skipped",
  completed: "finished",
  stopped: "stopped",
};

function projectStatusLabel(value) {
  return projectStatusLabels[value] || value || "waiting";
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
    grid.innerHTML = '<div class="empty-state compact">No private projects imported yet.</div>';
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
    `).join("") : `<li><span>Project detected</span><strong>${escapeHtml(project.validation_status || "waiting")}</strong></li>`;
    return `<details class="local-project-card status-${escapeHtml(status)}" ${status === "running" ? "open" : ""}>
      <summary>
        <span class="project-card-copy"><strong title="${escapeHtml(project.project_name || "Project")}">${escapeHtml(project.project_name || "Project")}</strong><small>${formatNumber(checked)}/${formatNumber(scripts)} scripts checked</small></span>
        <span class="project-status-badge">${escapeHtml(projectStatusLabel(status))}</span>
      </summary>
      <div class="project-card-details">
        <ul class="project-phase-list">${phaseRows}</ul>
        <dl class="project-card-stats">
          <div><dt>Scripts found</dt><dd>${formatNumber(scripts)}</dd></div>
          <div><dt>Trainable</dt><dd>${formatNumber(trainable)}</dd></div>
          <div><dt>Warnings</dt><dd>${formatNumber(warnings)}</dd></div>
          <div><dt>Errors</dt><dd>${formatNumber(failed)}</dd></div>
          <div><dt>Quarantined</dt><dd>${formatNumber(quarantined)}</dd></div>
          <div><dt>Add-ons excluded</dt><dd>${formatNumber(project.addon_files || 0)}</dd></div>
          <div><dt>Cache/Import excluded</dt><dd>${formatNumber(project.generated_files || 0)}</dd></div>
          <div><dt>Import enabled</dt><dd>${project.enabled_for_training ? "Yes" : "No"}</dd></div>
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
  setText("#local-progress-title", `${index}/${total} projects · ${percent} %`);
  setText("#local-progress-percent", `${percent} %`);
  $("#local-progress-bar").style.width = `${percent}%`;
  setText("#local-current-project", progress.project_name || (job.status === "completed" ? "Import finished" : "Planning running"), progress.project_name || "");
  setText("#local-current-phase", ProgressTools.phaseLabel(progress.phase));
  const currentFile = progress.current_file || progress.message || "–";
  setText("#local-current-file", ProgressTools.shortenPath(currentFile), currentFile);
  const fileIndex = progress.file_index || 0;
  const fileTotal = progress.file_total || progress.scripts_found || 0;
  setText("#local-file-progress", `${formatNumber(fileIndex)}/${formatNumber(fileTotal)} · ${formatNumber(Math.max(0, fileTotal - fileIndex))} remaining`);
  setText("#local-passed", formatNumber(progress.passed || 0));
  setText("#local-warnings", formatNumber(progress.warnings || 0));
  setText("#local-failed", formatNumber(progress.failed || 0));
  setText("#local-quarantined", formatNumber(progress.quarantined || 0));
  setText("#local-addons", formatNumber(progress.addon_files || 0));
  setText("#local-generated", formatNumber(progress.generated_files || 0));
  setText("#local-elapsed", ProgressTools.formatDuration(job.elapsed_seconds ?? progress.elapsed_seconds));
  setText("#local-eta", job.status === "completed" ? "finished" : ProgressTools.formatEta(progress));
  setText("#local-next-project", progress.next_project || (job.status === "completed" ? "No next project" : "being determined"), progress.next_project || "");
  setText("#local-next-detail", progress.next_project ? `${formatNumber(progress.next_project_scripts || 0)} detected scripts` : (progress.next_phase ? ProgressTools.phaseLabel(progress.next_phase) : "–"));
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
  projectSelect.innerHTML = '<option value="">All projects</option>' + projects.map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`).join("");
  phaseSelect.innerHTML = '<option value="">All phases</option>' + phases.map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(ProgressTools.phaseLabel(value))}</option>`).join("");
  if (projects.includes(priorProject)) projectSelect.value = priorProject;
  if (phases.includes(priorPhase)) phaseSelect.value = priorPhase;
}

function renderLog(job) {
  const terminal = $("#training-log");
  if (!terminal) return;
  const distanceBefore = terminal.scrollHeight - terminal.scrollTop - terminal.clientHeight;
  if (!job) {
    terminal.textContent = "Studio ready.";
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
    const timestamp = entry.timestamp ? (state.logView === "technical" ? entry.timestamp : new Date(entry.timestamp).toLocaleTimeString("en-US")) : "";
    const context = [entry.project, entry.phase ? ProgressTools.phaseLabel(entry.phase) : ""].filter(Boolean).join(" · ");
    const prefix = [timestamp, entry.level?.toUpperCase(), context].filter(Boolean).map((value) => `[${value}]`).join(" ");
    return `${prefix}${prefix ? " " : ""}${entry.text}`;
  });
  terminal.textContent = lines.join("\n") || "No visible log entries for the current filters.";
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
  toast("Only the current log view was cleared. Saved reports remain available.");
}

function exportJobLog(format) {
  const job = state.currentJob;
  if (!job) return toast("No job log available.", "error");
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

function setWorkflowStep(name, { completed = false, active = false, label = "Pending" } = {}) {
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
  setWorkflowStep("audit", { completed: auditReady, active: activeKind === "corpus-audit", label: activeKind === "corpus-audit" ? "Running" : auditReady ? "Done" : "Pending" });
  setWorkflowStep("autotune", { completed: autotuneReady, active: activeKind === "hardware-autotune", label: activeKind === "hardware-autotune" ? "Running" : autotuneReady ? "Done" : "Pending" });
  setWorkflowStep("smoke", { completed: smokeReady, active: activeKind === "training-smoke-50", label: activeKind === "training-smoke-50" ? "Running" : smokeReady ? "Passed" : "Pending" });
  setWorkflowStep("preflight", { completed: preflightStatus === "green", active: false, label: preflightStatus === "green" ? "Approved" : preflightStatus === "yellow" ? "Warning" : preflightStatus === "red" ? "Blocked" : "Pending" });

  // Auto-advance hint: show what to do next when the current step completes.
  // Never auto-start training — the user must click "Start training" explicitly.
  const canStartTraining = preflightStatus === "green" || preflightStatus === "yellow";
  $("#start-training").disabled = !canStartTraining;
  if (!active && !canStartTraining) {
    const nextStep = !auditReady ? "audit" : !autotuneReady ? "autotune" : !smokeReady ? "smoke" : "preflight";
    const names = { audit: "Check data", autotune: "Set up hardware", smoke: "50-step probe run", preflight: "Preflight" };
    $("#start-training").title = `First: ${names[nextStep] || "complete the preflight"}`;
  } else if (canStartTraining) {
    $("#start-training").title = "Start training with this configuration";
  }
}

function applyJobMetricOverride(job) {
  if (!job || job.kind !== "training-smoke-50") return;
  const stepsLabel = $("#metric-steps").closest(".metric-card")?.querySelector("span");
  if (stepsLabel) stepsLabel.textContent = "Probe run";
  const summary = parseJobJson(job, "TRAINING_SUMMARY_JSON=");
  const runHeader = parseJobJson(job, "RUN_HEADER_JSON=");
  const steps = Number(summary?.run_steps_completed || job.max_steps || 50);
  const passes = Number(summary?.equivalent_dataset_passes_seen ?? runHeader?.token_accounting?.equivalent_dataset_passes_planned);
  $("#metric-steps").textContent = Number.isFinite(passes) ? `${formatNumber(steps)} steps · ${passes.toFixed(2)}× data` : `${formatNumber(steps)} smoke steps`;
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
  $("#professional-run-title").textContent = `${friendlyJobLabel(job.kind)} · ${job.status === "completed" ? "completed" : job.status === "failed" ? "failed" : job.status === "stopped" ? "stopped" : "running"}`;
  const summary = parseJobJson(job, "TRAINING_SUMMARY_JSON=");
  if (summary) {
    const speed = Math.round(summary.average_training_tokens_per_second || summary.average_tokens_per_second || 0);
    const peak = Number(summary.peak_vram_reserved_gib || 0).toFixed(2);
    const val = summary.final_val_loss == null ? "–" : Number(summary.final_val_loss).toFixed(4);
    $("#professional-run-detail").textContent = `${formatNumber(summary.run_steps_completed || 0)} steps · ${formatNumber(speed)} tok/s · ${peak} GiB peak · Val ${val}`;
  } else {
    $("#professional-run-detail").textContent = job.step && job.max_steps ? `Step ${formatNumber(job.step)} of ${formatNumber(job.max_steps)}` : "Status updates live.";
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
    target.innerHTML = '<div class="empty-state">No main profiles found yet.</div>';
    return;
  }
  target.innerHTML = profiles.map((config) => {
    const probe = profileProbeEntry(config.profile_id);
    const result = probe?.configured_result;
    const statusClass = result?.status === "pass" ? "passed" : result ? "failed" : "";
    const probeCopy = result?.status === "pass"
      ? `<div class="profile-probe-result pass">✓ Probe run passed · ${result.peak_reserved_gib ?? "?"} GiB peak · ${formatNumber(Math.round(result.tokens_per_second || 0))} tok/s</div>`
      : result
        ? `<div class="profile-probe-result fail">× ${escapeHtml(result.status)} · ${escapeHtml(result.error || "Profile did not fit")}</div>`
        : '<div class="profile-probe-result">Not measured on this GPU yet.</div>';
    return `<article class="profile-card ${config.path === selectedPath ? "selected" : ""} ${config.profile_recommended ? "recommended" : ""} ${statusClass}" data-profile-config="${escapeHtml(config.path)}">
      <div class="profile-title">${escapeHtml(config.profile_title || config.name)}</div>
      <div class="profile-method">${escapeHtml(config.profile_method || "Training")}</div>
      <div class="profile-description">${escapeHtml(config.profile_description || "")}</div>
      <div class="profile-stats">
        <div><span>Parameter</span><strong>${formatCompact(config.parameters)}</strong></div>
        <div><span>Context</span><strong>${formatNumber(config.max_seq_len)}</strong></div>
        <div><span>AMP-Compute</span><strong>${escapeHtml(config.dtype)}</strong></div>
        <div><span>Memory</span><strong>${config.gradient_checkpointing ? "Checkpointing" : "Direct"}</strong></div>
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
    summary.innerHTML = `<strong>Local hardware recommendation: ${escapeHtml(tuned.matrix_label || "Autotuned")}</strong><br>Batch ${formatNumber(tuned.batch_size || 0)} · Context ${formatNumber(tuned.context || 0)} · ${peak} GiB peak · ${speed} tok/s. The three cards above remain capacity comparisons.`;
    return;
  }
  if (!state.hardwareProbe) {
    summary.textContent = "No hardware probe run yet. It measures all three profiles with real forward, backward and optimizer steps.";
    return;
  }
  const recommendation = state.hardwareProbe.recommendation || {};
  const created = formatDate(state.hardwareProbe.created_at);
  summary.innerHTML = recommendation.profile_id
    ? `<strong>Capacity recommendation: ${escapeHtml(recommendation.profile_title || recommendation.profile_id)}</strong><br>${escapeHtml(recommendation.reason || "")} · measured ${created}`
    : `<strong>No profile safely recommended.</strong><br>${escapeHtml(recommendation.reason || "Check the live log.")} · measured ${created}`;
}

function renderTrainingReports() {
  const target = $("#training-report-list");
  if (!target) return;
  const reports = state.trainingReports || [];
  if (!reports.length) {
    target.innerHTML = '<div class="empty-state">No training report yet. The next run writes a final report automatically.</div>';
    return;
  }
  target.innerHTML = reports.slice(0, 6).map((report) => {
    const profile = report.profile?.title || report.run_id || "Training";
    const bestLoss = report.best_val_loss == null ? "–" : Number(report.best_val_loss).toFixed(4);
    const peak = report.peak_vram_reserved_gib == null ? "CPU" : `${Number(report.peak_vram_reserved_gib).toFixed(2)} GiB`;
    return `<article class="training-report-card ${report.status === "failed" ? "failed" : ""}">
      <div class="report-name"><span>Run</span><strong>${escapeHtml(profile)}</strong></div>
      <div><span>Status</span><strong>${escapeHtml(report.status || "?")}</strong></div>
      <div><span>Tokens seen</span><strong>${formatCompact(report.cumulative_tokens_seen)}</strong></div>
      <div><span>Avg. throughput</span><strong>${formatNumber(Math.round(report.average_training_tokens_per_second || report.average_tokens_per_second || 0))} Tok/s</strong></div>
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
  light.querySelector("strong").textContent = status === "green" ? "Ready" : status === "yellow" ? "With warnings" : "Blocked";
  const blockers = report?.blockers || [];
  const warnings = report?.warnings || [];
  const modeLabel = report?.mode === "smoke" ? "Smoke test" : "Long training";
  $("#preflight-summary").innerHTML = blockers.length
    ? `<strong>${escapeHtml(modeLabel)} blockiert · ${blockers.length} reason(s):</strong> ${blockers.map(escapeHtml).join(" · ")}`
    : warnings.length
      ? `<strong>${escapeHtml(modeLabel)} technically possible, but check:</strong> ${warnings.map(escapeHtml).join(" · ")}`
      : report ? `<strong>${escapeHtml(modeLabel)} approved.</strong> All mandatory checks are current.` : "No complete preflight available yet.";
  const audit = report?.audit || {};
  const validation = report?.validation || {};
  const data = report?.dataset || {};
  const freshness = report?.freshness || {};
  const plan = report?.training_plan || {};
  const hardware = report?.hardware_recommendation || {};
  $("#preflight-details").innerHTML = `
    Mode / Profile: ${escapeHtml(modeLabel)} / ${escapeHtml(report?.profile_id || "–")}<br>
    Project validation: ${formatNumber(validation.prepared || 0)} prepared · ${formatNumber(validation.failed || 0)} hard errors · ${formatNumber(validation.context_warnings || 0)} context warnings<br>
    Accepted files: ${formatNumber((audit.accepted || 0) + (audit.warning || 0))}<br>
    Projects train/val/test: ${formatNumber(audit.train_projects || 0)} / ${formatNumber(audit.val_projects || 0)} / ${formatNumber(audit.test_projects || 0)}<br>
    Parser rate: ${audit.parser_pass_rate == null ? "–" : Math.round(audit.parser_pass_rate * 100) + " %"}<br>
    Training tokens: ${formatNumber(data.train_tokens || 0)} / recommended at least ${formatNumber(plan.minimum_recommended_tokens || 0)}<br>
    Token stream: ${freshness.stale ? "STALE" : data.manifest_path ? "current" : "missing"}${freshness.newest_input ? ` · newest input ${escapeHtml(freshness.newest_input)}` : ""}<br>
    Planned passes: ${plan.dataset_passes ?? "–"}<br>
    Hardware profile: ${escapeHtml(hardware.matrix_label || hardware.profile_title || "not chosen yet")}
  `;
  renderWorkflowSteps(state.currentJob);
}


function renderManifest(manifest) {
  const target = $("#manifest-mini");
  if (!manifest) {
    target.textContent = "No processed dataset yet.";
    return;
  }
  target.innerHTML = `
    <strong>${formatNumber(manifest.train_tokens)} train</strong> + ${formatNumber(manifest.val_tokens)} validation tokens<br>
    ${formatNumber(manifest.train_files?.length)} training files · ${formatNumber(manifest.val_files?.length)} validation files<br>
    Vocabulary: ${formatNumber(manifest.vocab_size)} tokens
  `;
}


function renderCurriculum(status) {
  const summary = $("#curriculum-summary");
  const list = $("#curriculum-list");
  const validation = $("#curriculum-validation");
  if (!status?.manifest) {
    summary.textContent = "Curriculum not created yet. Step 1 creates 192 controlled lessons.";
    list.innerHTML = "";
    validation.textContent = "Godot validation pending.";
    return;
  }
  const manifest = status.manifest;
  const splits = manifest.split_counts || {};
  summary.innerHTML = `<strong>${formatNumber(manifest.total_lessons)} lessons</strong><br>${formatNumber(splits.train)} Train · ${formatNumber(splits.val)} Validation · ${formatNumber(splits.test)} Test`;
  const topicCounts = manifest.topic_counts || {};
  const maxCount = Math.max(1, ...Object.values(topicCounts));
  list.innerHTML = (manifest.topics || []).map((topic) => {
    const count = topicCounts[topic.slug] || 0;
    const percent = Math.round((count / maxCount) * 100);
    return `<div><span>${escapeHtml(topic.label)} · ${formatNumber(count)}</span><i style="--p: ${percent}%"></i></div>`;
  }).join("");
  if (status.validation) {
    const rate = Math.round((status.validation.pass_rate || 0) * 100);
    validation.textContent = `Godot: ${formatNumber(status.validation.passed)}/${formatNumber(status.validation.total)} passed · ${rate} %`;
  } else {
    validation.textContent = "Godot validation pending.";
  }
}



function renderLocalSources(local) {
  const statusElement = $("#local-source-status");
  const grid = $("#local-project-grid");
  if (!statusElement || !grid) return;
  const items = local?.inbox_items || [];
  const report = local?.report;
  const summary = report?.summary;
  statusElement.innerHTML = `<strong>${items.length} file(s)/folder(s) in the import folder</strong><span>${escapeHtml(local?.inbox || "data/local_sources/inbox")}</span>${summary ? `<small>${summary.projects} projects checked · ${summary.enabled} enabled · ${summary.quarantined} quarantined · ${summary.failed || 0} failed · ~${formatNumber(summary.estimated_bpe_tokens)} tokens</small>` : ""}`;
  renderLocalProjectCards(state.currentJob?.kind === "local-source-import" ? (state.currentJob.progress_state?.projects || []) : []);
}

async function openLocalSourceInbox() {
  try {
    const result = await api("/api/corpus/local/open", { method: "POST", body: "{}" });
    toast(`Import folder opened: ${result.path}`);
  } catch (error) { toast(error.message, "error"); }
}

async function importLocalSources() {
  const confirmed = Boolean($("#confirm-local-ownership")?.checked);
  if (!confirmed) return toast("First confirm that you are allowed to use the source code.", "error");
  // Forward the fast-import toggles; the server maps them to env vars.
  const body = { confirm_owned: true };
  if ($("#opt-skip-project-import")?.checked) body.skip_project_import = true;
  if ($("#opt-fast-static")?.checked) body.fast_static = true;
  if ($("#opt-tighten-abort")?.checked) body.error_abort_threshold = 60;
  try {
    const job = await api("/api/jobs/corpus/local-import", { method: "POST", body: JSON.stringify(body) });
    state.currentJob = job;
    renderJob(job);
    toast("Private projects are checked and imported safely.");
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
    $("#corpus-source-grid").innerHTML = '<div class="empty-state">No sources configured.</div>';
  } else {
  $("#corpus-source-grid").innerHTML = allSources.map((source) => {
    const download = downloads.get(source.id);
    const ref = source.ref || source.branch || "main";
    const localSize = download?.size_mb == null ? "local" : `${download.size_mb} MB local`;
    const downloadState = download?.needs_refresh ? "Ref changed – reload" : download?.downloaded ? localSize : "not loaded yet";
    const isPrivate = source.catalog_tier === "local-private";
    const tier = source.catalog_tier === "official" ? "Official" : source.catalog_tier === "verified-community" ? "Verified community" : isPrivate ? "Private · local" : "Own source";
    const expansion = source.expansion_tier === "core-5m" ? "5M candidate" : source.expansion_tier === "extended-20m" ? "20M expansion" : "";
    const estimate = Number(source.estimated_unique_tokens || 0);
    const licenseState = isPrivate
      ? (download?.license_verified ? "✓ Ownership confirmed · do not redistribute" : "⚠ Ownership confirmation missing")
      : download?.downloaded ? (download?.license_verified ? `✓ License local: ${download.license_file || source.license}` : "⚠ License not confirmed") : `Declared: ${source.license}`;
    return `<label class="source-card ${source.enabled ? "enabled" : ""}" data-source-id="${escapeHtml(source.id)}" data-source-title="${escapeHtml(source.title || "")}" data-source-enabled="${source.enabled}">
      <input type="checkbox" data-corpus-source="${escapeHtml(source.id)}" ${source.enabled ? "checked" : ""}>
      <div><div class="source-title">${escapeHtml(source.title)}</div>
      <div class="source-description">${escapeHtml(source.description || "Godot data source")}</div>
      <div class="source-meta"><span class="${download?.downloaded && !download?.license_verified ? "license-warning" : "license-ok"}">${escapeHtml(licenseState)}</span><span>${escapeHtml(tier)}</span>${expansion ? `<span>${escapeHtml(expansion)}</span>` : ""}${estimate ? `<span>Estimate ~${formatNumber(estimate)} tokens</span>` : ""}<span>${escapeHtml(source.kind === "godot_projects" ? "GDScript projects" : "Documentation examples")}</span><span>Ref: ${escapeHtml(ref)}</span><span>${escapeHtml(downloadState)}</span></div></div>
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
  const labels = ["Sources local", "Corpus scanned", "Godot checked", "Audit done", "BPE ready", "Tokens prepared", "Task data prepared"];
  $("#corpus-readiness").innerHTML = `<strong>${percent} %</strong><span>${completed ? labels[completed - 1] : "Not started yet"}</span>`;

  const summary = status?.manifest?.summary || {};
  const validation = status?.validation || {};
  const processedManifest = status?.processed || {};
  $("#corpus-stat-grid").innerHTML = [
    ["Sources", `${enabled.length}`],
    ["Examples", formatNumber(summary.records || 0)],
    ["Godot passed", formatNumber(validation.passed || 0)],
    ["Context warnings", formatNumber(validation.context_warnings || 0)],
    ["Hard exclusions", formatNumber(validation.failed || 0)],
    ["Train tokens", formatNumber(processedManifest.train_tokens || 0)],
    ["Vocabulary", formatNumber(status?.tokenizer?.vocab_size || 0)],
    ["Audit accepted", formatNumber((status?.audit?.summary?.accepted || 0) + (status?.audit?.summary?.warning || 0))],
    ["Quarantined", formatNumber(status?.audit?.summary?.quarantine || 0)],
    ["Tasks", formatNumber(status?.instructions?.total_tasks || 0)],
  ].map(([label, value]) => `<div class="corpus-stat"><span>${label}</span><strong>${value}</strong></div>`).join("");

  const next = !enabled.length ? "Enable at least one allowed source and save the selection."
    : !downloaded ? "Next step: download the sources. This can take a while depending on your connection."
    : !scanned ? "Next step: scan the sources and remove duplicates."
    : !validated ? "Next step: start the project-based Godot validation. Only clearly broken or incompatible scripts are hard-excluded."
    : !audited ? "Next step: run the professional corpus audit."
    : !tokenizer ? "Next step: train the code tokenizer on the audited files."
    : !processed ? "Next step: prepare fixed train, validation and test token streams."
    : !instructions ? "Optional next step: generate checked task data for the later instruction-tuning phase."
    : "Base and task data are prepared. For a serious 91M run, the unique corpus should first reach several million tokens.";
  $("#corpus-next-action").textContent = next;
  const filterBox = $("#corpus-filter-summary");
  if (filterBox) {
    const validationReasons = Object.entries(validation.classifications || {}).filter(([, count]) => Number(count) > 0).sort((a, b) => Number(b[1]) - Number(a[1])).slice(0, 5);
    const auditReasons = Object.entries(status?.audit?.reason_counts || {}).filter(([, count]) => Number(count) > 0).sort((a, b) => Number(b[1]) - Number(a[1])).slice(0, 5);
    const sourceCount = (validation.source_results || []).length;
    filterBox.innerHTML = validationReasons.length || auditReasons.length
      ? `<strong>Transparency:</strong> ${sourceCount ? `${formatNumber(sourceCount)} sources evaluated. ` : ""}${validationReasons.length ? `Validation: ${validationReasons.map(([reason, count]) => `${escapeHtml(reason)} ${formatNumber(count)}`).join(" · ")}. ` : ""}${auditReasons.length ? `Audit: ${auditReasons.map(([reason, count]) => `${escapeHtml(reason)} ${formatNumber(count)}`).join(" · ")}.` : ""}`
      : "Exclusion reasons appear after validation and audit.";
  }
  const plan = status?.scale_plan;
  const goal = plan?.targets?.find((item) => item.target_unique_tokens === 20000000);
  if ($("#corpus-scale-goal")) {
    const progress = goal ? Math.round((goal.progress || 0) * 1000) / 10 : 0;
    $("#corpus-scale-goal").innerHTML = `<span>20M corpus goal</span><strong>${formatNumber(plan?.current_train_tokens || 0)} / 20.000.000 unique tokens</strong><i style="--p:${Math.min(100, progress)}%"></i><small>${goal ? `${formatNumber(goal.missing_unique_tokens)} missing · ~${goal.estimated_training_hours ?? "?"} h for 4 passes per current autotune` : "No planning yet"}</small>`;
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
  if (!candidates.length) return toast("No verified expansion sources found.", "error");
  await saveCorpusSources();
  toast(`${candidates.length} verified expansion sources enabled.`);
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
  if (!candidates.length) return toast("No matching expansion sources found.", "error");
  await saveCorpusSources();
  const estimated = candidates.reduce((sum, source) => sum + Number(source.estimated_unique_tokens || 0), 0);
  toast(`${candidates.length} expansion sources enabled · catalog estimate ~${formatNumber(estimated)} tokens. The real value is only known after audit and tokenization.`, "info", 7500);
}

async function saveCorpusSources() {
  const sources = structuredClone(state.corpus?.registry?.sources || []);
  const checks = new Map($$('[data-corpus-source]').map((box) => [box.dataset.corpusSource, box.checked]));
  for (const source of sources) source.enabled = Boolean(checks.get(source.id));
  try {
    const registry = await api("/api/corpus/sources", { method: "PUT", body: JSON.stringify({ sources }) });
    state.corpus.registry = registry;
    renderCorpus(state.corpus);
    toast("Source selection saved.");
  } catch (error) { toast(error.message, "error"); }
}

async function addCustomSource() {
  const id = $("#custom-source-id").value.trim().toLowerCase();
  const url = $("#custom-source-url").value.trim();
  const ref = $("#custom-source-ref").value.trim() || "main";
  if (!id || !url) return toast("Short name and Git URL are required.", "error");
  const sources = structuredClone(state.corpus?.registry?.sources || []);
  sources.push({
    id, title: id.replaceAll("-", " "), description: "Custom allowed source",
    url, branch: ref, ref, kind: $("#custom-source-kind").value,
    license: $("#custom-source-license").value, attribution: "Custom source contributors",
    enabled: true, beginner_recommended: false,
  });
  try {
    const registry = await api("/api/corpus/sources", { method: "PUT", body: JSON.stringify({ sources }) });
    state.corpus.registry = registry;
    renderCorpus(state.corpus);
    $("#custom-source-id").value = ""; $("#custom-source-url").value = ""; $("#custom-source-ref").value = "";
    toast("Own source added. Check license and attribution before any publication.");
  } catch (error) { toast(error.message, "error"); }
}

async function runProfessionalAudit() {
  return startCorpusJob("/api/jobs/corpus/audit", "Professional data audit started.");
}

async function runAutotune() {
  try {
    const job = await api("/api/jobs/hardware/autotune", { method: "POST", body: "{}" });
    state.currentJob = job; renderJob(job); setView("training"); toast("Hardware autotuner started. Each test runs isolated.");
  } catch (error) { toast(error.message, "error"); }
}

async function runSmoke50() {
  const config = $("#training-config").value;
  try {
    const preflight = await api(`/api/preflight?config=${encodeURIComponent(config)}&mode=smoke`);
    state.preflight = preflight; renderPreflight(preflight);
    if (!preflight.can_start) return toast("The smoke test is blocked by the preflight.", "error", 6500);
    const job = await api("/api/jobs/train-smoke", { method: "POST", body: JSON.stringify({ config, resume: null }) });
    state.currentJob = job; renderJob(job); toast("50-step probe run started.");
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
      const msg = query ? 'No sources found for "' + f.search + '".' : "No sources in the current filter.";
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
    toast(report.status === "green" ? "Night training approved." : "Preflight updated.", report.status === "red" ? "error" : "info");
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
  $("#file-count").textContent = `${formatNumber(files.length)} entries${activeTokens ? ` · ${formatNumber(activeTokens)} tokens` : ""}`;
  $("#file-list").innerHTML = files.map((file) => {
    const openPath = file.storage_path || "";
    const detail = [file.kind === "training" ? file.split?.toUpperCase() : file.kind === "instruction" ? `${formatNumber(file.tasks || 0)} tasks` : "Raw file", file.status === "pending" ? "not prepared yet" : ""].filter(Boolean).join(" · ");
    const token = file.tokens != null ? `${formatNumber(file.tokens)} T` : file.kind === "raw" ? "RAW" : "";
    return `<button class="file-item ${state.currentFile === openPath ? "active" : ""} ${file.status === "pending" ? "pending" : ""}" data-file="${escapeHtml(openPath)}" ${openPath ? "" : "disabled"} title="${escapeHtml(file.path)}">
      <span class="file-icon">‹›</span><span class="file-main"><span class="file-name">${escapeHtml(file.path.replace(/^training\//, ""))}</span><span class="file-detail">${escapeHtml(detail)}</span></span><span class="token-badge">${escapeHtml(token)}</span>
    </button>`;
  }).join("") || '<div class="empty-state">No data found for this filter.</div>';
  $$(".file-item[data-file]").forEach((button) => button.addEventListener("click", () => { if (button.dataset.file) openFile(button.dataset.file); }));
}

function renderDataCatalogSummary() {
  const catalog = state.dataCatalog;
  const summary = catalog?.summary || {};
  $("#dataset-token-count").textContent = formatNumber(summary.train_tokens || 0);
  $("#data-token-breakdown").innerHTML = [
    ["Train", summary.train_tokens], ["Validation", summary.val_tokens], ["Test", summary.test_tokens],
    ["Active documents", summary.training_documents], ["New/pending", summary.pending_documents], ["Tasks", summary.instruction_tasks],
  ].map(([label, value]) => `<div><span>${label}</span><strong>${formatNumber(value || 0)}</strong></div>`).join("");
  const live = $("#data-live-status");
  const stateBox = $("#data-catalog-state");
  if (!catalog?.manifest) {
    live.textContent = "Live · raw files visible · no active token stream yet";
    stateBox.textContent = "After changes, prepare audit/tokenizer and training data first.";
    stateBox.classList.remove("stale");
    return;
  }
  const path = catalog.manifest.manifest_path || "manifest.json";
  live.textContent = catalog.stale ? "Live · source data changed · token stream is stale" : `Live · ${formatNumber(summary.entries)} entries in sync`;
  live.classList.toggle("stale", Boolean(catalog.stale));
  stateBox.textContent = catalog.stale
    ? "New or deleted raw data is already visible. The displayed active tokens still come from the last prepared manifest. Re-run the pipeline from the affected step."
    : `Active dataset: ${path}. All ${formatNumber(summary.total_tokens || 0)} prepared train/validation/test tokens are listed.`;
  stateBox.classList.toggle("stale", Boolean(catalog.stale));
}

async function openFile(path) {
  if (isEditorDirty() && !confirm("Discard unsaved changes?")) return;
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
    $("#editor-meta").textContent = `${new Blob([data.content]).size} Bytes · UTF-8 · ${data.editable ? "editable" : "read only"}`;
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
  $("#editor-path").textContent = "No file open";
  $("#editor-meta").textContent = "Select a file on the left.";
  updateEditorStats();
}

function isEditorDirty() { return state.currentFile && !$("#data-editor").disabled && $("#data-editor").value !== state.currentFileOriginal; }

function updateEditorStats() {
  const content = $("#data-editor").value;
  $("#editor-lines").textContent = `${content ? content.split("\n").length : 0} lines`;
  $("#editor-save-state").textContent = isEditorDirty() ? "Unsaved" : $("#data-editor").disabled && state.currentFile ? "Read only" : "Saved";
}

async function saveEditor() {
  if (!state.currentFile || $("#data-editor").disabled) return;
  try {
    const content = $("#data-editor").value;
    const result = await api("/api/data/file", { method: "PUT", body: JSON.stringify({ path: state.currentFile, content }) });
    state.currentFileOriginal = content;
    updateEditorStats();
    toast(result.backup ? "File saved; backup created." : "File saved.");
    await refreshDataCatalog(true);
  } catch (error) { toast(`Saving failed: ${error.message}`, "error"); }
}

async function deleteCurrentFile() {
  if (!state.currentFile || $("#delete-editor").disabled) return;
  const path = state.currentFile;
  if (!confirm(`Really delete the file?\n\n${path}\n\nA backup is created before deleting. The prepared token stream is then considered stale.`)) return;
  try {
    const result = await api(`/api/data/file?path=${encodeURIComponent(path)}`, { method: "DELETE" });
    resetDataEditor();
    await refreshDataCatalog(true);
    toast(`File deleted. Backup: ${result.backup}`);
  } catch (error) { toast(`Deleting failed: ${error.message}`, "error", 6500); }
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
      <div class="message-meta">${role === "user" ? "Your prompt" : "Godot Coder · local"}</div>
      ${code ? `<pre class="message-code">${escapeHtml(content)}</pre>` : `<p>${escapeHtml(content).replaceAll("\n", "<br>")}</p>`}
  ${options.loading ? '<div class="message-meta loading-dots" style="margin-top:9px">Model working</div>' : ""}
  ${code && !options.skipTools ? '<div class="message-tools"><button data-copy-message>Copy code</button><button data-validate-message>Check with Godot</button></div>' : ""}
    </div>
  `;
  $("#chat-feed").append(article);
  $("#chat-feed").scrollTop = $("#chat-feed").scrollHeight;
  $("[data-copy-message]", article)?.addEventListener("click", () => navigator.clipboard.writeText(content).then(() => toast("Code copied.")));
  $("[data-validate-message]", article)?.addEventListener("click", () => validateCode(content));
  return article;
}

async function generate() {
  const prompt = $("#chat-input").value;
  if (!prompt.trim()) return toast("Enter a prompt first.", "error");
  if (!state.activeCheckpoint) return toast("No checkpoint selected.", "error");
  addMessage("user", prompt, { code: true });
  const loading = addMessage("assistant", "", { loading: true });
  $("#generate-button").disabled = true;
  try {
    // Use streaming SSE endpoint
    const useTaskFormat = $("#task-format")?.checked !== false;
    const modelPrompt = useTaskFormat
      ? `# file: chat/generated\n# task: ${prompt}\n`
      : prompt;
    const response = await fetch("/api/chat/generate-stream", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        checkpoint: state.activeCheckpoint,
        prompt: modelPrompt,
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
          if (parsed.error) { pre.textContent += `\n[Error: ${parsed.error}]`; break; }
        } catch {}
      }
      $("#chat-feed").scrollTop = $("#chat-feed").scrollHeight;
    }
    let result = pre.textContent;
    if (!result.trim()) {
      // The model hit its end-of-sequence token immediately. Show a hint
      // instead of an empty code block so the chat does not look broken.
      result = "# The model returned an empty completion.\n# This usually means the checkpoint is undertrained for this kind of prompt.\n# Try a smaller task, rephrase it, or continue training first.";
      pre.textContent = result;
    }
    state.lastPrompt = prompt;
    state.lastGenerated = result;
    setupMessageTools(msg, result);
    $("#validate-last").disabled = false;
    $("#save-last").disabled = false;
  } catch (error) {
    if (loading.parentNode) loading.remove();
    toast(`Generation failed: ${error.message}`, "error", 7000);
  } finally {
    $("#generate-button").disabled = !state.activeCheckpoint;
    $("#chat-input").value = "";
    $("#chat-input").focus();
  }
}

function setupMessageTools(msg, content) {
  if (!msg.querySelector(".message-tools")) {
    const tools = document.createElement("div");
    tools.className = "message-tools";
    tools.innerHTML = '<button data-copy-message>Copy code</button><button data-validate-message>Check with Godot</button>';
    const body = msg.querySelector(".message-body") || msg;
    body.appendChild(tools);
  }
  $("[data-copy-message]", msg)?.addEventListener("click", () => navigator.clipboard.writeText(content).then(() => toast("Code copied.")));
  $("[data-validate-message]", msg)?.addEventListener("click", () => validateCode(content, { capture: true }));
}

function setValidationState(result) {
  const target = $("#validation-state");
  target.classList.remove("passed", "failed");
  if (!result) {
    target.innerHTML = '<span class="validation-icon">○</span><div><strong>Not checked yet</strong><small>Have the generated code parsed directly.</small></div>';
    return;
  }
  target.classList.add(result.passed ? "passed" : "failed");
  const statusLabel = result.passed ? "Parser passed" : result.timed_out ? "Check timed out" : "Parser error";
  target.innerHTML = `<span class="validation-icon">${result.passed ? "✓" : "×"}</span><div><strong>${statusLabel}</strong><small>${escapeHtml((result.output || "No output").split("\n").slice(-2).join(" · "))}</small></div>`;
}

async function validateCode(code, options = {}) {
  if (!code) return toast("No code to check.", "error");
  try {
    const result = await api("/api/chat/validate", { method: "POST", body: JSON.stringify({ code }) });
    setValidationState(result);
    toast(result.passed ? "Godot parser passed." : result.timed_out ? "Godot check timed out - the process tree was cleaned up." : "Godot found a parser error.", result.passed ? "info" : "error", 5500);
    if (!result.passed && result.output) addMessage("assistant", result.output, { code: true });
    if (!result.passed && options.capture && code === state.lastGenerated && code.trim()) {
      await saveFailedSample(code, result.output || "");
    }
    return result;
  } catch (error) {
    toast(`Godot check failed: ${error.message}`, "error");
  }
}

async function saveFailedSample(code, errorText) {
  const stamp = new Date().toISOString().replaceAll(/[-:TZ.]/g, "").slice(0, 14);
  const path = `data/raw/user_lessons/generated_${stamp}.gd`;
  const firstError = (errorText || "").split("\n").find((ln) => ln.trim()) || "no parser output";
  const header = `# user sample: generated code that failed the Godot parser\n# error: ${firstError.slice(0, 300)}\n`;
  try {
    await api("/api/data/file", { method: "PUT", body: JSON.stringify({ path, content: header + code }) });
    toast("Failed sample saved as training data.");
  } catch { /* saving is best-effort */ }
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
    toast("Hardware probe run for all three profiles started.");
  } catch (error) { toast(error.message, "error"); }
}

async function startTraining() {
  const config = $("#training-config").value;
  if (!config) return toast("No configuration selected.", "error");
  const selected = state.configs.find((item) => item.path === config);
  if (selected?.profile_id) {
    if (!selected.data_ready || !selected.tokenizer_ready) return toast("This profile first needs the complete knowledge building: sources, validation, BPE and training data.", "error", 6500);
    if (selected.profile_generated) {
      const recommendation = state.autotune?.recommendation;
      if (!recommendation || recommendation.config !== selected.path) return toast("This auto-generated configuration no longer has a matching autotune report. Set up the hardware again.", "error", 6500);
    } else {
      const result = profileProbeEntry(selected.profile_id)?.configured_result;
      if (!result || result.status !== "pass") return toast("Run the hardware probe first before this large run.", "error", 6000);
    }
  }
  try {
    const preflight = await api(`/api/preflight?config=${encodeURIComponent(config)}&mode=full`);
    state.preflight = preflight; renderPreflight(preflight);
    if (!preflight.can_start) return toast("The long training run is blocked by the preflight.", "error", 7000);
    const job = await api("/api/jobs/train", {
      method: "POST",
      body: JSON.stringify({ config, resume: $("#training-resume").value || null }),
    });
    state.currentJob = job;
    renderJob(job);
    toast("Training started.");
  } catch (error) { toast(error.message, "error"); }
}

async function prepareData() {
  try {
    const job = await api("/api/jobs/prepare", { method: "POST", body: JSON.stringify({}) });
    state.currentJob = job;
    renderJob(job);
    toast("Data preparation started.");
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
  if (!state.activeCheckpoint) return toast("No checkpoint selected.", "error");
  try {
    const job = await api("/api/jobs/benchmark", {
      method: "POST",
      body: JSON.stringify({ checkpoint: state.activeCheckpoint }),
    });
    state.currentJob = job;
    renderJob(job);
    setView("training");
    toast("Fixed parser benchmark started.");
  } catch (error) { toast(error.message, "error"); }
}

async function stopJob() {
  try {
    const job = await api("/api/jobs/stop", { method: "POST", body: "{}" });
    state.currentJob = job;
    renderJob(job);
    toast("Stop signal sent.");
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
  $("#job-pill-text").textContent = !job ? "Ready" : `${friendlyJobLabel(job.kind)} · ${projectStatusLabel(job.status)}`;
  renderWorkflowSteps(job);
  renderProfessionalRun(job);
  renderLocalProgress(job);
  renderLog(job);
  renderRemote(state.remote);
  if (!job) {
    $("#training-status").textContent = "No active run";
    $("#training-progress").style.width = "0%";
    $("#training-progress-label").textContent = "0 %";
    $("#log-job-id").textContent = "No job started yet";
    updateConfigMetrics();
    return;
  }
  const progress = job.progress_state || {};
  const phase = progress.phase ? ` · ${ProgressTools.phaseLabel(progress.phase)}` : (job.step ? ` · Step ${job.step}` : "");
  const lastSuccess = job.status === "failed" && job.last_successful_step?.phase_label
    ? ` · Last success: ${job.last_successful_step.phase_label}`
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
    target.innerHTML = '<div class="empty-state">No checkpoints yet. Start the first run in the Training tab.</div>';
    return;
  }
  target.innerHTML = state.checkpoints.map((item) => `
    <article class="model-card ${item.path === state.activeCheckpoint ? "active" : ""}">
      <div class="model-head"><div class="model-name">${escapeHtml(item.run)} / ${escapeHtml(item.name)}</div><span class="model-badge">${escapeHtml(item.kind)}</span></div>
      <div class="model-path">${escapeHtml(item.path)}</div>
      <div class="model-stats"><span>${item.size_mb} MB</span><span>${item.step ? `Step ${formatNumber(item.step)}` : formatDate(item.modified_at)}</span></div>
      <button class="${item.path === state.activeCheckpoint ? "secondary-button" : "ghost-button"} compact" data-use-model="${escapeHtml(item.path)}">${item.path === state.activeCheckpoint ? "Active" : "Use in chat"}</button>
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
  toast("Active checkpoint changed.");
}

function renderSystem() {
  const o = state.overview;
  if (!o) return;
  const items = [
    ["Python", o.python],
    ["PyTorch", o.torch],
    ["CUDA Build", o.torch_cuda || (o.torch_hip ? "n/a (HIP build)" : "CPU")],
    ["ROCm Build", o.torch_hip || "off"],
    ["GPU", o.gpu?.name || "Not active"],
    ["MPS", o.mps_available ? "available" : "off"],
    ["VRAM", o.gpu ? `${o.gpu.vram_gib} GiB` : "–"],
    ["Godot", o.godot_version || "Not found"],
    ["Data files", formatNumber(o.dataset_file_count)],
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
    toast("Training file created.");
  } catch (error) { toast(error.message, "error"); }
}


function registerPwa() {
  const stateElement = $("#pwa-state");
  const installButton = $("#install-pwa");
  if (!("serviceWorker" in navigator)) {
    stateElement.textContent = "This browser does not support service workers.";
    return;
  }
  navigator.serviceWorker.register("/sw.js").then(() => {
    stateElement.textContent = window.matchMedia("(display-mode: standalone)").matches
      ? "The Studio is already running as an installed app."
      : "The mobile interface is prepared as an offline-capable app shell; live data still requires the PC.";
  }).catch((error) => { stateElement.textContent = `Could not register the PWA: ${error.message}`; });
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
    setText("#remote-state-detail", "The phone is offline or the connection to the PC was interrupted.");
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
  $("#remote-source-file").addEventListener("change", (event) => setText("#remote-file-name", event.target.files?.[0]?.name || "No file chosen", event.target.files?.[0]?.name || ""));
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
  $("#build-curriculum").addEventListener("click", () => startSimpleJob("/api/jobs/curriculum/build", "Curriculum generation started."));
  $("#validate-curriculum").addEventListener("click", () => startSimpleJob("/api/jobs/curriculum/validate", "Godot validation of all lessons started."));
  $("#prepare-curriculum").addEventListener("click", () => startSimpleJob("/api/jobs/curriculum/prepare", "Curriculum tokenization started."));
  $("#save-corpus-sources").addEventListener("click", saveCorpusSources);
  $("#select-verified-expansion").addEventListener("click", selectVerifiedExpansion);
  $("#select-core-expansion").addEventListener("click", () => selectExpansionTier("core"));
  $("#select-max-expansion").addEventListener("click", () => selectExpansionTier("max"));
  $("#add-custom-source").addEventListener("click", addCustomSource);
  $("#open-local-source-inbox").addEventListener("click", openLocalSourceInbox);
  $("#import-local-sources").addEventListener("click", importLocalSources);
  $("#corpus-fetch").addEventListener("click", () => startCorpusJob("/api/jobs/corpus/fetch", "Source download started."));
  $("#corpus-build").addEventListener("click", () => startCorpusJob("/api/jobs/corpus/build", "Corpus scan started."));
  $("#corpus-validate").addEventListener("click", () => startCorpusJob("/api/jobs/corpus/validate", "Godot validation started."));
  $("#corpus-audit").addEventListener("click", runProfessionalAudit);
  $("#corpus-tokenizer").addEventListener("click", () => startCorpusJob("/api/jobs/corpus/tokenizer", "BPE tokenizer training started.", { vocab_size: Number($("#bpe-vocab").value), min_frequency: Number($("#bpe-frequency").value) }));
  $("#corpus-prepare").addEventListener("click", () => startCorpusJob("/api/jobs/corpus/prepare", "Corpus tokenization started."));
  $("#corpus-instructions").addEventListener("click", () => startCorpusJob("/api/jobs/corpus/instructions", "Task data will be generated from the audited corpus."));
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
  $("#copy-log").addEventListener("click", () => navigator.clipboard.writeText(state.visibleLogText || $("#training-log").textContent).then(() => toast("Visible log copied.")));
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
