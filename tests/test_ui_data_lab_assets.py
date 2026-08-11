from pathlib import Path

from godot_coder import __version__

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
    assert "Max expansion · toward 20M" in html


def test_service_worker_cache_is_bumped_for_maintenance_release() -> None:
    service_worker = (STATIC / "sw.js").read_text(encoding="utf-8")
    assert f"godot-coder-shell-v{__version__}" in service_worker


def test_raw_chat_fetches_carry_the_remote_csrf_header() -> None:
    # The remote-access middleware demands the session CSRF token on every
    # non-GET request reached through Tailscale. The streaming and stop
    # paths use raw fetch (the api() helper can't stream SSE), so they must
    # attach the header themselves or remote chat dies with a 403.
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert 'function csrfHeaders()' in js
    assert 'X-Godot-Coder-CSRF' in js
    assert 'headers: csrfHeaders()' in js  # POST /api/chat/stop
    assert '"Content-Type": "application/json", ...csrfHeaders()' in js  # POST /api/chat/generate-stream


def test_delete_active_session_resets_last_generation_state() -> None:
    # Deleting the active conversation must reset the global Check/Save
    # buttons the same way clearChat() does - otherwise they keep pointing
    # at the deleted turn's completion.
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert 'state.lastGenerated = "";' in js
    assert 'state.lastPrompt = "";' in js
    assert '#validate-last").disabled = true' in js
    assert '#save-last").disabled = true' in js
    assert "setValidationState(null);" in js
    # the reset lives inside deleteChatSession (there are two setValidation
    # calls now: clearChat + deleteChatSession)
    assert js.count("setValidationState(null);") >= 2


def test_conversations_toggle_renders_even_when_session_api_fails() -> None:
    # The loadChatHistory catch path must render the empty conversations
    # toggle; otherwise the feature looks missing until the first message.
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "History is best-effort" in js
    assert "state.chatSessions = [];" in js
    # the catch branch renders the toggle itself (it must not rely on a
    # later refreshChatSessions() that may never come)
    assert "renderChatSessions();\n  }\n}" in js
    # plus the normal empty-list branch before it
    assert js.count("renderChatSessions();") >= 5
