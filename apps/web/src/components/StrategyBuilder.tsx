import { useMemo, useState } from 'react'
import {
  INDICATORS, INDICATOR_CATEGORIES, PRICE_SOURCES, CANDLE_FIELDS,
  UNSUPPORTED_INDICATORS, OPERATORS,
  defaultParams, getIndicator, validateParamsClient,
} from '../builder/indicatorMeta'
import {
  MAX_TREE_NODES, MAX_OFFSET, MAX_CHILDREN,
  isComparison, isLogic, treeSize, treeDepth,
  nodeAt, updateAt, removeAt, insertChildAt, updateOperand,
  collectIndicators, comparisonSummary, logicLabel,
  emptyComparison, type Path,
} from '../builder/specTree'

const DIR_OPTIONS = ['LONG', 'SHORT', 'BOTH'] as const
const SL_ANCHORS = ['entry_price', 'signal_low', 'signal_high', 'atr']
const DIST_UNITS = [
  { value: 'absolute', label: 'ABSOLUTE' },
  { value: 'points', label: 'POINTS' },
  { value: 'ticks', label: 'TICKS' },
  { value: 'pips', label: 'PIPS' },
  { value: 'atr_multiple', label: 'ATR_MULTIPLE' },
]

interface StrategyBuilderProps {
  spec: any
  onChange: (spec: any) => void
  jsonError: string | null
  onRevertJson: () => void
  instrument: string
}

function parseNum(raw: string): number | null {
  if (raw.trim() === '') return null
  const v = Number(raw)
  return Number.isFinite(v) ? v : null
}

