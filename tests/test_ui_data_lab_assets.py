from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "godot_coder" / "ui" / "static"


def test_data_lab_has_live_catalog_delete_and_token_filters() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert 'id="delete-editor"' in html
    assert 'id="data-kind-filter"' in html
    assert 'value="training"' in html
    assert 'id="data-token-breakdown"' in html
    assert 'api("/api/data/catalog")' in js
    assert 'method: "DELETE"' in js
    assert "refreshDataCatalog(false)" in js
    assert "2500" in js
    assert '$("#dataset-token-count").textContent = formatNumber(summary.train_tokens || 0)' in js
    assert "Maximalausbau · Richtung 20M" in html


def test_service_worker_cache_is_bumped_for_maintenance_release() -> None:
    service_worker = (STATIC / "sw.js").read_text(encoding="utf-8")
    assert 'godot-coder-shell-v0.10.1' in service_worker
