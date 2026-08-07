const assert = require("node:assert/strict");
const progress = require("../src/godot_coder/ui/static/progress.js");

assert.equal(progress.shouldAutoFollow(true, 0), true);
assert.equal(progress.shouldAutoFollow(true, 80), false);
assert.equal(progress.shouldAutoFollow(false, 0), false);
assert.equal(progress.formatEta({}), "Restzeit wird berechnet …");
assert.equal(progress.formatEta({ eta_status: "calculating", estimated_remaining_seconds: 12 }), "Restzeit wird berechnet …");
assert.match(progress.formatEta({ estimated_remaining_seconds: 65 }), /01:05/);
assert.equal(progress.phaseLabel("remote_download"), "Quelle auf dem PC herunterladen");

const job = {
  command: ["python", "-m", "demo"],
  started_at: 1,
  events: [
    { timestamp: "2026-01-01T00:00:01Z", level: "info", project_name: "A", phase: "inventory", message: "Found" },
    { timestamp: "2026-01-01T00:00:02Z", level: "warning", project_name: "B", phase: "static_analysis", message: "Warn" },
    { timestamp: "2026-01-01T00:00:03Z", level: "error", project_name: "B", phase: "godot_validation", message: "Fail" },
  ],
  log_records: [{ timestamp: "2026-01-01T00:00:00Z", level: "info", text: "raw" }],
};
const simple = progress.normalizeLogEntries(job, "simple");
const technical = progress.normalizeLogEntries(job, "technical");
assert.equal(simple.length, 3);
assert.ok(technical.length >= 5);
assert.deepEqual(progress.filterLogEntries(simple, {
  levels: { info: false, warning: true, error: true }, project: "B", phase: "",
}).map((entry) => entry.level), ["warning", "error"]);
assert.equal(progress.filterLogEntries(simple, {
  levels: { info: true, warning: true, error: true }, project: "B", phase: "static_analysis",
}).length, 1);

const legacy = progress.normalizeLogEntries({ logs: ["local_import=1/2", "ERROR: broken"] }, "simple");
assert.equal(legacy[1].level, "error");
const windowsPath = "C:\\Users\\Tester\\VeryLongProjectName\\scripts\\" + "x".repeat(120) + ".gd";
const shortened = progress.shortenPath(windowsPath, 70);
assert.ok(shortened.length <= 75);
assert.match(shortened, /scripts\//);
console.log("js progress tests passed");
