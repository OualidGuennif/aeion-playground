import { assertEquals } from "https://deno.land/std@0.216.0/assert/mod.ts";
import { mergeable } from "../lib/format.ts";

Deno.test("mergeable is false without a review verdict even when tests are absent", () => {
  assertEquals(mergeable({ status: "OPEN" }), false);
});

Deno.test("mergeable is false with zero-test runs even when reviewed", () => {
  assertEquals(mergeable({ status: "OPEN", checks: { tests: { passed: 0, total: 0 } }, review: { verdict: "OK" } }), false);
});

Deno.test("mergeable is false with green tests but no review verdict", () => {
  assertEquals(mergeable({ status: "OPEN", checks: { tests: { passed: 2, total: 2 } } }), false);
});