export function StrategyBuilder({ spec, onChange, jsonError, onRevertJson, instrument }: StrategyBuilderProps) {
  const [query, setQuery] = useState('')
  const [builderError, setBuilderError] = useState<string | null>(null)

  const entry = spec?.entry
  const exits = spec?.exits ?? {}
  const identity = spec?.identity ?? {}
  const execution = spec?.execution ?? {}

  const apply = (fn: (draft: any) => any) => {
    if (jsonError) { setBuilderError('JSON is invalid — the visual builder is read-only until it parses. Use “Revert JSON to last valid”.'); return }
    const next = fn(spec)
    const size = treeSize(next?.entry)
    if (size > MAX_TREE_NODES) { setBuilderError(`Expression limit reached (${MAX_TREE_NODES} nodes) — remove a condition or group.`); return }
    setBuilderError(null)
    onChange(next)
  }
  const patch = (path: string[], value: any) => apply((draft) => {
    const clone = structuredClone(draft)
    let cur = clone
    for (let i = 0; i < path.length - 1; i++) { cur[path[i]] = cur[path[i]] ?? {}; cur = cur[path[i]] }
    cur[path[path.length - 1]] = value
    return clone
  })
  const patchIdentity = (key: string, value: unknown) => patch(['identity', key], value)
  const patchExec = (key: string, value: unknown) => patch(['execution', key], value)
  const patchExits = (value: any) => patch(['exits'], value)

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    return INDICATORS.filter((i) => !q || i.name.toLowerCase().includes(q) || i.category.toLowerCase().includes(q) || i.description.toLowerCase().includes(q))
  }, [query])

  const usedIndicators = useMemo(() => collectIndicators(entry), [entry])
  const uniqueIndicatorKeys = useMemo(() => {
    const seen = new Set<string>()
    return usedIndicators.filter((i) => {
      const k = `${i.name}|${JSON.stringify(i.params)}|${i.field ?? ''}`
      if (seen.has(k)) return false
      seen.add(k); return true
    })
  }, [usedIndicators])

  const damaged = !!jsonError
  const summary = useMemo(() => buildSummary(spec, treeDepth(entry)), [spec, entry])

  // ---------- entry-tree operations ----------
  const addComparison = (indicatorName?: string) => {
    const cmp = indicatorName
      ? (() => {
          const ind = getIndicator(indicatorName)!
          const params = defaultParams(ind)
          const field = ind.fields ? ind.fields[0] : undefined
          return {
            type: 'compare', op: 'gt',
            left: { kind: 'indicator', name: ind.name, params, ...(field ? { field } : {}), offset: 0 },
            right: { kind: 'constant', value: 0 },
          }
        })()
      : emptyComparison()
    apply((draft) => {
      const clone = structuredClone(draft)
      if (isLogic(clone.entry) && Array.isArray(clone.entry.children)) {
        if (clone.entry.children.length >= MAX_CHILDREN) { setBuilderError(`A group may hold at most ${MAX_CHILDREN} conditions.`); return draft }
        clone.entry = { ...clone.entry, children: [...clone.entry.children, cmp] }
      } else {
        // bare comparison at root → wrap in AND (no data loss)
        clone.entry = { type: 'and', children: [clone.entry, cmp] }
      }
      return clone
    })
  }

  const setRootCombinator = (type: 'and' | 'or') => {
    apply((draft) => {
      const clone = structuredClone(draft)
      if (isLogic(clone.entry) && Array.isArray(clone.entry.children)) clone.entry = { ...clone.entry, type }
      else clone.entry = { type, children: [clone.entry] }
      return clone
    })
  }

  const removeNode = (path: Path) => apply((draft) => ({ ...structuredClone(draft), entry: removeAt(cloneEntry(draft), path) }))
  const cloneEntry = (d: any) => structuredClone(d?.entry)
  const duplicateNode = (path: Path) => apply((draft) => {
    const clone = structuredClone(draft)
    const parentPath = path.slice(0, -1)
    const idx = path[path.length - 1]
    if (typeof idx !== 'number') return draft
    const parent = nodeAt(clone.entry, parentPath)
    if (Array.isArray(parent?.children)) {
      if (parent.children.length >= MAX_CHILDREN) { setBuilderError(`A group may hold at most ${MAX_CHILDREN} conditions.`); return draft }
      const copy = structuredClone(parent.children[idx])
      return { ...clone, entry: insertChildAt(clone.entry, parentPath, copy) }
    }
    return draft
  })
  const setOp = (path: Path, op: string) => apply((draft) => {
    const clone = structuredClone(draft)
    return { ...clone, entry: updateAt(clone.entry, path, (cmp: any) => ({ ...cmp, op })) }
  })
  const setCombinator = (path: Path, type: 'and' | 'or') => apply((draft) => {
    const clone = structuredClone(draft)
    return { ...clone, entry: updateAt(clone.entry, path, (g: any) => ({ ...g, type })) }
  })

  // ---------- renderers ----------
  const renderOperand = (cmpPath: Path, side: 'left' | 'right', op: any) => {
    const setKind = (kind: string) => apply((draft) => {
      const clone = structuredClone(draft)
      const next = kind === 'indicator'
        ? (() => { const ind = INDICATORS[0]; return { kind: 'indicator', name: ind.name, params: defaultParams(ind), ...(ind.fields ? { field: ind.fields[0] } : {}), offset: 0 } })()
        : kind === 'candle'
          ? { kind: 'candle', field: 'close', offset: 0 }
          : { kind: 'constant', value: 0 }
      return { ...clone, entry: updateOperand(clone.entry, cmpPath, side, () => next) }
    })
    const setOperand = (patchFn: (operand: any) => any) => apply((draft) => {
      const clone = structuredClone(draft)
      return { ...clone, entry: updateOperand(clone.entry, cmpPath, side, patchFn) }
    })

    return (
      <div className="operand-editor">
        <select className="input input-xs" value={op?.kind} onChange={(e) => setKind(e.target.value)} aria-label={`${side} operand type`}>
          <option value="indicator">Indicator</option>
          <option value="candle">Candle</option>
          <option value="constant">Number</option>
        </select>

        {op?.kind === 'indicator' && (
          <IndicatorOperandEditor op={op} onChange={(fn) => setOperand(fn)} disabled={damaged} />
        )}
        {op?.kind === 'candle' && (
          <div className="operand-row">
            <select className="input input-xs" value={op.field} onChange={(e) => setOperand((o) => ({ ...o, field: e.target.value }))} aria-label="Candle field">
              {CANDLE_FIELDS.map((f) => <option key={f} value={f}>{f.toUpperCase()}</option>)}
            </select>
            <label className="mini-field">offset
              <input className="input input-xs num" type="number" min={0} max={MAX_OFFSET} value={op.offset ?? 0}
                     onChange={(e) => { const v = parseNum(e.target.value); if (v !== null && Number.isInteger(v) && v >= 0 && v <= MAX_OFFSET) setOperand((o) => ({ ...o, offset: v })) }} />
            </label>
          </div>
        )}
        {op?.kind === 'constant' && (
          <input className="input input-xs num" type="number" step="any" value={op.value ?? 0}
                 onChange={(e) => { const v = parseNum(e.target.value); if (v !== null) setOperand(() => ({ kind: 'constant', value: v })) }}
                 aria-label="Constant value" />
        )}
      </div>
    )
  }

  const renderComparison = (cmp: any, path: Path, index: number) => {
    const problems = cmp.left?.kind === 'indicator'
      ? validateParamsClient(cmp.left.name, cmp.left.params ?? {}, cmp.left.field)
      : []
    const problemsR = cmp.right?.kind === 'indicator'
      ? validateParamsClient(cmp.right.name, cmp.right.params ?? {}, cmp.right.field)
      : []
    const allProblems = [...problems.map((p) => `left: ${p}`), ...problemsR.map((p) => `right: ${p}`)]
    return (
      <div className={`cond-row${allProblems.length ? ' cond-row-invalid' : ''}`} key={index}>
        <div className="cond-row-head">
          <code className="cond-summary">{comparisonSummary(cmp)}</code>
          <div className="cond-row-actions">
            <button type="button" className="btn btn-ghost btn-xs" title="Duplicate condition" disabled={damaged} onClick={() => duplicateNode(path)}>⧉</button>
            <button type="button" className="btn btn-ghost btn-xs" title="Remove condition" disabled={damaged} onClick={() => removeNode(path)}>✕</button>
          </div>
        </div>
        <div className="cond-row-body">
          {renderOperand(path, 'left', cmp.left)}
          <select className="input input-xs op-select" value={cmp.op} onChange={(e) => setOp(path, e.target.value)} aria-label="Comparison operator">
            {OPERATORS.map((o) => <option key={o.op} value={o.op}>{o.label}</option>)}
          </select>
          {renderOperand(path, 'right', cmp.right)}
        </div>
        {allProblems.length > 0 && <div className="cond-problems">{allProblems.map((p, i) => <div key={i}>• {p}</div>)}</div>}
      </div>
    )
  }

  const renderGroup = (node: any, path: Path, depth: number): JSX.Element => {
    if (isComparison(node)) return renderComparison(node, path, 0)
    if (node?.type === 'not') {
      return (
        <div className="cond-group cond-not">
          <div className="cond-group-head"><span className="group-chip not">NOT</span>
            <button type="button" className="btn btn-ghost btn-xs" title="Remove NOT (keeps the inner condition)" onClick={() => removeNode(path)}>unwrap</button>
          </div>
          <div className="cond-group-body">{renderGroup(node.child, [...path, 'child'], depth + 1)}</div>
        </div>
      )
    }
    if (isLogic(node) && Array.isArray(node.children)) {
      const type = node.type as 'and' | 'or'
      return (
        <div className="cond-group">
          <div className="cond-group-head">
            <div className="segmented mini" role="group" aria-label="Group logic">
              <button type="button" className={type === 'and' ? 'active' : ''} onClick={() => (path.length === 0 ? setRootCombinator('and') : setCombinator(path, 'and'))}>AND</button>
              <button type="button" className={type === 'or' ? 'active' : ''} onClick={() => (path.length === 0 ? setRootCombinator('or') : setCombinator(path, 'or'))}>OR</button>
            </div>
            <span className="faint cond-group-count">{node.children.length}/{MAX_CHILDREN}</span>
            {path.length > 0 && (
              <div className="cond-row-actions">
                <button type="button" className="btn btn-ghost btn-xs" title="Duplicate group" onClick={() => duplicateNode(path)}>⧉ group</button>
                <button type="button" className="btn btn-ghost btn-xs" title="Delete group" onClick={() => removeNode(path)}>✕ group</button>
              </div>
            )}
          </div>
          <div className="cond-group-body">
            {node.children.map((child: any, i: number) => (
              <div key={i}>
                {isComparison(child)
                  ? renderComparison(child, [...path, i], i)
                  : isLogic(child) || child?.type === 'not'
                    ? renderGroup(child, [...path, i], depth + 1)
                    : <div className="unsupported-chip">Unsupported node type “{String(child?.type)}” — edit in the JSON editor; it will be preserved.</div>}
              </div>
            ))}
            {node.children.length === 0 && <div className="faint">Empty group — invalid. Add a condition.</div>}
            <div className="cond-group-add">
              <button type="button" className="btn btn-ghost btn-xs" disabled={damaged} onClick={() => {
                apply((draft) => {
                  const clone = structuredClone(draft)
                  const target = nodeAt(clone.entry, path)
                  if (Array.isArray(target?.children) && target.children.length >= MAX_CHILDREN) { setBuilderError(`A group may hold at most ${MAX_CHILDREN} conditions.`); return draft }
                  return insertChildAt(clone.entry, path, emptyComparison())
                })
              }}>+ condition</button>
              {path.length < 2 && (
                <button type="button" className="btn btn-ghost btn-xs" onClick={() => apply((draft) => {
                  const clone = structuredClone(draft)
                  const target = nodeAt(clone.entry, path)
                  if (Array.isArray(target?.children) && target.children.length >= MAX_CHILDREN) { setBuilderError(`A group may hold at most ${MAX_CHILDREN} conditions.`); return draft }
                  return insertChildAt(clone.entry, path, { type: 'and', children: [emptyComparison()] })
                })}>+ nested group</button>
              )}
            </div>
          </div>
        </div>
      )
    }
    return <div className="unsupported-chip">Unsupported expression node “{String(node?.type)}” — edit in the JSON editor; it will be preserved.</div>
  }

  const rootIsBare = entry && !isLogic(entry)

  return (
    <div className="strategy-builder" aria-disabled={damaged || undefined}>
      {damaged && (
        <div className="json-invalid-banner" role="alert">
          <div><strong>JSON is invalid.</strong> The visual builder shows the last valid specification and is read-only until the JSON parses again.</div>
          <button type="button" className="btn btn-ghost btn-xs" onClick={onRevertJson}>Revert JSON to last valid</button>
        </div>
      )}
      {builderError && <div className="json-invalid-banner" role="alert">{builderError}</div>}

      {/* ================= SECTION 1 — ENTRY CONDITIONS ================= */}
      <section className="builder-section" aria-labelledby="sec-entry">
        <h4 id="sec-entry"><span className="section-num">1</span> Entry Conditions</h4>
        <fieldset className="direction-block" aria-describedby="sec-entry">
          <legend className="direction-legend">Direction <span className="faint">— part of the strategy identity &amp; fingerprint; cannot be flipped later without forking</span></legend>
          <div className="segmented direction-segmented" role="radiogroup" aria-label="Strategy direction">
            {DIR_OPTIONS.map((d) => {
              const current = identity?.direction ?? 'BOTH'
              return (
                <button key={d} type="button" role="radio" aria-checked={current === d}
                        className={current === d ? 'active dir-' + d.toLowerCase() : `dir-${d.toLowerCase()}`}
                        disabled={damaged}
                        onClick={() => patchIdentity('direction', d)}>
                  {d === 'BOTH' ? 'BOTH (long + short)' : d}
                </button>
              )
            })}
          </div>
          <p className="faint direction-hint">
            {(identity?.direction ?? 'BOTH') === 'LONG' && 'This strategy will only evaluate long opportunities.'}
            {(identity?.direction ?? 'BOTH') === 'SHORT' && 'This strategy will only evaluate short opportunities.'}
            {(identity?.direction ?? 'BOTH') === 'BOTH' && 'This strategy evaluates both long and short opportunities (default).'}
          </p>
        </fieldset>
        <div className="entry-layout">
          <aside className="indicator-library">
            <input className="input input-sm" placeholder="Search indicators…" value={query}
                   onChange={(e) => setQuery(e.target.value)} aria-label="Search indicators" />
            <div className="library-scroll">
              {INDICATOR_CATEGORIES.map((cat) => {
                const items = filtered.filter((i) => i.category === cat)
                if (items.length === 0) return null
                return (
                  <div key={cat} className="library-category">
                    <div className="library-category-title">{cat}</div>
                    {items.map((ind) => (
                      <button key={ind.name} type="button" className="library-item" disabled={damaged}
                              title={`${ind.description} · warm-up ≈ ${ind.warmup(defaultParams(ind))} candles`}
                              onClick={() => addComparison(ind.name)}>
                        <span className="library-item-name">{ind.name}</span>
                        <span className="library-item-desc">{ind.description}</span>
                      </button>
                    ))}
                  </div>
                )
              })}
              <div className="library-category">
                <div className="library-category-title">Not supported (backend registry)</div>
                {UNSUPPORTED_INDICATORS.map((u) => (
                  <div key={u.name} className="library-item library-item-disabled" title={u.reason}>
                    <span className="library-item-name">{u.name}</span>
                    <span className="library-item-desc">{u.reason}</span>
                  </div>
                ))}
              </div>
            </div>
            <p className="faint library-note">Library mirrors the backend indicator registry exactly ({INDICATORS.length} identifiers). The backend revalidates everything.</p>
          </aside>

          <div className="entry-tree">
            {rootIsBare ? (
              <div className="unsupported-chip">
                The entry is a single comparison.
                <button type="button" className="btn btn-ghost btn-xs" onClick={() => onChange({ ...structuredClone(spec), entry: { type: 'and', children: [entry] } })}>Wrap in an AND group to edit</button>
              </div>
            ) : renderGroup(entry, [], 1)}
          </div>
        </div>
      </section>

      {/* ================= SECTION 2 — EXIT CONDITIONS ================= */}
      <section className="builder-section" aria-labelledby="sec-exits">
        <h4 id="sec-exits"><span className="section-num">2</span> Exit Conditions <span className="faint">(explicit — never auto-invented)</span></h4>
        <div className="exits-grid">
          <fieldset className="exit-block">
            <legend><label className="check"><input type="checkbox" checked={!!exits.stop_loss} disabled={damaged}
              onChange={(e) => patchExits(e.target.checked
                ? { ...exits, stop_loss: { anchor: 'entry_price', distance: { unit: 'pips', value: 50 } } }
                : (() => { const { stop_loss, ...rest } = exits; return rest })())} /> Stop loss</label></legend>
            {exits.stop_loss && (
              <div className="exit-body">
                <label className="mini-field">anchor
                  <select className="input input-xs" value={exits.stop_loss.anchor ?? 'entry_price'}
                          onChange={(e) => patchExits({ ...exits, stop_loss: { ...exits.stop_loss, anchor: e.target.value } })}>
                    {SL_ANCHORS.map((a) => <option key={a} value={a}>{a}</option>)}
                  </select></label>
                <DistanceEditor value={exits.stop_loss.distance ?? { unit: 'pips', value: 50 }}
                                onChange={(d) => patchExits({ ...exits, stop_loss: { ...exits.stop_loss, distance: d } })} disabled={damaged} />
              </div>
            )}
          </fieldset>

          <fieldset className="exit-block">
            <legend><label className="check"><input type="checkbox" checked={!!exits.take_profit} disabled={damaged}
              onChange={(e) => patchExits(e.target.checked
                ? { ...exits, take_profit: { ratio: 1.5 } }
                : (() => { const { take_profit, ...rest } = exits; return rest })())} /> Take profit</label></legend>
            {exits.take_profit && (
              <div className="exit-body">
                <div className="segmented mini" role="group" aria-label="Take profit mode">
                  <button type="button" className={'ratio' in exits.take_profit ? 'active' : ''}
                          onClick={() => patchExits({ ...exits, take_profit: { ratio: exits.take_profit?.ratio ?? 1.5 } })}>R multiple</button>
                  <button type="button" className={'distance' in exits.take_profit ? 'active' : ''}
                          onClick={() => patchExits({ ...exits, take_profit: { distance: exits.take_profit?.distance ?? { unit: 'pips', value: 75 } } })}>Distance</button>
                </div>
                {'ratio' in exits.take_profit && (
                  <label className="mini-field">R multiple (0–100)
                    <input className="input input-xs num" type="number" min={0} max={100} step="0.1" value={exits.take_profit.ratio}
                           onChange={(e) => { const v = parseNum(e.target.value); if (v !== null && v > 0 && v <= 100) patchExits({ ...exits, take_profit: { ratio: v } }) }} />
                  </label>
                )}
                {'distance' in exits.take_profit && (
                  <DistanceEditor value={exits.take_profit.distance}
                                  onChange={(d) => patchExits({ ...exits, take_profit: { distance: d } })} disabled={damaged} />
                )}
              </div>
            )}
          </fieldset>

          <fieldset className="exit-block">
            <legend><label className="check"><input type="checkbox" checked={exits.max_holding_candles != null} disabled={damaged}
              onChange={(e) => {
                const { max_holding_candles, ...rest } = exits
                patchExits(e.target.checked ? { ...rest, max_holding_candles: 24 } : rest)
              }} /> Max holding candles</label></legend>
            {exits.max_holding_candles != null && (
              <input className="input input-xs num" type="number" min={1} max={100000} value={exits.max_holding_candles}
                     onChange={(e) => { const v = parseNum(e.target.value); if (v !== null && Number.isInteger(v) && v >= 1 && v <= 100000) patchExits({ ...exits, max_holding_candles: v }) }} />
            )}
          </fieldset>

          <fieldset className="exit-block">
            <legend><label className="check"><input type="checkbox" checked={!!exits.time_exit_utc} disabled={damaged}
              onChange={(e) => {
                const { time_exit_utc, ...rest } = exits
                patchExits(e.target.checked ? { ...rest, time_exit_utc: '20:30' } : rest)
              }} /> Time exit (UTC)</label></legend>
            {!!exits.time_exit_utc && (
              <input className="input input-xs" type="time" value={exits.time_exit_utc}
                     onChange={(e) => patchExits({ ...exits, time_exit_utc: e.target.value })} />
            )}
          </fieldset>

          {exits.explicit_exit && (
            <fieldset className="exit-block exit-block-note">
              <legend>Explicit exit expression</legend>
              <p className="faint">Configured — edit it in the JSON editor (tree preserved).</p>
            </fieldset>
          )}
        </div>
        {!exits.stop_loss && !exits.take_profit && exits.max_holding_candles == null && !exits.time_exit_utc && !exits.explicit_exit && (
          <p className="field-error">At least one exit is required — validation will reject this spec (EXIT_REQUIRED). Add a stop loss, take profit, max holding or time exit.</p>
        )}
      </section>

      {/* ================= SECTION 3 — EXECUTION & DESCRIPTION ================= */}
      <section className="builder-section" aria-labelledby="sec-exec">
        <h4 id="sec-exec"><span className="section-num">3</span> Execution &amp; description</h4>
        <div className="info-grid">
          <label className="mini-field wide">Description <span className="faint">(display only — not part of the semantic fingerprint)</span>
            <textarea className="input input-sm" rows={2} value={identity?.description ?? ''} disabled={damaged}
                      onChange={(e) => patchIdentity('description', e.target.value)} placeholder="What does this strategy do?" /></label>
        </div>
        <div className="execution-row">
          <span className="faint">Execution:</span>
          <label className="check small"><input type="radio" name="entry_timing" checked={(execution?.entry_timing ?? 'next_bar_open') === 'next_bar_open'}
                 onChange={() => patchExec('entry_timing', 'next_bar_open')} /> next bar open</label>
          <label className="check small"><input type="radio" name="entry_timing" checked={execution?.entry_timing === 'signal_close'}
                 onChange={() => patchExec('entry_timing', 'signal_close')} /> signal close</label>
          <label className="check small"><input type="checkbox" checked={execution?.permit_forming_candle === true}
                 onChange={(e) => { patchExec('permit_forming_candle', e.target.checked); patchExec('candle', e.target.checked ? 'forming' : 'completed') }} /> permit forming candle</label>
        </div>
        <p className="faint" style={{ marginTop: 6 }}>Sizing, risk %, stop placement overrides and capital allocation are <strong>not</strong> configured here — they belong to DeepTesting / Live Testing stages.</p>
      </section>

      {/* ================= INDICATOR VALUE PREVIEW ================= */}
      <section className="builder-section preview-section" aria-labelledby="sec-preview">
        <h4 id="sec-preview"><span className="section-num">∑</span> Indicator preview <span className="faint">— read-only, never trades</span></h4>
        <div className="preview-meta">Instrument <span className="mono">{instrument}</span> · timeframe <span className="mono">{identity?.timeframe ?? '?'}</span></div>
        {uniqueIndicatorKeys.length === 0 ? (
          <p className="faint">No indicators in this strategy. Add some from the library above.</p>
        ) : (
          <ul className="preview-list">
            {uniqueIndicatorKeys.map((ind, i) => (
              <li key={i} className="preview-row">
                <code>{ind.name}({Object.entries(ind.params).map(([k, v]) => `${k}=${v}`).join(', ')}){ind.field ? `.${ind.field}` : ''}</code>
                <span className="preview-unavailable">INDICATOR ENGINE NOT AVAILABLE</span>
              </li>
            ))}
          </ul>
        )}
        <p className="faint preview-note">Live values require the Phase 3 market-data store + indicator mathematics, which are not implemented yet. Missing data is never simulated — this panel will list real values once the engine lands. The builder itself is fully functional.</p>
        <div className="builder-summary" aria-label="Node summary">{summary}</div>
      </section>
    </div>
  )
}

