(function attachProgressTools(globalScope) {
  "use strict";

  const PHASE_LABELS = {
    input_detection: "ZIP oder Ordner erkennen",
    secure_extract: "Sicher entpacken",
    project_detection: "project.godot erkennen",
    inventory: "Dateien inventarisieren",
    cache_exclusion: "Cache- und Importdateien ausschließen",
    addon_classification: "Add-ons klassifizieren",
    secret_scan: "Secret-Prüfung",
    file_size_check: "Dateigrößenprüfung",
    static_analysis: "Statische GDScript-Prüfung",
    deduplication: "Quelle deduplizieren",
    corpus_admission: "Bereinigte Arbeitskopie aufnehmen",
    godot_validation: "Godot-Projektimport und Parserprüfung",
    quarantine_decision: "Quarantäneentscheidung",
    registry_update: "Corpus-Registry aktualisieren",
    report_writing: "Abschlussbericht schreiben",
    remote_link_validation: "Remote-Link sicher prüfen",
    remote_download: "Quelle auf dem PC herunterladen",
  };

  function levelFromText(text) {
    const value = String(text || "").toLowerCase();
    if (value.includes("traceback") || value.includes("error") || value.includes("failed") || value.includes("exception")) return "error";
    if (value.includes("warning") || value.includes("warn") || value.includes("quarant")) return "warning";
    return "info";
  }

  function formatDuration(seconds) {
    if (seconds == null || Number.isNaN(Number(seconds))) return "–";
    const total = Math.max(0, Math.round(Number(seconds)));
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    const secs = total % 60;
    return hours > 0
      ? `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`
      : `${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
  }

  function formatEta(progressState) {
    const state = progressState || {};
    if (state.eta_status === "calculating") return "Restzeit wird berechnet …";
    const estimate = state.estimated_remaining_seconds;
    if (estimate == null) return "Restzeit wird berechnet …";
    const minimum = state.estimated_remaining_min_seconds;
    const maximum = state.estimated_remaining_max_seconds;
    if (minimum != null && maximum != null && Number(maximum) - Number(minimum) >= 20) {
      return `geschätzt ${formatDuration(minimum)}–${formatDuration(maximum)}`;
    }
    return `geschätzt ${formatDuration(estimate)}`;
  }

  function normalizeLogEntries(job, mode = "simple") {
    if (!job) return [];
    const events = Array.isArray(job.events) ? job.events : [];
    const records = Array.isArray(job.log_records) ? job.log_records : [];
    if (mode === "technical") {
      const result = [];
      if (Array.isArray(job.command) && job.command.length) {
        result.push({
          timestamp: job.started_at ? new Date(job.started_at * 1000).toISOString() : "",
          level: "info",
          project: "",
          phase: "command",
          text: `$ ${job.command.join(" ")}`,
          raw: { record_type: "command", command: job.command, cwd: job.cwd },
        });
      }
      for (const record of records) {
        result.push({
          timestamp: record.timestamp || "",
          level: record.level || levelFromText(record.text),
          project: record.project_name || "",
          phase: record.phase || "raw_output",
          text: record.text || "",
          raw: record,
        });
      }
      for (const event of events) {
        result.push({
          timestamp: event.timestamp || "",
          level: event.level || "info",
          project: event.project_name || "",
          phase: event.phase || event.event || "event",
          text: JSON.stringify(event),
          raw: { record_type: "event", ...event },
        });
      }
      return result.sort((a, b) => String(a.timestamp).localeCompare(String(b.timestamp)));
    }
    if (events.length) {
      return events
        .filter((event) => event.message || event.event === "job_finished")
        .map((event) => ({
          timestamp: event.timestamp || "",
          level: event.level || "info",
          project: event.project_name || "",
          phase: event.phase || event.event || "",
          text: event.message || event.event,
          raw: event,
        }));
    }
    return (job.logs || []).map((text) => ({
      timestamp: "",
      level: levelFromText(text),
      project: "",
      phase: "legacy_text",
      text: String(text),
      raw: { record_type: "legacy_text", text: String(text) },
    }));
  }

  function filterLogEntries(entries, filters) {
    const settings = filters || {};
    const levels = settings.levels || { info: true, warning: true, error: true };
    return (entries || []).filter((entry) => {
      if (levels[entry.level] === false) return false;
      if (settings.project && entry.project !== settings.project) return false;
      if (settings.phase && entry.phase !== settings.phase) return false;
      return true;
    });
  }

  function shouldAutoFollow(enabled, distanceFromBottom) {
    return Boolean(enabled) && Number(distanceFromBottom || 0) <= 32;
  }

  function shortenPath(value, maximum = 86) {
    const text = String(value || "");
    if (text.length <= maximum) return text;
    const normalized = text.replaceAll("\\", "/");
    const parts = normalized.split("/");
    const filename = parts.at(-1) || "";
    const parent = parts.at(-2) || "";
    const tail = [parent, filename].filter(Boolean).join("/");
    if (tail.length >= maximum - 2) {
      const available = Math.max(8, maximum - parent.length - 3);
      return `${parent ? `${parent}/` : ""}…${filename.slice(-available)}`;
    }
    const budget = Math.max(8, maximum - tail.length - 3);
    return `${normalized.slice(0, budget)}…/${tail}`;
  }

  function phaseLabel(value) {
    return PHASE_LABELS[value] || String(value || "–").replaceAll("_", " ");
  }

  const api = {
    PHASE_LABELS,
    filterLogEntries,
    formatDuration,
    formatEta,
    levelFromText,
    normalizeLogEntries,
    phaseLabel,
    shortenPath,
    shouldAutoFollow,
  };

  if (typeof module !== "undefined" && module.exports) module.exports = api;
  globalScope.GodotCoderProgress = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
