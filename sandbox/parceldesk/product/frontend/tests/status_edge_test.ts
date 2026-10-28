import { assertEquals } from "https://deno.land/std@0.216.0/assert/mod.ts";
import { statusLabel } from "../lib/format.ts";

Deno.test("status labels cover edge cases", () => {
  assertEquals(statusLabel("PACKING"), "Packing");
  assertEquals(statusLabel("IN_TRANSIT"), "In Transit");
  assertEquals(statusLabel("ON_HOLD"), "On Hold");
  assertEquals(statusLabel(""), "");
});
