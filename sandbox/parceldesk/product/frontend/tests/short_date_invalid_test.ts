import { assertEquals } from "https://deno.land/std@0.216.0/assert/mod.ts";
import { shortDate } from "../lib/format.ts";

Deno.test("invalid short dates render a dash", () => {
  assertEquals(shortDate("not-a-date"), "—");
  assertEquals(shortDate(null), "—");
  assertEquals(shortDate("2026-13-40"), "—");
});
