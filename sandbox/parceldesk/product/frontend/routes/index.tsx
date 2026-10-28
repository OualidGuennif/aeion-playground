import type { Handlers, PageProps } from "$fresh/server.ts";
import { backendUrl, cockpitUrl, emptySummary, getJson, type Order, type Summary, type Ticket } from "../lib/api.ts";
import { money, refundNote, shortDate, statusLabel } from "../lib/format.ts";

interface Data {
  orders: Order[];
  refunds: Order[];
  summary: Summary;
  tickets: Ticket[];
  backendOnline: boolean;
}

export const handler: Handlers<Data> = {
  async GET(_request, ctx) {
    const [orders, refunds, summary, ticketPayload] = await Promise.all([
      getJson<Order[]>(`${backendUrl}/api/orders`, []),
      getJson<Order[]>(`${backendUrl}/api/refunds`, []),
      getJson<Summary>(`${backendUrl}/api/summary`, emptySummary()),
      getJson<{ tickets: Ticket[] }>(`${cockpitUrl}/api/parceldesk/tickets`, { tickets: [] }),
    ]);
    return ctx.render({
      orders,
      refunds,
      summary,
      tickets: ticketPayload.tickets ?? [],
      backendOnline: orders.length > 0,
    });
  },
};

function safeJson(value: unknown): string {
  return JSON.stringify(value).replace(/</g, "\\u003c");
}

export default function Warehouse({ data }: PageProps<Data>) {
  const openTickets = data.tickets.filter((ticket) =>
    ["queued", "running", "awaiting_human"].includes(ticket.mission?.status ?? ticket.status)
  ).length;
  const refunded = data.refunds;
  const totalGrams = data.orders.reduce((sum, order) => sum + order.grams, 0);
  return (
    <div class="page page-warehouse">
      <section class="hero">
        <div>
          <p class="eyebrow">live parcel operations</p>
          <h1>Every parcel, <em>accounted for.</em></h1>
          <p class="lede">
            A real Quarkus + Hibernate back office, a Three.js warehouse and a Factory that turns
            improvement tickets into reviewed pull requests.
          </p>
        </div>
        <div class="hero-actions">
          <a class="button primary" href="/tickets">File a Factory ticket →</a>
          <a class="button ghost" href="/pulls">Review pull requests ({data.summary.openPulls})</a>
        </div>
      </section>

      <section class="kpis" aria-label="Warehouse summary">
        <div class="kpi"><span>orders</span><strong>{data.summary.orders}</strong><small>{totalGrams.toLocaleString("en-US")} g in transit</small></div>
        <div class="kpi"><span>delivered</span><strong>{data.summary.counts.delivered ?? 0}</strong><small>refund window 30 days</small></div>
        <div class="kpi"><span>refunded</span><strong>{data.summary.refunds}</strong><small>{money(data.summary.refundedCents)} returned</small></div>
        <div class="kpi"><span>open Factory tickets</span><strong>{openTickets}</strong><small>open or under review</small></div>
        <div class="kpi accent"><span>merged PRs</span><strong>{data.summary.mergedPulls}</strong><small>every one tested and reviewed</small></div>
      </section>

      <section class="warehouse-grid">
        <div class="panel scene-panel">
          <div class="panel-head">
            <div>
              <p class="eyebrow">three.js / sorting floor</p>
              <h2>Live parcel map</h2>
            </div>
            <div class="legend">
              <span><i class="dot packing" />packing</span>
              <span><i class="dot transit" />in transit</span>
              <span><i class="dot delivered" />delivered</span>
              <span><i class="dot refunded" />refunded</span>
            </div>
          </div>
          <div id="warehouse-canvas" class="warehouse-canvas" aria-label="Interactive three dimensional parcel warehouse">
            <div class="scene-loading">Loading the floor…</div>
          </div>
          <div id="parcel-detail" class="parcel-detail">
            <span class="detail-label">selected parcel</span>
            <strong>Click a parcel</strong>
            <p>Drag to orbit, scroll to zoom. The scene mirrors the live order table.</p>
          </div>
        </div>

        <aside class="panel side-panel">
          <div class="panel-head">
            <div>
              <p class="eyebrow">attention</p>
              <h2>Refund desk</h2>
            </div>
            <span class={`signal ${data.backendOnline ? "ok" : "bad"}`}>{data.backendOnline ? "api online" : "api offline"}</span>
          </div>
          <p class="muted">
            Refunds are delivered-only and must be requested within 30 days. Shipping is excluded,
            and a written reason is required for refunds above €200.
          </p>
          {refunded.length === 0
            ? <p class="empty">No refund yet.</p>
            : (
              <ul class="refund-list">
                {refunded.map((order) => (
                  <li key={order.id}>
                    <div>
                      <strong>{order.id}</strong>
                      <span>{order.customer}</span>
                    </div>
                    <div>{refundNote(order)} · {shortDate(order.refundedAt)}</div>
                  </li>
                ))}
              </ul>
            )}
          <a class="text-link" href="/tickets">File an improvement ticket →</a>
          <a class="text-link" href="/api/refunds.csv" download="refunds.csv" title="Download every refunded order as CSV">Download all refunds (CSV)</a>
        </aside>
      </section>

      <section class="panel orders-panel">
        <div class="panel-head">
          <div>
            <p class="eyebrow">hibernate / orders</p>
            <h2>Order ledger</h2>
          </div>
          <span class="mono">{money(data.summary.revenueCents)} recognised</span>
        </div>
        <div class="table-wrap">
          <table class="orders">
            <thead>
              <tr>
                <th>order</th><th>customer</th><th>parcel</th><th>weight</th><th>value</th><th>status</th><th>refund note</th><th />
              </tr>
            </thead>
            <tbody>
              {data.orders.map((order) => {
                const canRefund = order.status === "DELIVERED";
                const reason = order.refundReason;
                return (
                  <tr key={order.id} data-order={order.id}>
                    <td class="mono">{order.id}</td>
                    <td><strong>{order.customer}</strong><small>{order.city}</small></td>
                    <td>{order.item}</td>
                    <td class="mono">{order.grams.toLocaleString("en-US")} g</td>
                    <td class="mono">{money(order.totalCents)}</td>
                    <td><span class={`status ${order.status.toLowerCase()}`}>{statusLabel(order.status)}</span></td>
                    <td class={reason ? "reason" : "muted"}>{reason || (order.status === "REFUNDED" ? "—" : "")}</td>
                    <td>
                      {canRefund
                        ? <button class="mini-button" data-refund={order.id} data-value={order.totalCents}>Refund</button>
                        : null}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      <script id="warehouse-data" type="application/json" dangerouslySetInnerHTML={{ __html: safeJson(data.orders) }} />
      <script type="module" src="/warehouse.js" />
    </div>
  );
}
