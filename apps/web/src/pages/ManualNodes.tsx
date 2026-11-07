import { Fragment, useCallback, useEffect, useMemo, useState } from 'react'
import { useInstrument } from '../state/instrument'
import { api, ApiError } from '../api/client'
import { bullishEngulfingSpec } from '../lib/exampleSpec'
import { StrategyBuilder } from '../components/StrategyBuilder'
import { StarButton } from '../components/StarButton'
import { StarredScopeControl } from '../components/StarredScopeControl'
import { TimeframeMultiSelect } from '../components/TimeframeMultiSelect'
import { useStarredScope } from '../state/starScope'
import { useTimeframeScope } from '../state/timeframeScope'
import { emptyComparison } from '../builder/specTree'
import { SUPPORTED_TIMEFRAMES } from '../lib/timeframes'
import { nextAutoName } from '../lib/nodeNames'
import type { Node, NodeOrigin, ValidationIssue, ValidationReport, CompileResult, NodeVersionList, InstrumentMetadataView } from '../api/types'
import { useTableSort, type SortColumn } from '../lib/tableSort'
import { SortHeader } from '../components/SortHeader'

const NODE_SORT_COLUMNS: SortColumn<Node>[] = [
  { key: 'id', label: 'ID', type: 'natural', get: (n) => n.id },
  { key: 'name', label: 'Name', type: 'natural', get: (n) => n.name },
  { key: 'origin', label: 'Origin', type: 'text', get: (n) => n.origin },
  { key: 'timeframe', label: 'TF', type: 'text', get: (n) => n.timeframe },
  { key: 'version', label: 'v', type: 'number', get: (n) => n.version },
  { key: 'spec', label: 'Spec', type: 'text', get: (n) => n.spec_hash },
  { key: 'status', label: 'Status', type: 'text', get: (n) => n.status },
  { key: 'approval', label: 'Approval', type: 'text', get: (n) => n.approval_state },
]

const ORIGINS: NodeOrigin[] = ['CUSTOM', 'WWW', 'INTERACTIVE', 'FINE_TUNED']

const STATUS_COLOR: Record<string, string> = {
  VALID: 'var(--ok)', WARNING: 'var(--warn)', NEEDS_METADATA: 'var(--info)', INVALID: 'var(--err)',
}
const SEV_COLOR: Record<string, string> = {
  INVALID: 'var(--err)', WARNING: 'var(--warn)', NEEDS_METADATA: 'var(--info)',
}

function IssueRow({ issue }: { issue: ValidationIssue }) {
  return (
    <li style={{ border: '1px solid var(--border)', borderRadius: 8, padding: '8px 10px', listStyle: 'none' }}>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <span style={{ color: SEV_COLOR[issue.severity] ?? 'var(--muted)', fontWeight: 800, fontSize: 9, letterSpacing: 1.2 }}>{issue.severity}</span>
        <code className="mono" style={{ fontSize: 11 }}>{issue.code}</code>
        <code className="mono faint" style={{ fontSize: 10.5 }}>{issue.path}</code>
      </div>
      <div style={{ fontSize: 12, marginTop: 4 }}>{issue.message}</div>
      {issue.remediation && <div className="faint" style={{ fontSize: 11, marginTop: 3 }}>Fix: {issue.remediation}</div>}
    </li>
  )
}



