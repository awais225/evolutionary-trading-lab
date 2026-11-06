import { useEffect, useState } from 'react'
import { api, ApiError } from '../api/client'
import type { SettingsOut } from '../api/types'

type FormState = {
  log_level: string
  research_max_sources: number
  research_max_minutes: number
  research_max_tokens: number
  research_require_evidence: boolean
}

export function Settings() {
  const [data, setData] = useState<SettingsOut | null>(null)
  const [form, setForm] = useState<FormState | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    ;(async () => {
      try {
        const res = await api.settings.get()
        if (!alive) return
        setData(res)
        const s = res.settings as Record<string, unknown>
        setForm({
          log_level: String(s.log_level ?? 'INFO'),
          research_max_sources: Number(s.research_max_sources ?? 40),
          research_max_minutes: Number(s.research_max_minutes ?? 30),
          research_max_tokens: Number(s.research_max_tokens ?? 200000),
          research_require_evidence: Boolean(s.research_require_evidence ?? true),
        })
      } catch (err) {
        if (alive) setError(err instanceof ApiError ? err.message : 'failed to load settings')
      }
    })()
    return () => { alive = false }
  }, [])

  const save = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!form) return
    setSaving(true)
    setSaved(null)
    setError(null)
    try {
      const res = await api.settings.put({ ...form })
      setData(res)
      setSaved('Settings saved.')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'save failed')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div>
      <div className="page-head">
        <div className="page-eyebrow">Configuration</div>
        <h1 className="page-title">SETTINGS</h1>
        <p className="page-sub">Infrastructure configuration comes from the environment / .env file (never committed). Research budgets and log level are persisted in the registry database. MT5 connection, instruments and AI providers expand in later phases.</p>
      </div>

      {error && <div className="banner warn" role="alert"><span aria-hidden="true">⚠</span><span>{error}</span></div>}
      {saved && <div className="banner" role="status"><span aria-hidden="true">✓</span><span>{saved}</span></div>}

      <div className="panel-grid">
        <form className="card" onSubmit={save}>
          <div className="card-title">Application preferences</div>
          {!form && !error && <div className="state"><div className="spinner" /><div className="state-title">Loading settings…</div></div>}
          {form && (
            <>
              <div className="field">
                <label htmlFor="log_level">Log level</label>
                <select id="log_level" className="input" value={form.log_level}
                        onChange={(e) => setForm({ ...form, log_level: e.target.value })}>
                  {['DEBUG', 'INFO', 'WARNING', 'ERROR'].map((l) => <option key={l} value={l}>{l}</option>)}
                </select>
              </div>
              <div className="field" style={{ marginTop: 12 }}>
                <label htmlFor="max_sources">Research · max sources per task</label>
                <input id="max_sources" type="number" min={1} max={500} className="input mono"
                       value={form.research_max_sources}
                       onChange={(e) => setForm({ ...form, research_max_sources: Number(e.target.value) })} />
              </div>
              <div className="field" style={{ marginTop: 12 }}>
                <label htmlFor="max_minutes">Research · max minutes per task</label>
                <input id="max_minutes" type="number" min={1} max={600} className="input mono"
                       value={form.research_max_minutes}
                       onChange={(e) => setForm({ ...form, research_max_minutes: Number(e.target.value) })} />
              </div>
              <div className="field" style={{ marginTop: 12 }}>
                <label htmlFor="max_tokens">Research · token budget per task</label>
                <input id="max_tokens" type="number" min={1000} max={2000000} className="input mono"
                       value={form.research_max_tokens}
                       onChange={(e) => setForm({ ...form, research_max_tokens: Number(e.target.value) })} />
              </div>
              <label style={{ display: 'flex', gap: 10, alignItems: 'center', marginTop: 14, cursor: 'pointer' }}>
                <input type="checkbox" checked={form.research_require_evidence}
                       onChange={(e) => setForm({ ...form, research_require_evidence: e.target.checked })} />
                <span>Require evidence quote + source URL for every finding (recommended)</span>
              </label>
              <button className="btn btn-primary" type="submit" disabled={saving} style={{ marginTop: 16 }}>
                {saving ? 'Saving…' : 'Save settings'}
              </button>
            </>
          )}
        </form>

        <div className="card">
          <div className="card-title">Environment (read-only)</div>
          {data?.environment ? (
            <div className="mono" style={{ fontSize: 12, display: 'grid', gap: 10 }}>
              {Object.entries(data.environment).map(([k, v]) => (
                <div key={k}>
                  <div className="faint" style={{ fontSize: 9.5, letterSpacing: 1.4, textTransform: 'uppercase' }}>{k}</div>
                  <div style={{ wordBreak: 'break-all' }}>{v}</div>
                </div>
              ))}
            </div>
          ) : <div className="state">Unavailable</div>}
          <div className="faint" style={{ marginTop: 16, fontSize: 11, lineHeight: 1.6 }}>
            Secrets (MT5 password, provider keys) never live in files or this page. On Windows they belong in the OS credential store / environment; this UI will only ever show whether they are configured — never their values.
          </div>
        </div>
      </div>
    </div>
  )
}
