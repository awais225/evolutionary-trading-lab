// V5.2 §9 — LIVE TESTING EXECUTION EVENT
//
// The one place that shows, for the most recent order the live engine actually
// attempted: the stage it reached, the broker's own retcode + comment, the
// order / deal / position tickets, the requested vs executed volume, the
// requested vs execution price, the requested vs broker SL/TP, the client order
// id used for idempotency, the duplicate-guard state and — when the broker gave
// nothing back — the raw diagnostic phase and `mt5.last_error` description.
//
// Everything is read from the backend (`/api/live-testing/status` →
// `recent_events` plus the persisted trade rows). Nothing here is derived from a
// dashboard snapshot, and "HTTP 200" is never used as proof that an order was
// executed: a row says EXECUTED only when the broker result was captured and
// the position was verified in MT5.
import React, { useMemo } from "react";
import { Card, Badge } from "./ui";
import { arr, objOrNull, txt } from "../lib/safe.js";

const ORDER_STAGES = ["ORDER SENT", "BROKER RESPONSE", "POSITION VERIFIED",
                      "UNKNOWN - VERIFY MT5", "REJECTED", "BLOCKED", "POSITION CLOSED"];

const TONE = {
  POSITION_OPEN: "var(--green)",
  PENDING_ORDER: "var(--green)",
  EXECUTED_UNCONFIRMED: "var(--amber)",
  UNKNOWN: "var(--red)",
  REJECTED: "var(--red)",
  BLOCKED: "var(--amber)",
  CLOSED_MISSING: "var(--muted)",
};

function KV({ k, v, tone, mono = true }) {
  return (
    <div className="item">
      <span className="k">{k}</span>
      <span className={"v" + (mono ? " mono" : "")} style={tone ? { color: tone } : undefined}>
        {v === null || v === undefined || v === "" ? <span className="muted">not reported</span> : String(v)}
      </span>
    </div>
  );
}

