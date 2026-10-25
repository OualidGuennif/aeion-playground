import type { Handlers, PageProps } from "$fresh/server.ts";
import { backendUrl, getJson, type Pull } from "../lib/api.ts";
import { mergeable, money, statusLabel } from "../lib/format.ts";

interface Data {
  pulls: Pull[];
  online: boolean;
}

export const handler: Handlers<Data> = {
  async GET(_request, ctx) {
    const pulls = await getJson<Pull[]>(`${backendUrl}/api/pulls`, []);
    return ctx.render({ pulls, online: Array.isArray(pulls) });
  },
};

export default function Pulls({ data }: PageProps<Data>) {
  const open = data.pulls.filter((pull) => pull.status === "OPEN" || pull.status === "APPROVED");
  const merged = data.pulls.filter((pull) => pull.status === "MERGED");
  return (
    <div class="page page-pulls">
      <section class="hero compact">
        <div>
          <p class="eyebrow">independent review</p>
          <h1>Read the diff. <em>Then merge.</em></h1>
          <p class="lede">
            Every accepted mission becomes a pull request with its checks, the reviewer's verdict and
            the exact local release commit. Low-risk green requests can merge automatically; anything
            risky waits for you.
          </p>
        </div>
        <div class="hero-actions">
          <button class="button ghost" data-sync data-auto-merge="true">Sync completed missions</button>
        </div>
      </section>

      <section class="kpis small">
        <div class="kpi"><span>waiting</span><strong>{open.length}</strong><small>open or approved</small></div>
        <div class="kpi"><span>merged</span><strong>{merged.length}</strong><small>reviewed releases</small></div>
        <div class="kpi"><span>tests</span><strong>{data.pulls.reduce((sum, pull) => sum + (pull.checks?.tests?.total ?? 0), 0)}</strong><small>across all pull requests</small></div>
        <div class="kpi accent"><span>api</span><strong>{data.online ? "up" : "down"}</strong><small>quarkus review ledger</small></div>
      </section>

      {data.pulls.length === 0
        ? (
          <section class="panel">
            <p class="empty">
              No pull request yet. Send a ticket to the Factory, then sync when the mission is sealed.
            </p>
          </section>
        )
        : (
          <section class="pull-list">
            {data.pulls.map((pull) => {
              const tests = pull.checks?.tests;
              const canMerge = mergeable(pull);
              return (
                <article class={`panel pull-card ${pull.status.toLowerCase()}`} id={pull.id} key={pull.id}>
                  <div class="pull-head">
                    <div>
                      <div class="pull-kicker">
                        <span class="mono">{pull.id}</span>
                        <span class={`status ${pull.status.toLowerCase()}`}>{statusLabel(pull.status)}</span>
                        <span class="mono">{pull.risk} risk</span>
                      </div>
                      <h2>{pull.title}</h2>
                      <p class="mono muted">{pull.branch} → {pull.base || "main"}</p>
                    </div>
                    <div class="pull-actions">
                      {pull.status === "MERGED"
                        ? <span class="merged-stamp">merged {pull.mergedBy ? `by ${pull.mergedBy}` : ""}</span>
                        : (
                          <>
                            <button class="mini-button" data-review={pull.id}>Request changes</button>
                            <button class="button small primary" data-merge={pull.id} disabled={!canMerge}>
                              Merge PR →
                            </button>
                          </>
                        )}
                    </div>
                  </div>

                  <div class="pull-grid">
                    <div class="pull-evidence">
                      <h3>Checks</h3>
                      <ul class="checks">
                        <li class={tests && tests.total && tests.passed === tests.total ? "pass" : "fail"}>
                          <span>trusted tests</span>
                          <strong>{tests?.passed ?? 0}/{tests?.total ?? 0}</strong>
                        </li>
                        <li class={pull.checks?.gateApproved ? "pass" : "fail"}>
                          <span>gate</span>
                          <strong>{pull.checks?.gateDecision || (pull.checks?.gateApproved ? "approved" : "pending")}</strong>
                        </li>
                        <li class="pass">
                          <span>commit</span>
                          <strong class="mono">{(pull.commitHash ?? "—").slice(0, 10)}</strong>
                        </li>
                        <li class="pass">
                          <span>ticket</span>
                          <strong class="mono">{pull.ticketId || "unlinked"}</strong>
                        </li>
                      </ul>
                      <h3>Independent review</h3>
                      <p class={`review ${pull.review?.verdict === "OK" ? "ok" : "changes"}`}>
                        {pull.review?.verdict || "pending"} · {pull.review?.model || pull.review?.reviewer || "sentinel"}
                      </p>
                      <p class="muted">{pull.review?.comment || "No reviewer comment."}</p>
                      {pull.mergedAt ? <p class="muted">Merged {pull.mergedAt.slice(0, 19).replace("T", " ")} UTC</p> : null}
                    </div>
                    <div class="pull-diff">
                      <h3>Diff</h3>
                      <details>
                        <summary>{pull.diff ? `${pull.diff.split("\n").length} lines` : "no diff"}</summary>
                        <pre>{pull.diff || "No diff captured."}</pre>
                      </details>
                    </div>
                  </div>
                </article>
              );
            })}
          </section>
        )}
    </div>
  );
}
