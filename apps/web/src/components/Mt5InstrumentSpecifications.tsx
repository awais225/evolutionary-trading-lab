import { useCallback, useEffect, useState } from 'react'
import { api } from '../api/client'
import { useInstrument } from '../state/instrument'

const GROUPS: [string, [string, string][]][] = [
  ['A. Instrument', [['symbol','Symbol'],['description','Description'],['currency_base','Base currency'],['currency_profit','Profit currency'],['currency_margin','Margin currency'],['trade_calc_mode','Calculation mode'],['chart_mode','Chart price mode']]],
  ['B. Price and Contract', [['digits','Digits'],['point','Point'],['pip_size','Pip size (derived)'],['trade_tick_size','Tick size'],['trade_tick_value_profit','Tick value profit'],['trade_tick_value_loss','Tick value loss'],['trade_contract_size','Contract size']]],
  ['C. Trading Volume', [['volume_min','Minimum volume'],['volume_max','Maximum volume'],['volume_step','Volume step'],['volume_limit','Volume limit']]],
  ['D. Spread and Execution', [['bid','Current bid'],['ask','Current ask'],['spread','Current spread (points)'],['spread_float','Floating spread'],['trade_exemode','Execution mode'],['filling_mode','Filling modes'],['stops_level','Stops level'],['freeze_level','Freeze level'],['trade_mode','Trading mode']]],
  ['E. Swap', [['swap_mode','Swap type'],['swap_long','Long swap'],['swap_short','Short swap'],['swap_rollover3days','Triple swap day']]],
  ['F. Margin', [['margin_initial','Initial margin'],['margin_maintenance','Maintenance margin'],['leverage','Account leverage'],['currency','Account currency']]],
]

export function Mt5InstrumentSpecifications() {
  const { instrument } = useInstrument()
  const [view, setView] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const load = useCallback(async (refresh = false) => {
    setBusy(true); setError('')
    try { setView(refresh ? await api.metadata.refresh(instrument) : await api.metadata.get(instrument)) }
    catch (e) {
      const message = e instanceof Error ? e.message : String(e)
      if (refresh) { try { setView(await api.metadata.get(instrument)); setError(`Refresh failed; showing saved snapshot (not current): ${message}`) }
        catch { setError(message) } }
      else setError(message)
    }
    finally { setBusy(false) }
  }, [instrument])
  useEffect(() => { void load() }, [load])
  const snap = view?.snapshot
  const f = snap?.fields ?? {}
  const raw = snap?.raw?.symbol_info ?? {}
  const tick = snap?.tick ?? snap?.raw?.tick ?? {}
  const account = snap?.account ?? {}
  const source = (key: string) => key === 'pip_size' ? 'DERIVED' : f[key] != null || raw[key] != null || tick[key] != null || account[key] != null ? 'MT5' : 'UNAVAILABLE'
  const value = (key: string) => key === 'pip_size' ? snap?.derived?.pip_size : key === 'bid' || key === 'ask' ? tick[key] : key === 'leverage' ? account.leverage : key === 'currency' ? account.currency : f[key] ?? raw[key] ?? tick[key]
  return <section className="card data-section" aria-labelledby="mt5-spec-label">
    <div className="card-title" id="mt5-spec-label">MT5 INSTRUMENT SPECIFICATIONS <span className="faint">{instrument}</span>
      <button className="btn btn-xs" style={{ float: 'right' }} disabled={busy} onClick={() => void load(true)}>{busy ? 'Refreshing…' : 'REFRESH FROM MT5'}</button>
    </div>
    <div className="faint micro">Connection: {snap ? (snap.connection_current ? 'CONNECTED / CURRENT' : `DISCONNECTED OR STALE (${snap.connection_status ?? 'unknown'})`) : 'No saved specification'} · Source: {snap?.source ?? 'UNAVAILABLE'} · Last refresh: {snap?.retrieved_at ?? '—'} · Broker/server: {snap?.broker_server ?? '—'} · Symbol available: {snap ? 'yes (snapshot)' : 'unknown'}</div>
    {error && <p role="alert" className="error-text">{error}</p>}
    {!snap && !error && <p className="faint">No MT5 specification snapshot for {instrument}. Connect MT5, then refresh.</p>}
    {snap && <div className="mt5-spec-grid">{GROUPS.map(([group, fields]) => <div className="mt5-spec-group" key={group}><strong>{group}</strong><table className="node-table"><tbody>{fields.map(([key,label]) => <tr key={key}><td>{label}</td><td>{String(value(key) ?? 'Unavailable')}</td><td className="faint micro">{source(key)}</td></tr>)}</tbody></table></div>)}</div>}
    <p className="faint micro">Direct MT5 values are labelled MT5; pip size is explicitly derived; unavailable values remain unavailable. Snapshot values are broker/server and symbol specific.</p>
  </section>
}
