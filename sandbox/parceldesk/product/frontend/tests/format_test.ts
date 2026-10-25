import { assertEquals } from "https://deno.land/std@0.216.0/assert/mod.ts";
import { mergeable, money, shortDate, statusLabel } from "../lib/format.ts";

Deno.test("money renders euro cents with two decimals", () => {
  assertEquals(money(6900).includes("69.00"), true);
  assertEquals(money(500).includes("5.00"), true);
});

Deno.test("status labels become readable", () => {
  assertEquals(statusLabel("IN_TRANSIT"), "In Transit");
  assertEquals(statusLabel("DELIVERED"), "Delivered");
});

Deno.test("short dates are stable", () => {
  assertEquals(shortDate("2026-10-22T12:34:56Z"), "2026-10-22");
  assertEquals(shortDate(null), "—");
});

Deno.test("only green reviewed pull requests are mergeable", () => {
  assertEquals(mergeable({ status: "OPEN", checks: { tests: { passed: 8, total: 8 } }, review: { verdict: "OK" } }), true);
  assertEquals(mergeable({ status: "OPEN", checks: { tests: { passed: 7, total: 8 } }, review: { verdict: "OK" } }), false);
  assertEquals(mergeable({ status: "OPEN", checks: { tests: { passed: 8, total: 8 } }, review: { verdict: "CHANGES_REQUESTED" } }), false);
  assertEquals(mergeable({ status: "MERGED", checks: { tests: { passed: 8, total: 8 } }, review: { verdict: "OK" } }), false);
});
