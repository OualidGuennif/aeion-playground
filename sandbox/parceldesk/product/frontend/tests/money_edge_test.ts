import { assertEquals } from "https://deno.land/std@0.216.0/assert/mod.ts";
import { money } from "../lib/format.ts";

Deno.test("money edge cases for zero and single-cent amounts", () => {
  assertEquals(money(0).includes("0.00"), true);
  assertEquals(money(1).includes("0.01"), true);
  assertEquals(money(99).includes("0.99"), true);
});
