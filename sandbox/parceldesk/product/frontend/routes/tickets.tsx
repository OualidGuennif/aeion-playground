import type { Handlers, PageProps } from "$fresh/server.ts";
import { backendUrl, cockpitUrl, getJson, type Pull, type Ticket } from "../lib/api.ts";
import { statusLabel } from "../lib/format.ts";

interface Data {
  tickets: Ticket[];
  pulls: Pull[];
  cockpitOnline: boolean;
}

export const handler: Handlers<Data> = {
  async GET(_request, ctx) {
    const [ticketPayload, pulls] = await Promise.all([
      getJson<{ tickets: Ticket[] }>(`${cockpitUrl}/api/parceldesk/tickets`, { tickets: [] }),
      getJson<Pull[]>(`${backendUrl}/api/pulls`, []),
    ]);
    return ctx.render({
      tickets: ticketPayload.tickets ?? [],
      pulls,
      cockpitOnline: Array.isArray(ticketPayload.tickets),
    });
  },
};

export default function Tickets({ data }: PageProps<Data>) {
  const pullByMission = new Map(data.pulls.map((pull) => [pull.missionId, pull]));
  return (
    <div class="page page-tickets">
      <section class="hero compact">
        <div>
          <p class="eyebrow">bounded autonomy</p>
          <h1>The <em>Factory</em> is open.</h1>
          <p class="lede">
            Describe an outcome. A paid author model patches the Quarkus/Deno product, the worker runs
            every suite, an independent reviewer reads the diff, and the Gate decides whether a human
            must approve it.
          </p>
        </div>
        <div class="hero-actions">
          <button class="button ghost" data-sync data-auto-merge="true">Sync reviews now</button>
          <a class="button ghost" href="/pulls">Review queue ({data.pulls.filter((p) => p.status === "OPEN" || p.status === "APPROVED").length})</a>
        </div>
      </section>

      <section class="split">
        <div class="panel form-panel">
          <div class="panel-head">
            <div>
              <p class="eyebrow">new ticket</p>
              <h2>Send an outcome</h2>
            </div>
            <span class={`signal ${data.cockpitOnline ? "ok" : "bad"}`}>{data.cockpitOnline ? "factory online" : "factory offline"}</span>
          </div>
          <form id="ticket-form" class="ticket-form">
            <input type="hidden" name="product" value="product" />
            <label>Title<input name="title" minlength="4" maxlength="120" required placeholder="Expose the refund reason in the order list" /></label>
            <label>Observable outcome<textarea name="outcome" rows="2" minlength="10" maxlength="500" required placeholder="A support agent sees why an order was refunded without opening the database." /></label>
            <div class="form-row">
              <label>Area
                <select name="area">
                  <option value="support">Support</option>
                  <option value="refunds">Refunds</option>
                  <option value="orders">Orders</option>
                  <option value="warehouse">Warehouse</option>
                  <option value="accessibility">Accessibility</option>
                  <option value="pricing">Pricing</option>
                </select>
              </label>
              <label>Risk
                <select name="risk">
                  <option value="low">Low · auto-approve</option>
                  <option value="medium">Medium</option>
                  <option value="high">High · human checkpoint</option>
                </select>
              </label>
            </div>
            <label>Acceptance checks<textarea name="acceptance" rows="4" minlength="10" maxlength="2000" required placeholder="The order list returns refundReason and refundAmountCents for PD-1050. A new Quarkus test asserts both. Every existing Python, Maven and Deno test passes." /></label>
            <label>Constraints<input name="constraints" maxlength="1000" placeholder="Do not change the refund window, the high-value threshold or the seed data." /></label>
            <div class="form-actions">
              <button class="button primary" type="submit">Send to the Factory →</button>
              <label class="check"><input type="checkbox" data-auto-sync checked /> Auto-sync completed missions and merge low-risk green PRs</label>
            </div>
            <p id="ticket-result" class="form-note" role="status" />
          </form>
        </div>

        <div class="panel">
          <div class="panel-head">
            <div>
              <p class="eyebrow">workboard</p>
              <h2>Open and resolved</h2>
            </div>
            <span class="mono">{data.tickets.length} total</span>
          </div>
          {data.tickets.length === 0
            ? <p class="empty">No ticket yet. The first one starts a paid, bounded mission.</p>
            : (
              <ul class="ticket-list">
                {data.tickets.map((ticket) => {
                  const mission = ticket.mission;
                  const pull = mission ? pullByMission.get(mission.id) : undefined;
                  const status = pull?.status === "MERGED" ? "RESOLVED" : (mission?.status ?? ticket.status);
                  return (
                    <li key={ticket.id} class="ticket-card">
                      <div class="ticket-top">
                        <span class="mono">{ticket.id}</span>
                        <span class={`status ${String(status).toLowerCase()}`}>{statusLabel(String(status))}</span>
                      </div>
                      <h3>{ticket.title}</h3>
                      <p>{ticket.outcome}</p>
                      <div class="ticket-meta">
                        <span>{ticket.area}</span>
                        <span>{ticket.risk} risk</span>
                        <span>{ticket.product === "product" ? "quarkus + deno" : "legacy sandbox"}</span>
                        {mission ? <span class="mono">{mission.id}</span> : null}
                        {pull ? <a class="mono" href={`/pulls#${pull.id}`}>{pull.id}</a> : null}
                      </div>
                      {mission?.gate_reason_code
                        ? <p class="ticket-reason">{mission.gate_reason_code}: {mission.gate_reason ?? ""}</p>
                        : null}
                      {mission?.status === "awaiting_human"
                        ? (
                          <button class="mini-button strong" data-approve={mission.id}>
                            Approve checkpoint
                          </button>
                        )
                        : null}
                    </li>
                  );
                })}
              </ul>
            )}
        </div>
      </section>
    </div>
  );
}
