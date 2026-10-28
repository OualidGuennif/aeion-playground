import { assertEquals } from "https://deno.land/std@0.216.0/assert/mod.ts";
import { refundNote } from "../lib/format.ts";

Deno.test("refund note accepts lowercase refunded status", () => {
  const note = refundNote({ status: "refunded", refundAmountCents: 1500, refundReason: "Damaged" }) ?? "";
  assertEquals(note.includes("15.00"), true);
  assertEquals(note.includes("Damaged"), true);
});

Deno.test("refund note accepts mixed-case Refunded status", () => {
  const note = refundNote({ status: "Refunded", refundAmountCents: 1500, refundReason: "Damaged" }) ?? "";
  assertEquals(note.includes("15.00"), true);
  assertEquals(note.includes("Damaged"), true);
});

Deno.test("refund note stays null for non-refunded orders regardless of case", () => {
  assertEquals(refundNote({ status: "DELIVERED", refundAmountCents: 1500, refundReason: "Damaged" }), null);
});
