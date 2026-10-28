import { assertEquals } from "https://deno.land/std@0.216.0/assert/mod.ts";
import { refundNote } from "../lib/format.ts";

Deno.test("refund note covers a zero refund with a blank reason", () => {
  const note = refundNote({ status: "refunded", refundAmountCents: 0, refundReason: "   " }) ?? "";
  assertEquals(note.includes("0.00"), true);
  assertEquals(note.includes("reason not recorded"), true);
});

Deno.test("refund note is null for a delivered, non-refunded order", () => {
  assertEquals(refundNote({ status: "DELIVERED", refundAmountCents: null, refundReason: null }), null);
});
