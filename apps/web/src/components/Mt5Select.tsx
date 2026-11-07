import { useEffect, useId, useRef, useState } from 'react'
import { api, ApiError } from '../api/client'
import type { Mt5ConnectionResult, Mt5TerminalsOut } from '../api/types'

/**
 * MT5 terminal/account selector — themed custom listbox (mirrors InstrumentSelect).
 * One terminal hosts exactly ONE logged-in account; "selection" here means
 * "use this terminal for the Live Testing context" — it NEVER changes the
 * instrument, nodes, fingerprints, research results or market data.
 * Account metrics stay display-only; no order is ever placed from this UI.
 */
export function Mt5Select() {
  const [status, setStatus] = useState<Mt5ConnectionResult | null>(null)
  const [terminals, setTerminals] = useState<Mt5TerminalsOut | null>(null)
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [customPath, setCustomPath] = useState('')
  const wrapRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const labelId = useId()

  const refreshStatus = async () => {
    try {
      setStatus(await api.mt5.status())
      setError(null)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'MT5 status unavailable')
    }
  }

  useEffect(() => {
    let alive = true
    const poll = async () => { if (alive) await refreshStatus() }
    poll()
    const t = window.setInterval(poll, 20000)
    return () => { alive = false; window.clearInterval(t) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const openMenu = async () => {
    setOpen((v) => !v)
    if (!open) {
      try { setTerminals(await api.mt5.terminals()) } catch { /* panel shows status anyway */ }
    }
  }

  useEffect(() => {
    if (!open) return
    const onMouseDown = (e: MouseEvent) => { if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false) }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') { setOpen(false); triggerRef.current?.focus() } }
    document.addEventListener('mousedown', onMouseDown)
    document.addEventListener('keydown', onKey)
    return () => { document.removeEventListener('mousedown', onMouseDown); document.removeEventListener('keydown', onKey) }
  }, [open])

  const selectTerminal = async (path?: string) => {
    setBusy(true); setError(null)
    try {
      setStatus(await api.mt5.connect(path))
      setTerminals((t) => (t ? { ...t, saved_terminal_path: path ?? null } : t))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'MT5 connection failed')
    } finally { setBusy(false) }
  }

  const disconnect = async () => {
    setBusy(true); setError(null)
    try {
      await api.mt5.disconnect()
      await refreshStatus()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'disconnect failed')
    } finally { setBusy(false) }
  }

  const account = status?.account ?? null
  const terminal = status?.terminal ?? null
  const connected = Boolean(status?.ok && account)
  const loginRequired = Boolean(status?.login_required || status?.state === 'CONNECTED')
  const lifecycleState = status?.state ?? (connected ? 'ACCOUNT_LOADED' : status?.package_available ? 'NOT_CONNECTED' : 'UNAVAILABLE')
  const dot = connected ? 'ok' : status?.package_available ? 'warn' : 'err'
  const triggerLabel = connected
    ? `#${account?.login ?? '—'}`
    : loginRequired
      ? 'LOGIN REQUIRED'
      : status?.package_available
        ? lifecycleState.replaceAll('_', ' ')
        : 'UNAVAILABLE'
  const triggerSub = connected
    ? `${account?.server ?? terminal?.company ?? ''} · ${account?.trade_mode_name ?? ''} · ${account?.currency ?? ''}`.replace(/\s+·\s*$/, '').replace(/^\s*·\s*/, '')
    : loginRequired
      ? 'log in inside MT5, then reconnect'
      : (status?.import_error ? 'package missing' : 'click to select terminal')

  return (
    <div ref={wrapRef} className="mt5-select-wrap" onKeyDown={(e) => { if (e.key === 'Escape' && open) { setOpen(false); triggerRef.current?.focus() } }}>
      <span className="mt5-select-label hide-sm" id={labelId}>MT5</span>
      <button ref={triggerRef} type="button" className="mt5-trigger" aria-labelledby={labelId}
              aria-haspopup="dialog" aria-expanded={open}
              title={connected
                ? `Connected to MT5 account #${account?.login} on ${account?.server} (Live Testing context only)`
                : 'MT5 not connected — click to select a terminal'}
              onClick={openMenu}>
        <span className={`mt5-dot ${dot}`} aria-hidden="true" />
        <span className="mt5-trigger-value">{triggerLabel}</span>
        <span className="mt5-trigger-sub hide-sm">{triggerSub}</span>
        <svg width="10" height="10" viewBox="0 0 12 12" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round"><path d="M2.5 4.5L6 8l3.5-3.5" /></svg>
      </button>
      {open && (
        <div className="mt5-panel" role="dialog" aria-modal="false" aria-label="MT5 terminal selection">
          <div className="mt5-panel-group">MT5 TERMINAL — select the account to use</div>

          {connected && account && (
            <div className="mt5-row selected" aria-disabled="true">
              <div className="mt5-row-main">
                <span>#{account.login} · {account.server}</span>
                <span className="mt5-row-sub">{account.name ?? 'Account holder unavailable'} · {account.company ?? ''} · {account.currency ?? ''} · leverage 1:{account.leverage ?? '—'}</span>
                <span className="mt5-row-sub">balance {account.balance ?? '—'} · equity {account.equity ?? '—'} · profit {account.profit ?? '—'} · margin {account.margin ?? '—'} · free {account.margin_free ?? '—'}{account.margin_level != null ? ` · level ${account.margin_level}%` : ''}</span>
                <span className="mt5-row-sub">trading {account.trade_allowed ? 'allowed' : 'not allowed'} · expert advisors {account.trade_expert ? 'allowed' : 'not allowed'} · state {lifecycleState}</span>
              </div>
              <span className={`mt5-row-badge ${String(account.trade_mode_name ?? '').toLowerCase()}`}>{account.trade_mode_name ?? 'UNKNOWN'}</span>
            </div>
          )}

          {terminals && !terminals.platform_windows && (
            <div className="mt5-panel-note">Terminal discovery runs on the Windows machine that hosts MT5. On this host, Hero1 reports MT5 as unavailable rather than simulating a connection. Use Windows with a logged-in terminal to select an account.</div>
          )}
          {terminals && terminals.import_error && (
            <div className="mt5-panel-note">MetaTrader5 package: {terminals.import_error}</div>
          )}
          <div className="mt5-panel-note">Connection state: <strong>{lifecycleState}</strong>{status?.launched ? ' · terminal launched by Hero1' : ''}</div>
          {loginRequired && (
            <div className="mt5-panel-note" role="status">The terminal is running but no account is loaded. Log in inside MetaTrader 5 (File → Login to Trade Account), then choose the terminal again. Hero1 never asks for or stores passwords.</div>
          )}
          {status?.warning && <div className="mt5-panel-note" role="status">{status.warning}</div>}
          {status?.error && !connected && <div className="mt5-panel-error" role="alert">{status.error}</div>}
          {terminals?.terminals.map((t) => (
            <button key={t.path} type="button" className={`mt5-row${terminals.saved_terminal_path === t.path ? ' selected' : ''}`}
                    disabled={busy}
                    onClick={() => selectTerminal(t.path)}>
              <div className="mt5-row-main">
                <span>{t.name}</span>
                <span className="mt5-row-sub">{t.path}</span>
              </div>
              <span className="mt5-row-badge">{t.state ?? (t.origin === 'config' ? 'SAVED' : 'DISCOVERED')}</span>
            </button>
          ))}
          {terminals && terminals.terminals.length === 0 && terminals.platform_windows && (
            <div className="mt5-panel-note">No terminals discovered in the standard install locations. If MT5 is installed elsewhere, paste the path to <code>terminal64.exe</code> (or its install folder) below.</div>
          )}
          <button type="button" className="mt5-row" disabled={busy} onClick={() => selectTerminal(undefined)}>
            <div className="mt5-row-main">
              <span>Auto / previously selected terminal</span>
              <span className="mt5-row-sub">initialize MT5 without a path (the terminal decides)</span>
            </div>
          </button>
          <form className="mt5-custom-path" onSubmit={(e) => { e.preventDefault(); if (customPath.trim()) selectTerminal(customPath.trim()) }}>
            <input className="input input-xs mono" style={{ width: '100%' }} value={customPath}
                   placeholder="C:\\Program Files\\MetaTrader 5\\terminal64.exe (custom install folder also accepted)"
                   aria-label="Custom terminal path"
                   onChange={(e) => setCustomPath(e.target.value)} />
            <button className="btn btn-ghost btn-xs" type="submit" disabled={busy || !customPath.trim()}>Use this path</button>
          </form>
          {error && <div className="mt5-panel-error" role="alert">{error}</div>}
          <div className="mt5-panel-actions">
            <button type="button" className="btn btn-ghost btn-xs" disabled={busy} onClick={() => refreshStatus()}>Refresh status</button>
            <button type="button" className="btn btn-ghost btn-xs" disabled={busy || !connected} onClick={disconnect}>Disconnect</button>
            <span className="mt5-panel-note" style={{ marginLeft: 'auto' }}>Live Testing context only</span>
          </div>
        </div>
      )}
    </div>
  )
}
