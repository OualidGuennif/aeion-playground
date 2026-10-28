import { assertEquals } from "https://deno.land/std@0.216.0/assert/mod.ts";
import { mergeable } from "../lib/format.ts";

Deno.test("mergeable edge cases", () => {
  assertEquals(mergeable({}), false);
  assertEquals(mergeable({ status: "OPEN", checks: { tests: { passed: 1, total: 1 } }, review: { verdict: "ok" } }), true);
  assertEquals(mergeable({ status: "APPROVED", checks: { tests: { passed: 1, total: 1 } }, review: { verdict: "OK" } }), true);
  assertEquals(mergeable({ status: "MERGED", checks: { tests: { passed: 1, total: 1 } }, review: { verdict: "OK" } }), false);
});
