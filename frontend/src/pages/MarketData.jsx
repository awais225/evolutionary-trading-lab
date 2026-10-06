import React, { useEffect, useState } from "react";
import { api, fmt } from "../api.js";
import { arr } from "../lib/safe.js";
import { ErrorNote, Spinner } from "../components/common.jsx";

export default function MarketData() {
  const [ds, setDs] = useState(null);
  const [sel, setSel] = useState("");
  const [bars, setBars] = useState(null);
  const [feats, setFeats] = useState(null);
  const [tick, setTick] = useState(null);
  const [form, setForm] = useState({ symbol: "XAUUSD", timeframe: "M15", months: "" });
  const [err, setErr] = useState(null);
  const [msg, setMsg] = useState("");

  const loadDs = () => api.datasets().then((r) => {
    setDs(r);
    if (!sel && r.datasets?.length) setSel(r.datasets[0].id);
  }).catch((e) => setErr(e.message));

  useEffect(() => { loadDs(); const t = setInterval(loadDs, 15000); return () => clearInterval(t); }, []);
  useEffect(() => {
    if (!sel) return;
    api.bars(sel, 60).then(setBars).catch(() => {});
    api.features(sel).then(setFeats).catch(() => {});
  }, [sel]);
  useEffect(() => {
    const t = setInterval(() => {
      api.ticks(form.symbol, 1).then((r) => setTick(r.ticks?.[0])).catch(() => {});
    }, 1500);
    api.ticks(form.symbol, 1).then((r) => setTick(r.ticks?.[0])).catch(() => {});
    return () => clearInterval(t);
  }, [form.symbol]);

  const ingest = async () => {
    setMsg("ingesting…");
    try {
      const body = { symbol: form.symbol, timeframe: form.timeframe };
      if (form.months) body.months = parseFloat(form.months);
      const r = await api.ingest(body);
      setMsg(`OK: ${r.dataset_id} (${r.bars} bars, source ${r.source}${r.cached ? ", cached" : ""})`);
      loadDs();
    } catch (e) { setMsg("ERROR: " + e.message); }
  };

  return (
    <div>
      <h2 className="page-title">Market Data</h2>
      <div className="page-sub">
        Historical data is downloaded ONCE per (symbol, timeframe, window), stored as Parquet and
        reused by every strategy through the feature cache. Additional symbols (BTCUSD, NAS100,
        EURUSD…) can be ingested without touching the core architecture.
      </div>
      <ErrorNote err={err} />

      <div className="grid cols-2" style={{ gridTemplateColumns: "1fr 1fr", marginBottom: 14 }}>
        <div className="panel">
          <h3>Datasets</h3>
          {!ds ? <Spinner /> : (
            <table className="tbl">
              <thead><tr><th>select</th><th>symbol</th><th>tf</th><th>bars</th><th>range</th><th>source</th><th>features</th></tr></thead>
              <tbody>
                {arr(ds?.datasets).map((d) => (
                  <tr key={d.id} onClick={() => setSel(d.id)}
                      style={{ background: sel === d.id ? "#1a2132" : undefined }}>
                    <td><input type="radio" checked={sel === d.id} readOnly style={{width:"auto"}}/></td>
                    <td>{d.symbol}</td><td>{d.timeframe}</td><td>{fmt.num(d.bars, 0)}</td>
                    <td className="mono" style={{fontSize:10}}>{fmt.dt(d.start_ts)} → {fmt.dt(d.end_ts)}</td>
                    <td><span className={"pill " + (d.source === "SIMULATOR" ? "sim" : "real")}>{d.source}</span></td>
                    <td>{d.cached_features}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <div className="muted" style={{fontSize:11.5, marginTop:8}}>
            active bridge: <b>{ds?.bridge}</b>{ds?.simulated ? " (SYNTHETIC DATA — not MT5)" : " (REAL MT5)"}
          </div>
        </div>

        <div className="panel">
          <h3>Ingest / download</h3>
          <div className="settings-grid">
            <label className="fld">symbol
              <input value={form.symbol} list="syms"
                     onChange={(e) => setForm({ ...form, symbol: e.target.value.toUpperCase() })}/>
              <datalist id="syms">
                <option value="XAUUSD"/><option value="BTCUSD"/><option value="NAS100"/><option value="EURUSD"/>
              </datalist>
            </label>
            <label className="fld">timeframe
              <select value={form.timeframe} onChange={(e) => setForm({ ...form, timeframe: e.target.value })}>
                {["M1","M5","M15","M30","H1"].map((t) => <option key={t}>{t}</option>)}
              </select>
            </label>
            <label className="fld">months (optional)
              <input value={form.months} placeholder="default from config"
                     onChange={(e) => setForm({ ...form, months: e.target.value })}/>
            </label>
          </div>
          <button className="btn primary" style={{marginTop:10}} onClick={ingest}>⬇ Download & cache</button>
          {msg && <div className="muted mono" style={{marginTop:8, fontSize:11.5}}>{msg}</div>}

          <h3 style={{marginTop:16}}>Live tick ({form.symbol})</h3>
          {tick ? (
            <div className="mono" style={{fontSize:13}}>
              bid <b>{fmt.num(tick.bid, 2)}</b> · ask <b>{fmt.num(tick.ask, 2)}</b> ·
              spread {fmt.num((tick.ask - tick.bid) / 0.01, 1)} pt · {fmt.ts(tick.ts)} ·
              <span className={"pill " + (tick.source === "SIMULATOR" ? "sim" : "real")} style={{marginLeft:6}}>{tick.source}</span>
            </div>
          ) : <Spinner />}
        </div>
      </div>

      <div className="grid cols-2" style={{ gridTemplateColumns: "1.4fr 1fr" }}>
        <div className="panel">
          <h3>Latest bars — {sel || "select a dataset"}</h3>
          {bars && (
            <div className="scroll-y" style={{ maxHeight: 380 }}>
              <table className="tbl">
                <thead><tr><th>time</th><th>open</th><th>high</th><th>low</th><th>close</th><th>bid</th><th>ask</th><th>spread</th><th>vol</th><th>session</th></tr></thead>
                <tbody>
                  {arr(bars?.bars).slice().reverse().map((b, i) => (
                    <tr key={i} style={{ cursor: "default" }}>
                      <td className="mono" style={{fontSize:10}}>{fmt.dt(b.ts)}</td>
                      <td>{fmt.num(b.open)}</td><td>{fmt.num(b.high)}</td>
                      <td>{fmt.num(b.low)}</td><td>{fmt.num(b.close)}</td>
                      <td>{fmt.num(b.bid)}</td><td>{fmt.num(b.ask)}</td>
                      <td>{fmt.num(b.spread, 1)}</td><td>{b.tick_volume}</td><td>{b.session}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="muted" style={{fontSize:11}}>showing last {bars.bars.length} of {fmt.num(bars.total_bars,0)} bars</div>
            </div>
          )}
        </div>
        <div className="panel">
          <h3>Cached feature arrays ({feats?.cached?.length ?? 0})</h3>
          <div className="scroll-y" style={{ maxHeight: 400 }}>
            <div className="mono" style={{ fontSize: 11, lineHeight: 1.9 }}>
              {arr(feats?.cached).map((f) => <span key={f} className="pill RETIRED" style={{margin:"0 4px 4px 0"}}>{f}</span>)}
            </div>
          </div>
          <div className="muted" style={{fontSize:11.5, marginTop:8}}>
            Strategies consume these precomputed arrays — no indicator is recomputed per strategy.
          </div>
        </div>
      </div>
    </div>
  );
}
