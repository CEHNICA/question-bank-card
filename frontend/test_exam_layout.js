"use strict";
const assert = require("node:assert/strict");
const layout = require("./exam-layout.js");
assert.deepEqual(layout.normalize(), { pagination: "compact", option_layout: "auto", option_overrides: {}, question_breaks: [] });
assert.deepEqual(layout.normalize({ pagination: "keep", option_layout: "four", option_overrides: { a: "two", b: "no" }, question_breaks: ["a", "a", 3] }),
  { pagination: "keep", option_layout: "four", option_overrides: { a: "two" }, question_breaks: ["a"] });
assert.equal(layout.keepWhole(900, layout.BODY_HEIGHT, "compact"), false);
assert.equal(layout.keepWhole(900, layout.BODY_HEIGHT, "keep"), true);
assert.equal(layout.keepWhole(1200, layout.BODY_HEIGHT, "keep"), false, "An oversized question must be allowed to continue instead of creating an empty sheet");
assert.equal(layout.requestedColumns("four", 4), 4);
assert.equal(layout.requestedColumns("four", 5), 2, "Five real options must survive a four-column preference");
assert.equal(layout.requestedColumns("two", 4), 2);
assert.equal(layout.requestedColumns("auto", 4), 0);
assert(Math.abs(layout.BODY_HEIGHT - 986.45669) < .001);
assert(Math.abs(layout.BODY_WIDTH - 672.755906) < .001);
assert.rejects(layout.paginate(null), /缺少/).then(() => console.log("A4 layout options and pagination boundaries: OK"));
