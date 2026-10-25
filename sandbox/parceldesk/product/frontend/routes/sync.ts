import type { Handlers } from "$fresh/server.ts";
import { backendUrl, cockpitUrl, type Pull, type Ticket } from "../lib/api.ts";

interface MissionFull {
  mission: {
    id: string;
    title: string;
    risk: string;
    status: string;
    gate_decision?: string | null;
    gate_reason_code?: string | null;
    gate_reason?: string | null;
    human_approval?: string | null;
    diff_json?: string;
    tests_json?: string;
    gate_json?: string;
  };
  runs: Array<{ agent: string; status: string; verdict?: string | null; reason?: string | null; model?: string | null }>;
  delivery?: { commit?: string; base_commit?: string; packet_digest?: string } | null;
}

interface TicketPayload {
  tickets: Ticket[];
}

async function readJson<T>(url: string): Promise<T> {
  const response = await fetch(url, { headers: { accept: "application/json" } });
  if (!response.ok) throw new Error(`${url} returned ${response.status}`);
  return await response.json() as T;
}

function parseJson<T>(value: string | undefined, fallback: T): T {
  if (!value) return fallback;
  try {
    return JSON.parse(value) as T;
  } catch {
    return fallback;
  }
}

export const handler: Handlers = {
  async POST(request) {
    let autoMerge = false;
    try {
      const body = await request.json();
      autoMerge = body?.autoMerge === true;
    } catch {
      autoMerge = false;
    }
    try {
      const payload = await readJson<TicketPayload>(`${cockpitUrl}/api/parceldesk/tickets`);
      let created = 0;
      let merged = 0;
      let blocked = 0;
      const notes: string[] = [];
      for (const ticket of payload.tickets) {
        if (ticket.product !== "product") continue;
        if (!ticket.mission_id || ticket.mission?.status !== "completed") continue;
        const detail = await readJson<MissionFull>(`${cockpitUrl}/api/missions/${ticket.mission_id}`);
        const mission = detail.mission;
        const diff = parseJson<{ patch?: string }>(mission.diff_json, { patch: "" });
        const tests = parseJson<Record<string, unknown>>(mission.tests_json, {});
        const gate = parseJson<Record<string, unknown>>(mission.gate_json, {});
        const review = detail.runs.find((run) => run.agent === "sentinel" && run.status === "completed");
        const checks = {
          tests,
          gate,
          gateApproved: mission.status === "completed" && mission.gate_decision !== "deny",
          gateDecision: mission.gate_decision ?? (gate.decision as string | undefined) ?? "unknown",
          reasonCode: mission.gate_reason_code ?? null,
        };
        const reviewPayload = {
          verdict: review?.verdict ?? "",
          comment: review?.reason ?? "No review run recorded.",
          model: review?.model ?? "sentinel",
          reviewer: "sentinel",
        };
        const body = JSON.stringify({
          missionId: mission.id,
          ticketId: ticket.id,
          title: mission.title,
          risk: mission.risk,
          diff: diff.patch ?? "",
          commitHash: detail.delivery?.commit ?? null,
          packetDigest: detail.delivery?.packet_digest ?? null,
          branch: `factory/${mission.id}`,
          base: detail.delivery?.base_commit ?? null,
          checks,
          review: reviewPayload,
        });
        const response = await fetch(`${backendUrl}/api/pulls`, {
          method: "POST",
          headers: { "content-type": "application/json", accept: "application/json" },
          body,
        });
        const pull = await response.json() as Pull & { error?: string };
        if (!response.ok) {
          blocked += 1;
          notes.push(`${ticket.id}: ${pull.error ?? response.status}`);
          continue;
        }
        created += 1;
        if (autoMerge && pull.status === "OPEN" && pull.risk === "low") {
          const merge = await fetch(`${backendUrl}/api/pulls/${pull.id}/merge`, {
            method: "POST",
            headers: { "content-type": "application/json", accept: "application/json" },
            body: JSON.stringify({ mergedBy: "factory-auto" }),
          });
          if (merge.ok) {
            merged += 1;
            notes.push(`${ticket.id}: ${pull.id} auto-merged`);
          } else {
            blocked += 1;
            notes.push(`${ticket.id}: merge refused (${merge.status})`);
          }
        }
      }
      return new Response(JSON.stringify({ ok: true, created, merged, blocked, notes }), {
        headers: { "content-type": "application/json" },
      });
    } catch (error) {
      return new Response(JSON.stringify({ ok: false, error: String(error) }), {
        status: 502,
        headers: { "content-type": "application/json" },
      });
    }
  },
};