export default function LiveExecutionEvent({ engine, trades }) {
  const events = arr(engine?.recent_events);
  const latest = useMemo(() => {
    for (let i = events.length - 1; i >= 0; i -= 1) {
      const ev = objOrNull(events[i]);
      if (ev && ORDER_STAGES.includes(String(ev.stage || "").toUpperCase())) return ev;
    }
    return null;
  }, [events]);
  const lastTrade = arr(trades?.trades || trades?.rows || trades)[0] || null;
  const d = objOrNull(latest?.detail) || {};
  const out = objOrNull(d.order_send) || {};
  const status = String(d.status || latest?.status || "").toUpperCase();

  return (
    <Card title="LIVE EXECUTION EVENT — THE ACTUAL ORDER ATTEMPT" right={
      <span className="muted" style={{ fontSize: 10 }}>
        read from the engine's own stage log · HTTP 200 is never proof of execution
      </span>
    }>
      {!latest && !lastTrade && (
        <div className="muted" style={{ fontSize: 11 }}>
          No live-test order has been attempted yet. Activate Live Testing, start a node and
          the stage log will fill this block with the real broker result — or with the exact
          reason nothing was sent.
        </div>
      )}

      {latest && (
        <>
          <div className="kit-strip" style={{ border: "none", padding: 0, flexWrap: "wrap" }}>
            <KV k="Stage" v={latest.stage} />
            <KV k="Status" v={latest.status || d.status} tone={TONE[status]} />
            <KV k="Node" v={latest.node_id != null ? `Node_${latest.node_id}` : null} />
            <KV k="Symbol / side" v={`${txt(latest.symbol, "—")} ${txt(latest.side, "")}`.trim()} />
            <KV k="Time" v={latest.ts_iso || (latest.ts ? new Date(latest.ts * 1000).toISOString() : null)} />
          </div>
          <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>{latest.message}</div>

          <div className="kit-strip" style={{ border: "none", padding: 0, marginTop: 6, flexWrap: "wrap" }}>
            <KV k="Broker retcode" v={d.retcode} />
            <KV k="Broker message" v={d.message || d.reason} />
            <KV k="Result class" v={d.result_class} />
            <KV k="Diagnostic phase" v={d.diagnostic_phase} />
            <KV k="Safe to retry" v={d.safe_to_retry === undefined ? null : String(d.safe_to_retry)} />
            <KV k="Retries" v={d.retries} />
          </div>

          <div className="kit-strip" style={{ border: "none", padding: 0, marginTop: 6, flexWrap: "wrap" }}>
            <KV k="Order ticket" v={d.order_ticket} />
            <KV k="Deal ticket" v={d.deal_ticket} />
            <KV k="Position ticket" v={d.position_ticket} />
            <KV k="Client order id" v={d.client_order_id} />
          </div>

          <div className="kit-strip" style={{ border: "none", padding: 0, marginTop: 6, flexWrap: "wrap" }}>
            <KV k="Requested volume" v={d.volume} />
            <KV k="Executed volume" v={d.exec_volume ?? d.volume_executed} />
            <KV k="Requested price" v={d.price ?? d.requested_price} />
            <KV k="Execution price" v={d.exec_price} />
            <KV k="Requested SL / TP" v={`${txt(d.sl ?? d.sl_requested, "—")} / ${txt(d.tp ?? d.tp_requested, "—")}`} />
            <KV k="Broker SL / TP" v={`${txt(d.sl_broker, "—")} / ${txt(d.tp_broker, "—")}`} />
            <KV k="SL/TP verified" v={d.sl_tp_verified === undefined ? null : String(d.sl_tp_verified)} />
          </div>

          {(d.order_send?.last_error || d.diagnostic?.last_error_name) && (
            <div className="error-note" style={{ marginTop: 6, fontSize: 11 }}>
              the terminal returned no result object — <b>mt5.last_error</b>:{" "}
              {txt(d.diagnostic?.last_error_name, JSON.stringify(objOrNull(d.order_send)?.last_error))}
              {d.diagnostic?.phase ? ` · phase ${d.diagnostic.phase}` : ""}
            </div>
          )}
          {status === "UNKNOWN" && (
            <div className="error-note" style={{ marginTop: 6, fontSize: 11 }}>
              <b>EXECUTION UNKNOWN — VERIFY IN MT5.</b> The order was sent once and its result could
              not be resolved from the terminal, so it is never resent automatically. Confirm the
              position/order by ticket in the terminal, then reconcile below.
            </div>
          )}
        </>
      )}

      {lastTrade && (
        <div style={{ marginTop: 10 }}>
          <div className="muted" style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: 1 }}>
            last persisted live-test record
          </div>
          <div className="kit-strip" style={{ border: "none", padding: 0, marginTop: 2, flexWrap: "wrap" }}>
            <KV
              k="Row"
              v={`#${txt(lastTrade.id, "?")} · ${txt(lastTrade.symbol, "?")} ${txt(lastTrade.side, "")} ${txt(lastTrade.volume, "")}`}
            />
            <KV k="Status" v={lastTrade.status} tone={TONE[String(lastTrade.status || "").toUpperCase()]} />
            <KV k="Order / deal / position" v={`${txt(lastTrade.order_ticket, "—")} / ${txt(lastTrade.deal_ticket, "—")} / ${txt(lastTrade.position_ticket, "—")}`} />
            <KV k="Retcode" v={lastTrade.retcode} />
            <KV k="Volume requested / executed" v={`${txt(lastTrade.volume, "—")} / ${txt(lastTrade.volume_executed, "—")}`} />
            <KV k="Price requested / executed" v={`${txt(lastTrade.price_requested, "—")} / ${txt(lastTrade.exec_price, "—")}`} />
            <KV k="SL / TP" v={`${txt(lastTrade.sl, "—")} / ${txt(lastTrade.tp, "—")}`} />
            <KV k="Client order id" v={lastTrade.client_order_id} />
          </div>
        </div>
      )}

      <div className="muted" style={{ fontSize: 10, marginTop: 6 }}>
        A manual order and an automated live-test order both travel the one execution path
        (order_check → order_send); this block shows the automated one. A row only reads
        POSITION_OPEN after the broker result was captured and the position was verified in MT5.
      </div>
    </Card>
  );
}
