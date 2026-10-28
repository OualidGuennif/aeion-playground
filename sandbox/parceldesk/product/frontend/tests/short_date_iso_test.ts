import { assertEquals } from "https://deno.land/std@0.216.0/assert/mod.ts";
import { shortDate } from "../lib/format.ts";

Deno.test("shortDate parses ISO timestamps and date-only inputs without shifting the day", () => {
  assertEquals(shortDate("2026-01-02T00:00:00Z"), "2026-01-02");
  assertEquals(shortDate("2026-01-02"), "2026-01-02");
  assertEquals(shortDate(undefined), "—");
});
