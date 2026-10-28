import { assertEquals } from "https://deno.land/std@0.216.0/assert/mod.ts";
import { statusLabel } from "../lib/format.ts";

Deno.test("status labels keep empty input empty and preserve a single letter", () => {
  assertEquals(statusLabel(""), "");
  assertEquals(statusLabel("A"), "A");
  assertEquals(statusLabel("IN_TRANSIT"), "In Transit");
});