export function ManualNodes() {
  const { instrument, instruments } = useInstrument()
  const { scope, setScope } = useStarredScope()
  const [nodes, setNodes] = useState<Node[]>([])
  const [counts, setCounts] = useState<{ total: number; starred_total: number; filtered_total: number; timeframe_counts: Record<string, number>; unassigned_timeframe_total: number } | null>(null)
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set())
  const [starBusyId, setStarBusyId] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [origin, setOrigin] = useState<NodeOrigin>('CUSTOM')
  const [description, setDescription] = useState('')
  const [creating, setCreating] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)

  // ---- Phase 2: strategy spec workspace state
  type SpecObject = Record<string, unknown>
  const [spec, setSpec] = useState<SpecObject | null>(null)   // last valid parsed spec (JSON editor may temporarily diverge)
  const [mode, setMode] = useState<'visual' | 'json'>('visual')
  const [nameDirty, setNameDirty] = useState(false)            // user typed a custom Node Name
  const [autoName, setAutoName] = useState('C1')
  const [renaming, setRenaming] = useState(false)
  const [specText, setSpecText] = useState('')
  const tfScope = useTimeframeScope()                    // shared table/scope filter
  const defaultTf = (instruments.find((i) => i.symbol.toUpperCase() === instrument.toUpperCase())?.default_timeframe ?? 'M15').toUpperCase()
  const [selectedTfs, setSelectedTfs] = useState<string[]>([defaultTf])
  const [tfInvalid, setTfInvalid] = useState(false)      // no timeframe selected → block save
  const [groupByTf, setGroupByTf] = useState(false)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [editDraft, setEditDraft] = useState('')
  const [editBusy, setEditBusy] = useState(false)
  const [editError, setEditError] = useState<string | null>(null)
  const [nodeName, setNodeName] = useState('')
  const [metadata, setMetadata] = useState<InstrumentMetadataView | null>(null)
  const [metadataBusy, setMetadataBusy] = useState(false)
  const [metadataError, setMetadataError] = useState<string | null>(null)
  const [metadataLoadedFor, setMetadataLoadedFor] = useState<string | null>(null)
  const [busy, setBusy] = useState<null | 'validate' | 'compile' | 'save' | 'version'>(null)
  const [specParseError, setSpecParseError] = useState<string | null>(null)
  const [validation, setValidation] = useState<ValidationReport | null>(null)
  const [compiled, setCompiled] = useState<CompileResult | null>(null)
  const [actionError, setActionError] = useState<{ message: string; code?: string; issues?: ValidationIssue[] } | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [lineage, setLineage] = useState<NodeVersionList | null>(null)
  // ISSUE-054: delete dialog (single or bulk) + Duplicate & Edit mode
  const [deleteIds, setDeleteIds] = useState<number[] | null>(null)
  const [deleteDeps, setDeleteDeps] = useState<{ descendants: number[]; descendant_count: number; dependent_total: number; dependent_records: Record<string, number>; blocked_reason: string | null } | null>(null)
  const [deleteIncludeDescendants, setDeleteIncludeDescendants] = useState(false)
  const [deleteBusy, setDeleteBusy] = useState(false)
  const [duplicateSource, setDuplicateSource] = useState<Node | null>(null)

  const selected = useMemo(() => nodes.find((n) => n.id === selectedId) ?? null, [nodes, selectedId])

  // Group-by-timeframe rendering: supported order, then drafts-without-TF last
  const nodeSort = useTableSort<Node>('manual-nodes', NODE_SORT_COLUMNS, { key: 'id', dir: 'desc' })
  const sortedNodes = useMemo(() => nodeSort.sorted(nodes), [nodeSort, nodes])

  const grouped = useMemo(() => {
    if (!groupByTf) return [{ key: 'all', label: null as string | null, items: sortedNodes }]
    const byTf = new Map<string, Node[]>()
    for (const n of sortedNodes) {
      const k = (n.timeframe ?? '').toUpperCase()
      const arr = byTf.get(k) ?? []
      arr.push(n)
      byTf.set(k, arr)
    }
    const out: { key: string; label: string | null; items: Node[] }[] = []
    for (const tf of SUPPORTED_TIMEFRAMES) {
      const items = byTf.get(tf)
      if (items && items.length) out.push({ key: tf, label: `${tf} · ${items.length} node${items.length === 1 ? '' : 's'}`, items })
    }
    const noTf = byTf.get('') ?? []
    if (noTf.length) out.push({ key: 'none', label: `NO TIMEFRAME (drafts) · ${noTf.length}`, items: noTf })
    return out
  }, [groupByTf, sortedNodes])

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await api.nodes.list(instrument, { starred: scope === 'starred' ? true : undefined,
                                                      timeframe: tfScope.timeframes.length ? tfScope.timeframes : undefined })
      setNodes(res.items)
      setCounts({ total: res.total, starred_total: res.starred_total, filtered_total: res.filtered_total,
                  timeframe_counts: res.timeframe_counts ?? {}, unassigned_timeframe_total: res.unassigned_timeframe_total ?? 0 })
      // selection can never exceed the visible (filtered) set — prune silently
      setSelectedIds((prev) => {
        const visible = new Set(res.items.map((n) => n.id))
        const next = new Set<number>()
        for (const id of prev) if (visible.has(id)) next.add(id)
        return next.size === prev.size ? prev : next
      })
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'failed to load nodes')
    } finally {
      setLoading(false)
    }
  }, [instrument, scope, tfScope.timeframes])

  useEffect(() => { load() }, [load])
  useEffect(() => {
    setValidation(null); setCompiled(null); setActionError(null); setNotice(null); setLineage(null)
    // instrument switch = a different database; never mix specs or naming across instruments
    setSpec(null); setSpecText(''); setSpecParseError(null); setNameDirty(false); setNodeName('C1')
    setSelectedTfs([(instruments.find((i) => i.symbol.toUpperCase() === instrument.toUpperCase())?.default_timeframe ?? 'M15').toUpperCase()])
    setTfInvalid(false)
    setEditingId(null); setEditError(null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [instrument])

  // auto-name from the registry (first available C-number, gap reuse — refresh-proof)
  const registryNames = useMemo(() => nodes.map((n) => n.name), [nodes])
  useEffect(() => {
    const next = nextAutoName(registryNames)
    setAutoName(next)
    if (!nameDirty && selectedTfs.length <= 1) setNodeName(next)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [registryNames, nameDirty, selectedTfs.length])

  const toggleStar = async (node: Node) => {
    if (starBusyId !== null) return
    const next = !node.starred
    setStarBusyId(node.id)
    // optimistic flip (research metadata; the PATCH is authoritative)
    setNodes((prev) => prev.map((n) => (n.id === node.id ? { ...n, starred: next } : n)))
    try {
      await api.nodes.setStarred(instrument, node.id, next)
      await load()   // re-sync rows + counts; unstarred rows leave a Starred-Only view
    } catch (err) {
      // rollback on failure
      setNodes((prev) => prev.map((n) => (n.id === node.id ? { ...n, starred: !next } : n)))
      setActionError({ message: err instanceof ApiError ? err.message : 'could not update star state' })
    } finally {
      setStarBusyId(null)
    }
  }

  const visibleIds = sortedNodes.map((n) => n.id)
  const allVisibleSelected = visibleIds.length > 0 && visibleIds.every((id) => selectedIds.has(id))
  const someVisibleSelected = visibleIds.some((id) => selectedIds.has(id))
  const toggleOne = (id: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id); else next.add(id)
      return next
    })
  }
  // Select All always means: the nodes CURRENTLY VISIBLE under the active filter
  const toggleAllVisible = () => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (allVisibleSelected) { for (const id of visibleIds) next.delete(id) }
      else { for (const id of visibleIds) next.add(id) }
      return next
    })
  }

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    const trimmed = name.trim()
    if (!trimmed) { setFormError('Name is required.'); return }
    setCreating(true)
    setFormError(null)
    try {
      await api.nodes.create(instrument, { name: trimmed, origin, description: description.trim() || undefined })
      setName('')
      setDescription('')
      await load()
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : 'creation failed')
    } finally {
      setCreating(false)
    }
  }

  // ------------------------------------------------- broker metadata (from MT5 via the backend)
  const loadMetadata = useCallback(async () => {
    try {
      setMetadata(await api.metadata.get(instrument))
      setMetadataError(null)
      setMetadataLoadedFor(instrument)
    } catch (err) {
      setMetadata(null)
      setMetadataLoadedFor(instrument)
      setMetadataError(err instanceof ApiError ? err.message : 'metadata lookup failed')
    }
  }, [instrument])

  const refreshMetadata = async () => {
    setMetadataBusy(true); setMetadataError(null)
    try {
      setMetadata(await api.metadata.refresh(instrument))
      setMetadataLoadedFor(instrument)
    } catch (err) {
      setMetadataError(err instanceof ApiError ? err.message : 'MT5 metadata refresh failed')
    } finally { setMetadataBusy(false) }
  }

  useEffect(() => { loadMetadata() }, [loadMetadata])

  const parseEditor = (): Record<string, unknown> | null => {
    try {
      const parsed = JSON.parse(specText)
      if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
        setSpecParseError('Spec must be a JSON object.')
        return null
      }
      setSpecParseError(null)
      return parsed as Record<string, unknown>
    } catch (err) {
      setSpecParseError(err instanceof Error ? err.message : 'invalid JSON')
      return null
    }
  }

  // Node name is applied to the spec at submit time: one visible source of truth.
  // (identity.name is fingerprinted only at save; renaming a SAVED node is a metadata-only PATCH.)
  const resolveNodeName = () => (nodeName.trim() || autoName).slice(0, 160)
  const resolveSpec = (): Record<string, unknown> | null => {
    const parsed = parseEditor()
    if (!parsed) return null
    const finalName = resolveNodeName()
    const identity = parsed.identity
    if (identity && typeof identity === 'object' && !Array.isArray(identity)) {
      // dry-run/compile preview always uses the FIRST selected timeframe as reference;
      // save fans out to one node per selected timeframe server-side.
      return { ...parsed, identity: { ...identity, name: finalName, timeframe: referenceTf } }
    }
    return parsed // backend will report the missing identity with exact issues
  }
  const syncFromVisual = (next: SpecObject) => {
    setSpec(next)
    setSpecText(JSON.stringify(next, null, 2))
    setSpecParseError(null)
    setValidation(null); setCompiled(null); setActionError(null); setNotice(null)
  }
  const syncFromJson = (text: string) => {
    setSpecText(text)
    try {
      const parsed = JSON.parse(text)
      if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
        setSpecParseError('Spec must be a JSON object.')
        return
      }
      setSpec(parsed as SpecObject)
      setSpecParseError(null)
    } catch (err) {
      setSpecParseError(err instanceof Error ? err.message : 'invalid JSON')
    }
  }
  const referenceTf = selectedTfs[0] ?? 'M15'
  const startBlankSpec = () => {
    const blank = {
      schema_version: '1.0.0',
      identity: { name: resolveNodeName(), description: '', instrument, timeframe: referenceTf, direction: 'BOTH', origin: 'CUSTOM', tags: [], metadata: {} },
      execution: { entry_timing: 'next_bar_open', candle: 'completed', permit_forming_candle: false, order_type: 'market', position_handling: 'single' },
      entry: { type: 'and', children: [emptyComparison()] },
      exits: {},
      risk: { sizing: { mode: 'fixed_risk', risk_pct: 1.0 } },
    }
    setSpec(blank)
    setSpecText(JSON.stringify(blank, null, 2))
    setSpecParseError(null); setValidation(null); setCompiled(null); setActionError(null); setNotice(null)
  }

  const loadExample = () => {
    const example = bullishEngulfingSpec(instrument, referenceTf) as SpecObject
    setSpec(example)
    setSpecText(JSON.stringify(example, null, 2))
    setValidation(null); setCompiled(null); setActionError(null); setNotice(null); setSpecParseError(null)
  }

  const formatEditor = () => {
    const parsed = parseEditor()
    if (parsed) setSpecText(JSON.stringify(parsed, null, 2))
  }

  const asIssues = (err: ApiError): ValidationIssue[] | undefined =>
    Array.isArray(err.issues) ? (err.issues as ValidationIssue[]) : undefined

  const doValidate = async () => {
    const spec = resolveSpec(); if (!spec) return
    setBusy('validate'); setActionError(null); setNotice(null)
    try {
      setValidation(await api.nodes.validate(instrument, spec))
      setCompiled(null)
    } catch (err) {
      if (err instanceof ApiError) setActionError({ message: err.message, code: err.code, issues: asIssues(err) })
      else setActionError({ message: 'validation failed' })
    } finally { setBusy(null) }
  }

  const doCompile = async () => {
    const spec = resolveSpec(); if (!spec) return
    setBusy('compile'); setActionError(null); setNotice(null)
    try {
      const result = await api.nodes.compile(instrument, spec)
      setCompiled(result)
    } catch (err) {
      setCompiled(null)
      if (err instanceof ApiError) setActionError({ message: err.message, code: err.code, issues: asIssues(err) })
      else setActionError({ message: 'compilation failed' })
    } finally { setBusy(null) }
  }

  const saveNewNode = async () => {
    const spec = resolveSpec(); if (!spec) return
    if (selectedTfs.length === 0) { setTfInvalid(true); setActionError({ message: 'Select at least one timeframe before saving.' }); return }
    setBusy('save'); setActionError(null); setNotice(null); setTfInvalid(false)
    const single = selectedTfs.length === 1
    try {
      const result = await api.nodes.generate(instrument, {
        spec,
        timeframes: selectedTfs,
        // explicit name honoured only for single-timeframe saves; multi-timeframe
        // generation allocates sequential first-available C-names server-side
        name: single ? resolveNodeName() : undefined,
        description: selectedTfs.length > 1
          ? `Generated from one strategy across ${selectedTfs.length} timeframes (server-validated + compiled).`
          : 'Saved from the spec workspace (server-validated + compiled).',
        })
      const created = result.created
      setNotice(created.length === 1
        ? `Saved as node #${created[0].id} ${created[0].name} (${created[0].timeframe}) · fingerprint ${created[0].spec_hash?.slice(0, 12)}…`
        : `Saved ${created.length} independent nodes: ${created.map((n) => `${n.name} (${n.timeframe})`).join(', ')} — each with its own spec fingerprint`)
      setSelectedId(created[created.length - 1]?.id ?? null)
      setNameDirty(false) // registry refresh recomputes the next auto C-number
      await load()
    } catch (err) {
      if (err instanceof ApiError) setActionError({ message: err.message, code: err.code, issues: asIssues(err) })
      else setActionError({ message: 'save failed' })
    } finally { setBusy(null) }
  }

  const saveNewVersion = async () => {
    if (!selected) return
    const spec = resolveSpec(); if (!spec) return
    setBusy('version'); setActionError(null); setNotice(null)
    try {
      const created = await api.nodes.versions(instrument, selected.id, {
        spec,
        name: resolveNodeName(),
      })
      setNotice(`Created v${created.version} of the lineage as node #${created.id} (parent #${selected.id}) · fingerprint ${created.spec_hash?.slice(0, 12)}…`)
      setSelectedId(created.id)
      setLineage(null)
      await load()
    } catch (err) {
      if (err instanceof ApiError) setActionError({ message: err.message, code: err.code, issues: asIssues(err) })
      else setActionError({ message: 'version creation failed' })
    } finally { setBusy(null) }
  }

  const showLineage = async () => {
    if (!selected) return
    try {
      setLineage(await api.nodes.lineage(instrument, selected.id))
    } catch (err) {
      setActionError({ message: err instanceof ApiError ? err.message : 'lineage failed' })
    }
  }

  const loadIntoEditor = () => {
    if (!selected?.spec_json) return
    setSpecText(selected.spec_json)
    try {
      const parsed = JSON.parse(selected.spec_json)
      setSpec(parsed && typeof parsed === 'object' ? (parsed as SpecObject) : null)
      setSpecParseError(null)
    } catch (err) {
      setSpec(null)
      setSpecParseError(err instanceof Error ? err.message : 'invalid JSON')
    }
    setNodeName(selected.name)
    setNameDirty(true)
    setValidation(null); setCompiled(null); setActionError(null); setNotice(null)
  }

  // Inline rename (click the displayed name): Enter saves · Escape cancels. Display only.
  const startInlineEdit = (n: Node) => { setEditingId(n.id); setEditDraft(n.name); setEditError(null) }
  const cancelInlineEdit = () => { setEditingId(null); setEditError(null) }
  const toggleTfChip = (tf: string) => {
    const cur = new Set(tfScope.timeframes)
    if (cur.has(tf)) cur.delete(tf); else cur.add(tf)
    tfScope.setTimeframes([...cur])
  }
  const commitInlineEdit = async (n: Node) => {
    if (editBusy) return
    const finalName = editDraft.trim()
    if (!finalName) { setEditError('Name cannot be empty.'); return }
    if (finalName.length > 160) { setEditError('Name must be ≤ 160 characters.'); return }
    const dup = nodes.find((m) => m.id !== n.id && m.name.trim().toLowerCase() === finalName.toLowerCase())
    if (dup) { setEditError(`“${finalName}” is already used by node #${dup.id} — names are unique per instrument.`); return }
    setEditBusy(true)
    try {
      await api.nodes.rename(instrument, n.id, finalName)
      setNotice(`Renamed node #${n.id} → “${finalName}” (display only — spec hash unchanged)`)
      setEditingId(null); setEditError(null)
      await load()
    } catch (err) {
      setEditError(err instanceof ApiError ? err.message : 'Rename failed')
    } finally { setEditBusy(false) }
  }
  const onInlineEditKeyDown = (n: Node) => (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') { e.preventDefault(); void commitInlineEdit(n) }
    else if (e.key === 'Escape') { e.preventDefault(); cancelInlineEdit() }
  }

  // Metadata-only rename of the SAVED node — never touches the spec or its hash.
  const renameSelected = async () => {
    if (!selected) return
    const finalName = resolveNodeName()
    setRenaming(true); setActionError(null); setNotice(null)
    try {
      await api.nodes.rename(instrument, selected.id, finalName)
      setNotice(`Renamed node #${selected.id} to "${finalName}" (display only — spec hash unchanged)`)
      setNameDirty(false)
      await load()
    } catch (err) {
      setActionError({ message: err instanceof ApiError ? err.message : 'rename failed' })
    } finally { setRenaming(false) }
  }

  // ---------------------------------------------------------- ISSUE-054
  const openDelete = async (ids: number[]) => {
    setDeleteIds(ids)
    setDeleteIncludeDescendants(false)
    setDeleteDeps(null)
    if (ids.length === 1) {
      try {
        const deps = await api.nodes.dependencies(instrument, ids[0])
        setDeleteDeps(deps)
      } catch { /* the delete call itself reports guards authoritatively */ }
    }
  }

  const confirmDelete = async () => {
    if (!deleteIds || deleteIds.length === 0 || deleteBusy) return
    setDeleteBusy(true); setActionError(null); setNotice(null)
    try {
      const result = deleteIds.length === 1 && !deleteIncludeDescendants && !deleteDeps?.descendants.length
        ? await api.nodes.remove(instrument, deleteIds[0], false)
        : await api.nodes.removeMany(instrument, deleteIds, deleteIncludeDescendants)
      const deletedSet = new Set(result.deleted)
      setSelectedIds((prev) => new Set([...prev].filter((id) => !deletedSet.has(id))))
      if (selectedId !== null && deletedSet.has(selectedId)) { setSelectedId(null); setLineage(null) }
      setNotice(result.deleted.length > 0
        ? `Deleted node(s): ${result.deleted.map((id) => `#${id}`).join(', ')}`
        : 'Nothing was deleted.')
      if (result.failed.length > 0) {
        setActionError({ message: result.failed.map((f) => `#${f.node_id}: ${f.message}`).join(' · ') })
      }
      setDeleteIds(null)
      await load()
    } catch (err) {
      setActionError({ message: err instanceof ApiError ? err.message : 'deletion failed' })
      setDeleteIds(null)
    } finally { setDeleteBusy(false) }
  }

  const startDuplicate = (node: Node) => {
    if (!node.spec_json) return
    setDuplicateSource(node)
    try {
      const parsed = JSON.parse(node.spec_json)
      setSpec(parsed && typeof parsed === 'object' ? (parsed as SpecObject) : null)
      setSpecText(node.spec_json)
      setSpecParseError(null)
    } catch (err) {
      setSpec(null)
      setSpecParseError(err instanceof Error ? err.message : 'invalid JSON')
    }
    if (node.timeframe) setSelectedTfs([node.timeframe])
    setNodeName(''); setNameDirty(false)   // auto C-name suggestion; editable
    setValidation(null); setCompiled(null); setActionError(null)
    setNotice(`Duplicating node #${node.id} (${node.name}) — edit the draft, then Save Variant. The original is never modified.`)
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  const cancelDuplicate = () => {
    setDuplicateSource(null)
    setNotice('Duplicate draft discarded — no node was created.')
  }

  const saveVariant = async () => {
    if (!duplicateSource) return
    const spec = resolveSpec(); if (!spec) return
    setBusy('save'); setActionError(null); setNotice(null)
    try {
      const created = await api.nodes.duplicate(instrument, duplicateSource.id, {
        spec, name: resolveNodeName(),
      })
      setNotice(`Saved variant #${created.id} ${created.name} (${created.timeframe ?? '—'}) based on #${duplicateSource.id} · fingerprint ${created.spec_hash?.slice(0, 12)}… — original #${duplicateSource.id} unchanged`)
      setDuplicateSource(null)
      setSelectedId(created.id)
      setNameDirty(false)
      await load()
    } catch (err) {
      if (err instanceof ApiError) setActionError({ message: err.message, code: err.code, issues: asIssues(err) })
      else setActionError({ message: 'variant save failed' })
    } finally { setBusy(null) }
  }

  return (
    <div>
      <div className="page-head">
        <div className="page-eyebrow">Builder · {instrument}</div>
        <h1 className="page-title">Manual Nodes</h1>
        <p className="page-sub">Foundational node registry for {instrument}. Phase 2 adds the strategy specification workspace: typed specs are validated and deterministically compiled server-side — a strategy is structured data, never executable code.</p>
      </div>

      <div className="banner" role="note">
        <span aria-hidden="true">ⓘ</span>
        <span><strong>Phase 2 scope.</strong> Specs saved here are validated then compiled to a declarative IR and SHA-256 fingerprinted by the server — client-supplied hashes are never authoritative. Nothing is backtested, optimized or executed from this screen. Broker metadata (digits/point/tick/contract) comes from the actual MT5 terminal per broker/server and is never assumed — without it, pip/tick/point distances report NEEDS_METADATA. Strategy semantics are immutable — changing a spec creates a new version linked to its parent.</span>
      </div>

      <div className="panel-grid">
        {/* ---------------------------------------------- spec workspace */}
        <div className="card" style={{ gridColumn: '1 / -1' }}>
          <div className="card-title">
            Strategy spec workspace · {instrument}
            <span className="faint" style={{ fontWeight: 400, fontSize: 11, marginLeft: 10 }}>validation &amp; compilation happen server-side</span>
          </div>

          <div className="builder-toolbar">
            <div className="segmented" role="tablist" aria-label="Editor mode">
              <button type="button" role="tab" aria-selected={mode === 'visual'} className={mode === 'visual' ? 'active' : ''} onClick={() => setMode('visual')}>Visual Builder</button>
              <button type="button" role="tab" aria-selected={mode === 'json'} className={mode === 'json' ? 'active' : ''} onClick={() => setMode('json')}>JSON Editor</button>
            </div>
            <TimeframeMultiSelect id="spec-tf-multi"
                                  value={selectedTfs}
                                  invalid={tfInvalid}
                                  onChange={(next) => { setSelectedTfs(next); setTfInvalid(false) }}
                                  label="TIMEFRAMES — ONE NODE PER TF"
                                  hint={selectedTfs.length > 1
                                    ? `Saving will generate ${selectedTfs.length} independent nodes (${selectedTfs.join(', ')}) — sequential names, one fingerprint each`
                                    : selectedTfs.length === 1
                                      ? 'Saving generates 1 node with this timeframe'
                                      : undefined} />
            <button className="btn btn-ghost btn-sm" type="button" onClick={startBlankSpec}>New blank strategy</button>
            <button className="btn btn-ghost btn-sm" type="button" onClick={loadExample}>Load bullish engulfing example</button>
            {mode === 'json' && <button className="btn btn-ghost btn-sm" type="button" onClick={formatEditor} disabled={!specText.trim()}>Format JSON</button>}
            <span className="faint" style={{ fontSize: 10.5, marginLeft: 'auto' }}>Both modes edit ONE spec — the backend remains authoritative</span>
          </div>

          {mode === 'visual' ? (
            spec === null ? (
              <div className="builder-empty">
                <p>No strategy loaded. Start a blank strategy and build it visually, or load the example.</p>
                <div style={{ display: 'flex', gap: 10 }}>
                  <button className="btn btn-primary btn-sm" type="button" onClick={startBlankSpec}>New blank strategy</button>
                  <button className="btn btn-ghost btn-sm" type="button" onClick={loadExample}>Load bullish engulfing example</button>
                </div>
                <p className="faint" style={{ fontSize: 11 }}>The spec starts with identity (direction BOTH), execution defaults and an empty AND entry group. Exits are never invented — add them in section 2. Sizing/risk parameters are intentionally not part of this workflow; they belong to DeepTesting / Live Testing.</p>
              </div>
            ) : (
              <StrategyBuilder spec={spec} onChange={syncFromVisual} jsonError={specParseError}
                               onRevertJson={() => { if (spec) { setSpecText(JSON.stringify(spec, null, 2)); setSpecParseError(null) } }}
                               instrument={instrument} />
            )
          ) : (
            <>
              <div className="field">
                <label htmlFor="spec-json">Specification JSON</label>
                <textarea id="spec-json" className="input mono" spellCheck={false}
                          style={{ minHeight: 220, fontSize: 11.5, lineHeight: 1.5, whiteSpace: 'pre' }}
                          value={specText} onChange={(e) => syncFromJson(e.target.value)}
                          placeholder='{"schema_version": "1.0.0", "identity": { … }, "entry": …, "exits": …, "risk": …, "execution": …}' />
              </div>
              {specParseError && <div className="field-error" role="alert" style={{ marginTop: 6 }}>JSON: {specParseError} — the visual builder keeps the last valid spec; nothing is deleted.</div>}
              {!specParseError && !specText.trim() && <div className="faint" style={{ fontSize: 11, marginTop: 6 }}>Empty editor — start with “New blank strategy”.</div>}
            </>
          )}

          <div className="field" style={{ marginTop: 10 }}>
            <label id="broker-meta-label">Broker metadata <span className="faint">(per {instrument} — read from the actual MT5 terminal per broker/server; NEVER assumed; validation uses the stored snapshot server-side)</span></label>
            <div className="broker-meta-panel" aria-labelledby="broker-meta-label">
              {metadataLoadedFor !== instrument && !metadata && (
                <p className="faint" style={{ margin: 0 }}>loading stored metadata…</p>
              )}
              {metadata?.available && metadata.snapshot ? (
                <>
                  <div className="meta-row">
                    <span className="meta-chip ok">STORED</span>
                    <span className="mono">{metadata.snapshot.broker_server}</span>
                    <span className="faint">source {metadata.snapshot.source} · retrieved {metadata.snapshot.retrieved_at ? new Date(metadata.snapshot.retrieved_at).toLocaleString() : '—'}</span>
                  </div>
                  <div className="meta-row mono meta-grid">
                    <span>digits <strong>{metadata.snapshot.digits ?? '—'}</strong></span>
                    <span>point <strong>{metadata.snapshot.derived?.point_size ?? '—'}</strong></span>
                    <span>tick size <strong>{metadata.snapshot.derived?.tick_size ?? '—'}</strong></span>
                    <span>pip size <strong>{metadata.snapshot.derived?.pip_size ?? '—'}</strong></span>
                    <span>contract <strong>{metadata.snapshot.derived?.contract_size ?? '—'}</strong></span>
                  </div>
                  <div className="meta-row faint" style={{ fontSize: 11 }}>
                    vol {metadata.snapshot.volume_min ?? '—'}–{metadata.snapshot.volume_max ?? '—'} step {metadata.snapshot.volume_step ?? '—'}
                    {metadata.snapshot.stops_level != null && <> · stops level {metadata.snapshot.stops_level}</>}
                    {metadata.snapshot.account_login != null && <> · from account #{metadata.snapshot.account_login}</>}
                    {metadata.snapshot.validation && !metadata.snapshot.validation.ok && (
                      <span className="field-error" style={{ marginLeft: 8 }}>missing: {metadata.snapshot.validation.missing.join(', ')}</span>
                    )}
                  </div>
                </>
              ) : metadata && !metadata.available ? (
                <div className="meta-missing">
                  <p style={{ margin: 0 }}><span className="meta-chip warn">NOT STORED</span> No broker metadata for {instrument} yet — pip/tick/point distances will report <strong>NEEDS_METADATA</strong> (the server will not guess).</p>
                  <p className="faint" style={{ margin: '6px 0 0', fontSize: 11 }}>On Windows with MT5 running, press <strong>Refresh from MT5</strong>. Without MT5, POST the broker&apos;s symbol_info to <code>/api/v1/instruments/{instrument}/metadata/import</code> (see docs/WINDOWS_SETUP.md). Absolute/ATR distances need no broker metadata.</p>
                </div>
              ) : null}
              <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginTop: 8, flexWrap: 'wrap' }}>
                <button className="btn btn-ghost btn-sm" type="button" onClick={refreshMetadata} disabled={metadataBusy}>
                  {metadataBusy ? 'Refreshing from MT5…' : 'Refresh from MT5'}
                </button>
                <span className="faint" style={{ fontSize: 10.5 }}>metadata is per broker/server — it never overwrites another broker&apos;s {instrument}</span>
              </div>
              {metadataError && <div className="field-error" role="alert" style={{ marginTop: 6 }}>{metadataError}</div>}
            </div>
            <div className="faint" style={{ fontSize: 10.5, marginTop: 6 }}>
              Validate/Compile preview uses the first selected timeframe as the <strong>reference</strong> (<span className="mono">{referenceTf}</span>) — identical conditions are replicated to every selected timeframe on save; timeframe is part of each node's identity and fingerprint.
            </div>
          </div>

          <div className="node-name-row">
            <label htmlFor="spec-node-name">Node name <span className="faint">{selectedTfs.length > 1 ? `${selectedTfs.length} nodes — sequential names allocated at save` : `auto → ${autoName}`}</span></label>
            <input id="spec-node-name" className="input mono" maxLength={160}
                   value={selectedTfs.length > 1 ? '' : nodeName}
                   disabled={selectedTfs.length > 1}
                   onChange={(e) => { setNodeName(e.target.value); setNameDirty(true) }}
                   placeholder={selectedTfs.length > 1 ? 'C… C… C… (allocated sequentially)' : autoName}
                   aria-label="Node name"
                   title={selectedTfs.length > 1 ? 'Multiple timeframes selected — each node receives the next sequential available C-name' : undefined} />
            {selectedTfs.length > 1 ? (
              <span className="faint" style={{ fontSize: 11 }}>names allocated sequentially from the registry (edit any of them later via inline rename)</span>
            ) : nameDirty ? (
              <>
                <button className="btn btn-ghost btn-sm" type="button" onClick={() => { setNameDirty(false); setNodeName(autoName) }}>Reset to auto ({autoName})</button>
                <span className="faint" style={{ fontSize: 11 }}>custom name will be preserved</span>
              </>
            ) : (
              <span className="faint" style={{ fontSize: 11 }}>left unchanged → saved as {autoName}</span>
            )}
            {selected?.spec_json && (
              <button className="btn btn-ghost btn-sm" type="button" disabled={renaming} onClick={renameSelected}
                      title="Metadata-only rename of the saved node — never changes the spec hash or forks a version">
                {renaming ? 'Renaming…' : `Rename #${selected.id} only (hash unchanged)`}
              </button>
            )}
          </div>
          <p className="faint" style={{ fontSize: 10.5, marginTop: 4 }}>Node naming is registry metadata, not strategy semantics: it is applied at save time, and renaming an existing node uses metadata-only PATCH — the spec fingerprint and version lineage are never affected by a display rename. Auto names come from the registry for <strong>{instrument}</strong> as the first available sequential C-number with gap reuse (C1, C3 exist → next is C2). Saving one strategy across N timeframes allocates N different sequential names, server-side and race-safe (unique index on instrument+name).</p>

          {duplicateSource && (
            <div className="banner" role="status" style={{ marginTop: 12, marginBottom: 0 }}>
              <span aria-hidden="true">⧉</span>
              <span><strong>Duplicate &amp; Edit:</strong> new variant based on <strong>#{duplicateSource.id} {duplicateSource.name}</strong> ({duplicateSource.timeframe ?? 'draft'}). The original node is never modified. Saving creates a NEW node with its own ID and fingerprint; the source is recorded as its parent. An unchanged strategy is rejected by the duplicate-prevention rule.</span>
            </div>
          )}

          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginTop: 12 }}>
            <button className="btn btn-primary" type="button" onClick={doValidate} disabled={busy !== null || !specText.trim()}>{busy === 'validate' ? 'Validating…' : 'Validate'}</button>
            <button className="btn" type="button" onClick={doCompile} disabled={busy !== null || !specText.trim()}>{busy === 'compile' ? 'Compiling…' : 'Compile'}</button>
            {duplicateSource ? (
              <>
                <button className="btn btn-primary" type="button" onClick={saveVariant} disabled={busy !== null || !specText.trim()}>{busy === 'save' ? 'Saving variant…' : `Save Variant (new node based on #${duplicateSource.id})`}</button>
                <button className="btn btn-ghost" type="button" onClick={cancelDuplicate} disabled={busy !== null}>Cancel</button>
              </>
            ) : (
              <>
                <button className="btn" type="button" onClick={saveNewNode} disabled={busy !== null || !specText.trim()}>{busy === 'save' ? 'Saving…' : selectedTfs.length > 1 ? `Generate ${selectedTfs.length} nodes` : 'Save as new spec node'}</button>
                <button className="btn btn-ghost" type="button" onClick={saveNewVersion} disabled={busy !== null || !specText.trim() || !selected?.spec_json}
                        title={selected?.spec_json ? `New immutable version in the lineage of node #${selected.id}` : 'Select a spec-backed node in the registry first'}>
                  {busy === 'version' ? 'Creating version…' : selected?.spec_json ? `Save as new version of #${selected.id}` : 'Save as new version (select a spec node)'}
                </button>
              </>
            )}
          </div>

          {notice && <div className="banner" style={{ marginTop: 12, marginBottom: 0, borderColor: 'rgba(61,220,151,.3)', background: 'rgba(61,220,151,.06)', color: '#b6f5d8' }} role="status"><span aria-hidden="true">✓</span><span className="mono" style={{ fontSize: 11 }}>{notice}</span></div>}

          {actionError && (
            <div style={{ marginTop: 12 }} role="alert">
              <div className="field-error">{actionError.code ? `[${actionError.code}] ` : ''}{actionError.message}</div>
              {actionError.issues && actionError.issues.length > 0 && (
                <ul style={{ display: 'grid', gap: 6, margin: '8px 0 0', padding: 0 }}>
                  {actionError.issues.map((issue, i) => <IssueRow key={i} issue={issue} />)}
                </ul>
              )}
            </div>
          )}

          {validation && (
            <div style={{ marginTop: 12 }}>
              <div style={{ display: 'flex', gap: 14, alignItems: 'center', flexWrap: 'wrap' }}>
                <span style={{ color: STATUS_COLOR[validation.status], fontWeight: 800, letterSpacing: 1.4, fontSize: 13 }}>{validation.status}</span>
                <span className="faint" style={{ fontSize: 11 }}>warmup {validation.required_warmup_candles ?? '—'} candles · schema {validation.schema_version ?? '—'}</span>
                <span className="faint" style={{ fontSize: 11 }}>invalid {validation.summary?.invalid ?? 0} · warnings {validation.summary?.warning ?? 0} · needs-metadata {validation.summary?.needs_metadata ?? 0}</span>
              </div>
              {validation.issues.length > 0 && (
                <ul style={{ display: 'grid', gap: 6, margin: '8px 0 0', padding: 0 }}>
                  {validation.issues.map((issue, i) => <IssueRow key={i} issue={issue} />)}
                </ul>
              )}
              {validation.status === 'VALID' && <div className="faint" style={{ fontSize: 11, marginTop: 6 }}>Spec is valid. Compile to inspect the IR and fingerprint, then save.</div>}
              {validation.status === 'NEEDS_METADATA' && <div className="faint" style={{ fontSize: 11, marginTop: 6 }}>A required broker parameter is missing — the server will not guess pip/tick sizes. Supply the metadata above or switch the spec to absolute/ATR distances.</div>}
            </div>
          )}

          {compiled && (
            <div style={{ marginTop: 12, borderTop: '1px solid var(--border)', paddingTop: 12 }}>
              <div className="card-title" style={{ marginBottom: 8 }}>Compiled IR <span className="faint" style={{ fontWeight: 400, fontSize: 10.5 }}>(declarative only — not executable)</span></div>
              <div style={{ display: 'grid', gap: 4, fontSize: 11 }} className="mono">
                <div><span className="faint">fingerprint</span><br /><span style={{ wordBreak: 'break-all' }}>{compiled.fingerprint}</span></div>
                <div className="faint">schema {compiled.schema_version} · compiler {compiled.compiler_version} · ir {compiled.ir_version} · warmup {compiled.required_warmup_candles}</div>
                {compiled.warnings.length > 0 && <div style={{ color: 'var(--warn)' }}>warnings: {compiled.warnings.join('; ')}</div>}
              </div>
              <pre className="input mono" style={{ maxHeight: 260, overflow: 'auto', fontSize: 10.5, marginTop: 8 }}>{JSON.stringify(compiled.ir, null, 2)}</pre>
            </div>
          )}
        </div>

        {/* ---------------------------------------------- create draft */}
        <form className="card" onSubmit={submit}>
          <div className="card-title">Create node draft</div>
          <div className="field">
            <label htmlFor="node-name">Name</label>
            <input id="node-name" className="input" value={name} maxLength={160}
                   onChange={(e) => setName(e.target.value)} placeholder="e.g. M15 EMA cross · London session" />
          </div>
          <div className="field" style={{ marginTop: 12 }}>
            <label htmlFor="node-origin">Origin</label>
            <select id="node-origin" className="input" value={origin} onChange={(e) => setOrigin(e.target.value as NodeOrigin)}>
              {ORIGINS.map((o) => <option key={o} value={o}>{o}</option>)}
            </select>
          </div>
          <div className="field" style={{ marginTop: 12 }}>
            <label htmlFor="node-desc">Description <span className="faint">(optional)</span></label>
            <textarea id="node-desc" className="input" value={description} maxLength={4000}
                      onChange={(e) => setDescription(e.target.value)} placeholder="What is this strategy idea?" />
          </div>
          {formError && <div className="field-error" role="alert" style={{ marginTop: 10 }}>{formError}</div>}
          <button className="btn btn-primary" type="submit" disabled={creating} style={{ marginTop: 14 }}>
            {creating ? 'Creating…' : 'Create node'}
          </button>
        </form>

        {/* ---------------------------------------------- registry */}
        <div className="card">
          <div className="card-title">
            Registry · {instrument}
            <button className="btn btn-ghost btn-sm" onClick={load} style={{ marginLeft: 'auto' }} aria-label="Refresh">↻ Refresh</button>
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 8 }}>
            <span className="tf-multi-label" style={{ margin: 0 }}>TIMEFRAME</span>
            <div className="tf-chips" role="group" aria-label="Filter nodes by timeframe">
              <button type="button" className={`tf-chip${tfScope.timeframes.length === 0 ? ' on' : ''}`}
                      aria-pressed={tfScope.timeframes.length === 0} onClick={() => tfScope.setTimeframes([])}>All</button>
              {SUPPORTED_TIMEFRAMES.map((tf) => {
                const on = tfScope.timeframes.includes(tf)
                const n = counts?.timeframe_counts?.[tf] ?? 0
                return (
                  <button key={tf} type="button" className={`tf-chip${on ? ' on' : ''}`}
                          aria-pressed={on} onClick={() => toggleTfChip(tf)}
                          title={`${tf} — ${n} node${n === 1 ? '' : 's'} in this instrument (star-scope aware)`}>
                    {tf}<span className="tf-chip-count">{n}</span>
                  </button>
                )
              })}
              {(counts?.unassigned_timeframe_total ?? 0) > 0 && (
                <span className="faint" style={{ fontSize: 10 }}>+{counts!.unassigned_timeframe_total} draft{counts!.unassigned_timeframe_total === 1 ? '' : 's'} without timeframe</span>
              )}
            </div>
            <button type="button" className={`tf-chip${groupByTf ? ' on' : ''}`} style={{ marginLeft: 'auto' }}
                    aria-pressed={groupByTf} onClick={() => setGroupByTf((v) => !v)}>
              Group by timeframe
            </button>
          </div>
          {tfScope.timeframes.length > 0 && (
            <div className="faint" style={{ fontSize: 10, marginBottom: 8 }}>
              Filtering: {tfScope.timeframes.join(', ')} — Select All applies only to these visible nodes ({counts?.filtered_total ?? '…'} of {counts?.total ?? '…'}).
            </div>
          )}
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end', gap: 12, flexWrap: 'wrap', marginBottom: 10 }}>
            <StarredScopeControl mini total={counts?.total ?? null} starredTotal={counts?.starred_total ?? null} />
            {selectedIds.size > 0 && (
              <span style={{ display: 'inline-flex', gap: 8, alignItems: 'center' }}>
                <span className="selection-chip" role="status">
                  {selectedIds.size} selected{scope === 'starred' ? ' · starred scope' : ''}
                  <button type="button" onClick={() => setSelectedIds(new Set())}>clear</button>
                </span>
                <button type="button" className="btn-danger" onClick={() => void openDelete([...selectedIds])}>
                  Delete Selected ({selectedIds.size})
                </button>
              </span>
            )}
          </div>
          {loading ? (
            <div className="state"><div className="spinner" /><div className="state-title">Loading nodes…</div></div>
          ) : error ? (
            <div className="state error"><div className="state-title">Backend unavailable</div>{error}<div style={{ marginTop: 12 }}><button className="btn btn-sm" onClick={load}>Retry</button></div></div>
          ) : nodes.length === 0 && scope === 'starred' ? (
            <div className="state">
              <div className="state-title">No starred nodes yet for {instrument}</div>
              Star nodes with the star column to pin them here — starring is research metadata and never changes a strategy.
              <div style={{ marginTop: 10 }}><button className="btn btn-sm" onClick={() => setScope('all')}>Show all nodes</button></div>
            </div>
          ) : nodes.length === 0 ? (
            <div className="state"><div className="state-title">No nodes yet for {instrument}</div>Create your first draft or save a validated spec — both stay inside this instrument’s database.</div>
          ) : (
            <div style={{ overflowX: 'auto' }}>
              <table className="node-table">
                <thead><tr>
                  <th className="select-col"><input type="checkbox" aria-label="Select all visible nodes"
                        checked={allVisibleSelected} data-indeterminate={someVisibleSelected && !allVisibleSelected}
                        onChange={toggleAllVisible} ref={(el) => { if (el) el.indeterminate = someVisibleSelected && !allVisibleSelected }} /></th>
                  <th className="star-col" aria-label="Starred">★</th>
                  <SortHeader label="ID" columnKey="id" state={nodeSort.state} onToggle={nodeSort.toggle} />
                  <SortHeader label="Name" columnKey="name" state={nodeSort.state} onToggle={nodeSort.toggle} />
                  <SortHeader label="Origin" columnKey="origin" state={nodeSort.state} onToggle={nodeSort.toggle} />
                  <SortHeader label="TF" columnKey="timeframe" state={nodeSort.state} onToggle={nodeSort.toggle} />
                  <SortHeader label="v" columnKey="version" state={nodeSort.state} onToggle={nodeSort.toggle} />
                  <SortHeader label="Spec" columnKey="spec" state={nodeSort.state} onToggle={nodeSort.toggle} />
                  <SortHeader label="Status" columnKey="status" state={nodeSort.state} onToggle={nodeSort.toggle} />
                  <SortHeader label="Approval" columnKey="approval" state={nodeSort.state} onToggle={nodeSort.toggle} />
                  <th></th>
                </tr></thead>
                <tbody>
                  {grouped.map((group) => (
                    <Fragment key={group.key}>
                      {group.label && <tr className="group-row"><td colSpan={11}>{group.label}</td></tr>}
                      {group.items.map((n) => (
                    <tr key={n.id} onClick={() => { setSelectedId(n.id); setLineage(null) }}
                        style={{ cursor: 'pointer', background: n.id === selectedId ? 'rgba(92,200,255,.06)' : undefined }}>
                      <td className="select-col" onClick={(e) => e.stopPropagation()}>
                        <input type="checkbox" aria-label={`Select node ${n.id}`}
                               checked={selectedIds.has(n.id)} onChange={() => toggleOne(n.id)} />
                      </td>
                      <td className="star-col" onClick={(e) => e.stopPropagation()}>
                        <StarButton starred={!!n.starred} busy={starBusyId === n.id}
                                    label={n.starred ? `Unstar node ${n.id}` : `Star node ${n.id}`}
                                    onToggle={() => toggleStar(n)} />
                      </td>
                      <td className="mono">#{n.id}</td>
                      <td onClick={(e) => e.stopPropagation()}>
                        {editingId === n.id ? (
                          <span>
                            <input autoFocus className="name-input" value={editDraft} maxLength={160}
                                   disabled={editBusy} aria-label={`Rename node ${n.id}`}
                                   onChange={(e) => { setEditDraft(e.target.value); setEditError(null) }}
                                   onKeyDown={onInlineEditKeyDown(n)}
                                   onBlur={() => { if (!editBusy) cancelInlineEdit() }} />
                            {editError && <span className="name-error" role="alert">{editError}</span>}
                          </span>
                        ) : (
                          <span className="name-cell">
                            <button type="button" className="name-text"
                                    title="Click to rename · Enter saves · Escape cancels"
                                    onClick={() => startInlineEdit(n)}>{n.name}</button>
                            <button type="button" className="name-edit-btn" aria-label={`Rename node ${n.id}`}
                                    onClick={() => startInlineEdit(n)}>
                              <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor"
                                   strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                                <path d="M17 3a2.85 2.85 1 1 1 4 4L8 20l-5 1 1-5L17 3z" />
                              </svg>
                            </button>
                            {n.description && <div className="row-desc">{n.description}</div>}
                          </span>
                        )}
                      </td>
                      <td><span className={`badge ${n.origin}`}>{n.origin}</span></td>
                      <td>{n.timeframe ? <span className="tf-badge">{n.timeframe}</span> : <span className="tf-badge none" title="Draft — no spec, no timeframe">—</span>}</td>
                      <td className="mono">v{n.version}{n.parent_node_id ? <span className="faint">↳#{n.parent_node_id}</span> : null}</td>
                      <td className="mono faint" style={{ fontSize: 10.5 }}>{n.spec_hash ? `${n.spec_hash.slice(0, 12)}…` : '—'}</td>
                      <td><span className="badge DRAFT">{n.status}</span></td>
                      <td><span className={`badge ${n.approval_state === 'APPROVED' ? 'APPROVED' : 'PENDING'}`}>{n.approval_state}</span></td>
                      <td onClick={(e) => e.stopPropagation()}>
                        <span className="node-actions">
                          <button className="btn btn-ghost btn-sm" type="button" onClick={() => { setSelectedId(n.id); setLineage(null) }}>{n.id === selectedId ? 'Selected' : 'Inspect'}</button>
                          {n.spec_json && <button className="btn btn-ghost btn-sm" type="button" title={`Create an independent variant of node #${n.id}`} onClick={() => startDuplicate(n)}>Duplicate &amp; Edit</button>}
                          <button className="btn-danger" type="button" title={`Delete node #${n.id}`} onClick={() => void openDelete([n.id])}>Delete</button>
                        </span>
                      </td>
                    </tr>
                      ))}
                    </Fragment>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {selected && (
            <div style={{ borderTop: '1px solid var(--border)', marginTop: 10, paddingTop: 10 }}>
              <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', fontSize: 11.5 }}>
                <strong>#{selected.id} · v{selected.version}</strong>
                {selected.timeframe
                  ? <span className="tf-badge">{selected.timeframe}</span>
                  : <span className="tf-badge none" title="Draft — no spec, no timeframe">draft · no TF</span>}
                <span className="tf-badge awaiting" title="No market-data store ships in this build (Phase 4) — a saved node is a validated spec, never a backtested result">AWAITING MARKET DATA</span>
                <span className="faint" style={{ fontSize: 10 }}>DATA UNAVAILABLE — Phase 4</span>
                <span className="faint">created by {selected.created_by}</span>
                {selected.parent_node_id && <span className="faint">derived from #{selected.parent_node_id}</span>}
                <button className="btn btn-ghost btn-sm" type="button" onClick={showLineage}>Lineage</button>
                {selected.spec_json && <button className="btn btn-ghost btn-sm" type="button" onClick={loadIntoEditor}>Load into editor</button>}
              </div>
              {selected.spec_hash && <div className="mono faint" style={{ fontSize: 10, marginTop: 4, wordBreak: 'break-all' }}>sha256 {selected.spec_hash}</div>}
              {lineage && (
                <div style={{ marginTop: 8 }}>
                  <div className="faint" style={{ fontSize: 10.5, letterSpacing: 1, textTransform: 'uppercase' }}>Lineage (oldest → newest){lineage.lineage_root_id ? ` · root #${lineage.lineage_root_id}` : ''}</div>
                  <ol style={{ margin: '6px 0 0', paddingLeft: 18, fontSize: 11.5 }}>
                    {lineage.items.map((n) => (
                      <li key={n.id} className="mono">#{n.id} · v{n.version}{n.parent_node_id ? ` ← #${n.parent_node_id}` : ' (root)'} · {n.spec_hash ? `${n.spec_hash.slice(0, 12)}…` : 'no spec'}</li>
                    ))}
                  </ol>
                </div>
              )}
              {selected.spec_json && (
                <pre className="input mono" style={{ maxHeight: 200, overflow: 'auto', fontSize: 10, marginTop: 8 }}>{selected.spec_json}</pre>
              )}
            </div>
          )}
        </div>
      </div>

      {deleteIds !== null && (
        <div className="modal-overlay" role="dialog" aria-modal="true" aria-label="Confirm node deletion">
          <div className="modal-card">
            <h3>{deleteIds.length === 1 ? 'Delete node?' : `Delete ${deleteIds.length} selected nodes?`}</h3>
            {deleteIds.length === 1 ? (
              <p style={{ fontSize: 12 }}>
                Node <strong>#{deleteIds[0]}</strong>
                {(() => { const n = nodes.find((x) => x.id === deleteIds[0]); return n ? <> · <strong>{n.name}</strong> ({n.timeframe ?? 'draft'}, v{n.version})</> : null })()}
                {' '}will be permanently deleted from the {instrument} registry. This cannot be undone; the deletion is recorded in the LOG audit trail.
              </p>
            ) : (
              <p style={{ fontSize: 12 }}>
                Nodes {deleteIds.map((id) => `#${id}`).join(', ')} will be permanently deleted from the {instrument} registry.
                Each deletion is recorded in the LOG audit trail. Nodes that cannot be deleted are reported individually and left untouched.
              </p>
            )}
            {deleteIds.length === 1 && deleteDeps === null && <p className="faint" style={{ fontSize: 11 }}>Checking dependencies…</p>}
            {deleteDeps && deleteDeps.descendant_count > 0 && (
              <div className="banner" role="note" style={{ margin: '8px 0' }}>
                <span aria-hidden="true">⚠</span>
                <span>This node has <strong>{deleteDeps.descendant_count} descendant(s)</strong> ({deleteDeps.descendants.map((d) => `#${d}`).join(', ')}). They are never cascade-deleted silently.
                  <label style={{ display: 'flex', gap: 6, alignItems: 'center', marginTop: 6 }}>
                    <input type="checkbox" checked={deleteIncludeDescendants} onChange={(e) => setDeleteIncludeDescendants(e.target.checked)} />
                    Also delete the descendant subtree explicitly
                  </label>
                </span>
              </div>
            )}
            {deleteDeps && deleteDeps.dependent_total > 0 && (
              <div className="field-error" role="alert">
                Blocked: {deleteDeps.blocked_reason}. Dependent testing/live records are never deleted with a node.
              </div>
            )}
            {deleteIds.length > 1 && (
              <label style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 12, margin: '8px 0' }}>
                <input type="checkbox" checked={deleteIncludeDescendants} onChange={(e) => setDeleteIncludeDescendants(e.target.checked)} />
                Where a selected node has descendants, delete its subtree too (each subtree still fails safely if dependent records exist)
              </label>
            )}
            <div style={{ display: 'flex', gap: 10, marginTop: 14 }}>
              <button type="button" className="btn-danger" disabled={deleteBusy || (deleteDeps !== null && deleteDeps.dependent_total > 0)}
                      onClick={() => void confirmDelete()}>
                {deleteBusy ? 'Deleting…' : deleteIds.length === 1 ? 'Delete node' : `Delete ${deleteIds.length} nodes`}
              </button>
              <button type="button" className="btn btn-ghost" disabled={deleteBusy} onClick={() => setDeleteIds(null)}>Cancel</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
