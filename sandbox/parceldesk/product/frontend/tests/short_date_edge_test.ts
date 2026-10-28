import { assertEquals } from "https://deno.land/std@0.216.0/assert/mod.ts";
import { shortDate } from "../lib/format.ts";

Deno.test("short date edge cases", () => {
  assertEquals(shortDate("2026-10-22"), "2026-10-22");
  assertEquals(shortDate("2026-10-22T23:59:59Z"), "2026-10-22");
  assertEquals(shortDate(""), "—");
});
