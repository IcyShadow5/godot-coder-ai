/* Pure SSE stream parsing for the chat token stream.

   The chat loops over a ReadableStream and feeds decoded text here; this
   helper owns the line splitting and frame parsing. It touches no DOM, so
   the node test (tests/js_stream_test.cjs) can cover the streaming display
   logic that is the core of the chat feature. */
(function attachStreamTools(globalScope) {
  "use strict";

  // Consume one decoded chunk and return the completed SSE events plus the
  // unterminated buffer tail. `[DONE]` markers and malformed frames are
  // skipped, mirroring what the UI should tolerate.
  function consumeStreamChunk(buffer, chunk) {
    const next = String(buffer || "") + String(chunk || "");
    const lines = next.split("\n");
    const rest = lines.pop() || "";
    const events = [];
    for (const line of lines) {
      if (!line.startsWith("data: ")) continue;
      const payload = line.slice(6).trim();
      if (payload === "[DONE]") continue;
      try {
        events.push(JSON.parse(payload));
      } catch {
        // A truncated or corrupted frame must not break the stream.
      }
    }
    return { events, rest };
  }

  const api = {
    consumeStreamChunk,
  };

  if (typeof module !== "undefined" && module.exports) module.exports = api;
  globalScope.GodotCoderStream = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