function IndicatorOperandEditor({ op, onChange, disabled }: { op: any; onChange: (fn: (o: any) => any) => void; disabled?: boolean }) {
  const ind = getIndicator(op?.name ?? 'SMA') ?? INDICATORS[0]
  const problems = validateParamsClient(ind.name, op.params ?? {}, op.field)
  const selectName = (name: string) => onChange(() => {
    const next = getIndicator(name)!
    return { kind: 'indicator', name, params: defaultParams(next), ...(next.fields ? { field: next.fields[0] } : {}), offset: op.offset ?? 0 }
  })
  const setParam = (key: string, raw: string) => onChange((o) => {
    const p = ind.params[key]
    if (p.kind === 'int') {
      const v = Number(raw)
      if (!Number.isInteger(v)) return o // keep last good value; JSON stays valid
      return { ...o, params: { ...o.params, [key]: v } }
    }
    if (p.kind === 'float') {
      const v = Number(raw)
      if (!Number.isFinite(v)) return o
      return { ...o, params: { ...o.params, [key]: v } }
    }
    return { ...o, params: { ...o.params, [key]: raw } }
  })
  return (
    <div className="indicator-editor">
      <select className="input input-xs" value={ind.name} onChange={(e) => selectName(e.target.value)} disabled={disabled} aria-label="Indicator">
        {INDICATORS.map((i) => <option key={i.name} value={i.name}>{i.name}</option>)}
      </select>
      <div className="param-grid">
        {Object.entries(ind.params).map(([key, p]) => (
          <label key={key} className="mini-field" title={p.min !== undefined ? `${p.label} · range [${p.min}, ${p.max}]` : p.label}>
            {p.label}{p.required && <span className="req">*</span>}
            {p.kind === 'int' || p.kind === 'float' ? (
              <input className="input input-xs num" type="number" step={p.kind === 'int' ? 1 : 'any'}
                     min={p.min} max={p.max} value={String(op.params?.[key] ?? p.def)}
                     onChange={(e) => setParam(key, e.target.value)} disabled={disabled} />
            ) : p.kind === 'source' ? (
              <select className="input input-xs" value={String(op.params?.[key] ?? p.def)} onChange={(e) => setParam(key, e.target.value)} disabled={disabled}>
                {PRICE_SOURCES.map((s) => <option key={s} value={s}>{s}</option>)}
              </select>
            ) : (
              <select className="input input-xs" value={String(op.params?.[key] ?? p.def)} onChange={(e) => setParam(key, e.target.value)} disabled={disabled}>
                <option value="daily">daily</option>
              </select>
            )}
          </label>
        ))}
        {ind.fields && (
          <label className="mini-field" title="Multi-output selector">
            Output{ind.fieldRequired && <span className="req">*</span>}
            <select className="input input-xs" value={op.field ?? ''} disabled={disabled}
                    onChange={(e) => onChange((o) => ({ ...o, field: e.target.value }))} aria-label="Indicator output">
              {ind.fields.map((f) => <option key={f} value={f}>{f}</option>)}
            </select>
          </label>
        )}
        <label className="mini-field" title="Completed-candle offset (0 = latest completed)">
          Offset
          <input className="input input-xs num" type="number" min={0} max={MAX_OFFSET} value={op.offset ?? 0} disabled={disabled}
                 onChange={(e) => { const v = Number(e.target.value); if (Number.isInteger(v) && v >= 0 && v <= MAX_OFFSET) onChange((o) => ({ ...o, offset: v })) }} />
        </label>
      </div>
      {problems.length > 0 && <div className="cond-problems">{problems.map((p, i) => <div key={i}>• {p}</div>)}</div>}
    </div>
  )
}

