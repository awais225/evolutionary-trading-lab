import { useEffect, useState } from 'react'
import { api, ApiError } from '../api/client'
import type { SettingsOut } from '../api/types'
import { useTasks } from '../state/tasks'

type FormState = {
  log_level: string
  research_max_sources: number
  research_max_minutes: number
  research_max_tokens: number
  research_require_evidence: boolean
}

export function Settings() {
  const { refresh: refreshTasks } = useTasks()
  const [data, setData] = useState<SettingsOut | null>(null)
  const [processes,setProcesses]=useState<any>(null)
  const [processMessage,setProcessMessage]=useState('')
  const [deletePreview,setDeletePreview]=useState<any>(null)
  const [deletePhrase,setDeletePhrase]=useState('')
  const [actionBusy,setActionBusy]=useState(false)
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

  const refreshProcessStatus=async()=>{setActionBusy(true);try{const p=await api.system.processStatus();setProcesses(p);setProcessMessage('Process status refreshed from persisted backend tasks and host metrics.');refreshTasks()}catch(e){setProcessMessage(`Status refresh failed: ${String(e)}`)}finally{setActionBusy(false)}}
  const stopAll=async()=>{if(!window.confirm('Stop all supported HERO1 background jobs? New jobs are blocked until cancellation is acknowledged. No unrelated operating-system processes will be killed.'))return;setActionBusy(true);try{const p=await api.system.stopAll(30);setProcesses(p);setProcessMessage(p.message);refreshTasks()}catch(e){setProcessMessage(`Stop request failed: ${String(e)}`)}finally{setActionBusy(false)}}
  const requestShutdown=async()=>{if(!window.confirm('SHUTDOWN will stop the HERO1 backend after supported jobs acknowledge cancellation. The backend will stop and you will need to start HERO1 again. Continue?'))return;setActionBusy(true);try{const p=await api.system.shutdown('SHUTDOWN HERO1',30);setProcessMessage(p.message)}catch(e){setProcessMessage(`Shutdown was not completed: ${String(e)}`)}finally{setActionBusy(false)}}
  const showDeletePreview=async(category:string)=>{setDeletePhrase('');setActionBusy(true);try{setDeletePreview(await api.system.previewDelete(category))}catch(e){setProcessMessage(`Could not prepare deletion preview: ${String(e)}`)}finally{setActionBusy(false)}}
  const confirmDelete=async()=>{if(!deletePreview)return;const cat=deletePreview.category;const phrase=cat==='ALL'?'DELETE ALL DATA':cat==='LOGS'?'DELETE LOGS':`DELETE ${cat.replace('_',' ')} DATA`;if(deletePhrase!==phrase)return;setActionBusy(true);try{const result=await api.system.deleteData(cat,phrase);setProcessMessage(`Permanent deletion finished for ${cat}. Removed ${result.deleted_files?.length??0} file path(s). Refreshing dashboard…`);setDeletePreview(null);setDeletePhrase('');refreshTasks();window.location.reload()}catch(e){setProcessMessage(`Deletion failed; verify backend report before retrying: ${String(e)}`)}finally{setActionBusy(false)}}

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

      <section className="card" style={{ marginTop: 18 }} aria-labelledby="process-controls">
        <div className="card-title" id="process-controls">PROCESS CONTROLS</div>
        <p className="faint">Controls act on HERO1-owned backend jobs only. STOP requests cooperative cancellation, blocks new jobs while pending, waits up to 30 seconds, and never kills unrelated OS processes. SHUTDOWN stops the API; restart HERO1 to use it again.</p>
        <div className="mc-toolbar"><button className="btn btn-ghost" onClick={()=>void refreshProcessStatus()} disabled={actionBusy}>REFRESH PROCESS STATUS</button><button className="btn btn-danger" onClick={()=>void stopAll()} disabled={actionBusy}>STOP ALL PROCESSES</button><button className="btn btn-danger" onClick={()=>void requestShutdown()} disabled={actionBusy}>SHUTDOWN</button></div>
        {processMessage&&<p role="status">{processMessage}</p>}
        {processes&&<div className="mono" style={{fontSize:12,marginTop:10}}><p>Accepting new jobs: {String(processes.accepting_new_jobs)} · active workers: {processes.worker_count} · queued: {processes.queued_jobs} · running: {processes.running_jobs}</p><p>Completed {processes.counts?.COMPLETED??0} · failed {processes.counts?.FAILED??0} · cancelled {processes.counts?.CANCELLED??0} · blocked {processes.counts?.BLOCKED??0}</p><p>CPU/RAM: {JSON.stringify(processes.resources?.cpu_ram??{})} · GPU: {JSON.stringify(processes.resources?.gpu??{})}</p>{processes.active_jobs?.map((j:any)=><p key={j.id}>{j.category} · {j.status} · {j.label} · {j.completed_units}/{j.total_units} · {j.error??j.summary??''}</p>)}{processes.unsupported_workers?.map((j:any)=><p key={j.task_id} role="alert">Could not cancel {j.task_id}: {j.reason}</p>)}</div>}
      </section>

      <section className="card" style={{ marginTop: 18 }} aria-labelledby="data-management">
        <div className="card-title" id="data-management">DATA MANAGEMENT</div>
        <p className="faint">Permanent deletion is restricted to the configured data root. All operations show a preview, are blocked while any HERO1 job is active, and require an exact typed confirmation. Source code, launchers, dependencies, credentials, instrument registry/settings, and MARKET_METADATA are preserved.</p>
        <div className="mc-toolbar">{[['NODES','DELETE NODES DATA'],['DEEPTESTING','DELETE DEEPTESTING DATA'],['MONTE_CARLO','DELETE MONTE CARLO DATA'],['MARKET_DATA','DELETE MARKET DATA'],['LOGS','DELETE LOGS'],['ALL','DELETE ALL DATA']].map(([cat,label])=><button key={cat} className={cat==='ALL'?'btn btn-danger':'btn btn-ghost'} onClick={()=>void showDeletePreview(cat)} disabled={actionBusy}>{label}</button>)}</div>
        {deletePreview&&<div role="dialog" aria-modal="true" aria-label={`Confirm ${deletePreview.category} deletion`} className="card" style={{marginTop:12,border:'1px solid var(--accent,#d68b40)'}}><h3>PERMANENTLY DELETE {deletePreview.category==='ALL'?'ALL DATA':deletePreview.category.replace('_',' ')}</h3><p>Data root: <code>{deletePreview.data_root}</code>. The following scope is included:</p><ul>{deletePreview.included?.map((x:string)=><li key={x}>{x}</li>)}</ul><p>Active dashboard jobs: {deletePreview.active_jobs} · persisted RUNNING records: {JSON.stringify(deletePreview.active_run_records??{})}; deletion {deletePreview.can_delete?'is currently permitted':'is blocked until all jobs stop or are reconciled'}.</p><pre style={{whiteSpace:'pre-wrap'}}>{JSON.stringify({instrument_records:deletePreview.instrument_records,registry_records:deletePreview.registry_records,files:deletePreview.files},null,2)}</pre><label>Type exactly <b>{deletePreview.category==='ALL'?'DELETE ALL DATA':deletePreview.category==='LOGS'?'DELETE LOGS':`DELETE ${deletePreview.category.replace('_',' ')} DATA`}</b><input className="input" value={deletePhrase} onChange={e=>setDeletePhrase(e.target.value)} /></label>{deletePreview.category==='ALL'&&<p className="error-text">Strong confirmation required: this removes every listed user-data category, but preserves MARKET_METADATA, source code, startup configuration, credentials and runtime files.</p>}<button className="btn btn-danger" disabled={actionBusy||!deletePreview.can_delete||deletePhrase!==(deletePreview.category==='ALL'?'DELETE ALL DATA':deletePreview.category==='LOGS'?'DELETE LOGS':`DELETE ${deletePreview.category.replace('_',' ')} DATA`)} onClick={()=>void confirmDelete()}>{actionBusy?'Deleting…':'CONFIRM PERMANENT DELETE'}</button><button className="btn btn-ghost" onClick={()=>{setDeletePreview(null);setDeletePhrase('')}} disabled={actionBusy}>Cancel</button></div>}
      </section>
    </div>
  )
}
