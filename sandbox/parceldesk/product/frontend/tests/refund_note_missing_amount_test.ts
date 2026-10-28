import { assertEquals } from "https://deno.land/std@0.216.0/assert/mod.ts";
import { refundNote } from "../lib/format.ts";

Deno.test("refund note handles missing amount and whitespace-only reason", () => {
  assertEquals((refundNote({ status: "REFUNDED", refundAmountCents: undefined, refundReason: "x" }) ?? "").includes("0.00"), true);
  assertEquals((refundNote({ status: "REFUNDED", refundAmountCents: undefined, refundReason: "x" }) ?? "").includes("x"), true);
  assertEquals((refundNote({ status: "REFUNDED", refundAmountCents: 1234, refundReason: "  " }) ?? "").includes("reason not recorded"), true);
});