function DistanceEditor({ value, onChange, disabled }: { value: { unit?: string; value?: number; atr_period?: number }; onChange: (d: any) => void; disabled?: boolean }) {
  const unit = value?.unit ?? 'pips'
  const needsAtr = unit === 'atr_multiple'
  return (
    <div className="distance-editor">
      <label className="mini-field">unit
        <select className="input input-xs" value={unit} disabled={disabled}
                onChange={(e) => {
                  const u = e.target.value
                  const next: any = { ...value, unit: u }
                  if (u === 'atr_multiple' && next.atr_period == null) next.atr_period = 14
                  if (u !== 'atr_multiple') delete next.atr_period
                  onChange(next)
                }}>
          {DIST_UNITS.map((u) => <option key={u.value} value={u.value}>{u.label}</option>)}
        </select></label>
      <label className="mini-field">value{needsAtr ? ' (×ATR)' : ''}
        <input className="input input-xs num" type="number" min={0} step="any" value={String(value?.value ?? '')} disabled={disabled}
               onChange={(e) => { const v = parseNum(e.target.value); if (v !== null && v > 0) onChange({ ...value, value: v }) }} />
      </label>
      {needsAtr && (
        <label className="mini-field">ATR period
          <input className="input input-xs num" type="number" min={1} max={100} value={value?.atr_period ?? 14} disabled={disabled}
                 onChange={(e) => { const v = Number(e.target.value); if (Number.isInteger(v) && v >= 1 && v <= 100) onChange({ ...value, atr_period: v }) }} />
        </label>
      )}
      <p className="faint micro">If broker metadata is missing, backend keeps NEEDS_METADATA — units are never assumed.</p>
    </div>
  )
}

