import { assertEquals } from "https://deno.land/std@0.216.0/assert/mod.ts";
import { money } from "../lib/format.ts";

Deno.test("money adds thousands separators above one thousand", () => {
  assertEquals(money(123456).includes("1,234.56"), true);
  assertEquals(money(100000).includes("1,000.00"), true);
});
