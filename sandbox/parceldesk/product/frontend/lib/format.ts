export function money(cents: number): string {
  return new Intl.NumberFormat("en-IE", {
    style: "currency",
    currency: "EUR",
  }).format(cents / 100);
}

export function statusLabel(status: string): string {
  return status
    .toLowerCase()
    .replace(/_/g, " ")
    .replace(/\b[a-z]/g, (value) => value.toUpperCase());
}

export function shortDate(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toISOString().slice(0, 10);
}

export function mergeable(pull: {
  status?: string;
  checks?: { tests?: { passed?: number; total?: number } };
  review?: { verdict?: string };
}): boolean {
  const tests = pull.checks?.tests;
  const testsGreen = Boolean(tests && tests.total && tests.total > 0 && tests.passed === tests.total);
  return (
    Boolean(testsGreen) &&
    Boolean(pull.review?.verdict && pull.review.verdict.toUpperCase() === "OK") &&
    ["OPEN", "APPROVED"].includes((pull.status ?? "").toUpperCase())
  );
}