function buildSummary(spec: any, depth: number): string {
  if (!spec) return 'No specification loaded.'
  const entry = spec.entry
  const count = treeSize(entry)
  const rootType = isLogic(entry) ? logicLabel(entry.type) : 'comparison'
  const exits: string[] = []
  const ex = spec.exits ?? {}
  if (ex.stop_loss?.distance) exits.push(`SL ${ex.stop_loss.distance.value} ${String(ex.stop_loss.distance.unit).toUpperCase()}`)
  if (ex.take_profit?.ratio != null) exits.push(`TP ${ex.take_profit.ratio}R`)
  if (ex.take_profit?.distance) exits.push(`TP ${ex.take_profit.distance.value} ${String(ex.take_profit.distance.unit).toUpperCase()}`)
  if (ex.max_holding_candles != null) exits.push(`max ${ex.max_holding_candles} candles`)
  if (ex.time_exit_utc) exits.push(`exit @ ${ex.time_exit_utc}Z`)
  if (ex.explicit_exit) exits.push('explicit exit')
  return `Entry: ${count} node(s) · ${rootType} · depth ${depth}   |   Exits: ${exits.length ? exits.join(' · ') : 'NONE (invalid)'}   |   ${spec.identity?.direction ?? 'BOTH'} · ${spec.identity?.timeframe ?? '?'}`
}
