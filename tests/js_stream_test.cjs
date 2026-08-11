const assert = require("node:assert/strict");
const stream = require("../src/godot_coder/ui/static/stream.js");

// A frame split across two network chunks must be reassembled.
const first = stream.consumeStreamChunk("", 'data: {"token": "func ');
assert.equal(first.events.length, 0, "incomplete frame is buffered");
assert.equal(first.rest, 'data: {"token": "func ');
const second = stream.consumeStreamChunk(first.rest, 'add"}\n');
assert.equal(second.events.length, 1);
assert.equal(second.events[0].token, "func add");
assert.equal(second.rest, "");

// Multiple frames in one chunk come out in order.
const multi = stream.consumeStreamChunk(
  "",
  'data: {"token": "a"}\ndata: {"token": "b"}\ndata: {"done": true, "text": "ab", "tokens": 2}\n',
);
assert.equal(multi.events.length, 3);
assert.deepEqual(
  multi.events.map((event) => event.token || event.done),
  ["a", "b", true],
);

// [DONE] markers and malformed frames are skipped, later frames survive.
const mixed = stream.consumeStreamChunk(
  "",
  'data: [DONE]\ndata: {not json}\ndata: {"error": "boom"}\n',
);
assert.equal(mixed.events.length, 1);
assert.equal(mixed.events[0].error, "boom");

// A trailing unterminated frame stays in the buffer for the next chunk.
const trailing = stream.consumeStreamChunk("", 'data: {"token": "x"}\ndata: {"to');
assert.equal(trailing.events.length, 1);
assert.equal(trailing.events[0].token, "x");
assert.equal(trailing.rest, 'data: {"to');

console.log("js stream tests passed");
