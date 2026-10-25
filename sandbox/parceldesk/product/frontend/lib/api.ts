export const backendUrl = Deno.env.get("BACKEND_URL") ?? "http://127.0.0.1:8080";
export const cockpitUrl = Deno.env.get("COCKPIT_URL") ?? "http://127.0.0.1:8124";

export async function getJson<T>(url: string, fallback: T): Promise<T> {
  try {
    const response = await fetch(url, { headers: { accept: "application/json" } });
    if (!response.ok) return fallback;
    return await response.json() as T;
  } catch {
    return fallback;
  }
}

export interface Order {
  id: string;
  customer: string;
  city: string;
  item: string;
  grams: number;
  totalCents: number;
  shippingCents: number;
  status: string;
  deliveredAt: string | null;
  refundReason?: string | null;
  refundAmountCents?: number | null;
  refundedAt?: string | null;
}

export interface Summary {
  orders: number;
  counts: Record<string, number>;
  revenueCents: number;
  refundedCents: number;
  refunds: number;
  openPulls: number;
  mergedPulls: number;
}

export interface MissionBrief {
  id: string;
  status: string;
  risk?: string;
  current_step?: string | null;
  gate_reason_code?: string | null;
  gate_reason?: string | null;
}

export interface Ticket {
  id: string;
  title: string;
  outcome: string;
  area: string;
  risk: string;
  status: string;
  mission_id: string | null;
  error?: string | null;
  created_at?: string;
  product?: string;
  mission?: MissionBrief | null;
}

export interface Pull {
  id: string;
  ticketId: string | null;
  missionId: string;
  title: string;
  risk: string;
  status: string;
  diff: string;
  checks: {
    tests?: { passed?: number; total?: number; skipped?: number };
    gateApproved?: boolean;
    gateDecision?: string;
    reasonCode?: string;
  };
  review: { verdict?: string; comment?: string; model?: string; reviewer?: string };
  commitHash: string | null;
  packetDigest: string | null;
  branch: string | null;
  base: string | null;
  createdAt: string;
  mergedAt: string | null;
  mergedBy: string | null;
}

export function emptySummary(): Summary {
  return {
    orders: 0,
    counts: {},
    revenueCents: 0,
    refundedCents: 0,
    refunds: 0,
    openPulls: 0,
    mergedPulls: 0,
  };
}
