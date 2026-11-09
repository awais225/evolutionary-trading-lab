export type ComparisonClass = 'DIRECTLY COMPARABLE' | 'PARTIALLY COMPARABLE' | 'INCOMPATIBLE SETUPS'
export type MonteCarloRunLike = Record<string, any>

const fields: [string,string][] = [
  ['deeptest_run_id','DeepTesting run ID'],['fingerprint','Strategy fingerprint'],['node_version','Strategy version'],
  ['instrument','Instrument'],['timeframe','Timeframe'],['tested_range','Tested date range'],
  ['initial_capital','Starting capital'],['risk_pct','Risk percentage'],['max_active_trades','Maximum active trades'],
  ['method','Simulation method'],['simulations','Simulation count'],['seed','Seed'],['random_points','Random points'],
  ['displayed_curves','Displayed curve count'],['block_size','Bootstrap block size'],['stress_multiplier','Execution stress multiplier'],
  ['execution_cost_assumptions','Execution-cost assumptions'],['source_data_readiness','Data readiness'],['known_logic_compliance_failures','Known logic/compliance failures'],
]
const value = (r: MonteCarloRunLike, k: string): unknown => {
  if (k === 'tested_range') return r.config?.tested_range ?? r.config?.actual_range ?? r.config?.requested_range ?? r.config?.range ?? null
  if (k === 'execution_cost_assumptions') return r.config?.execution_cost_assumptions ?? r.execution_cost_assumptions ?? null
  if (k === 'source_data_readiness') return r.config?.source_data_readiness ?? r.source_data_readiness ?? null
  if (k === 'known_logic_compliance_failures') return r.config?.known_logic_compliance_failures ?? r.known_logic_compliance_failures ?? null
  return r[k] ?? r.config?.[k] ?? null
}
const same = (a: unknown,b: unknown) => JSON.stringify(a) === JSON.stringify(b)
const display = (v:unknown) => v==null?'N/A':typeof v==='object'?JSON.stringify(v):String(v)
const identityKeys = new Set(['deeptest_run_id','fingerprint','node_version','instrument','timeframe','tested_range','method'])
const requiredRecorded = ['deeptest_run_id','fingerprint','node_version','instrument','timeframe','tested_range','initial_capital','risk_pct','max_active_trades','method','simulations','seed','execution_cost_assumptions','source_data_readiness']

export function compareMonteCarloRuns(a: MonteCarloRunLike,b: MonteCarloRunLike): {classification:ComparisonClass; differences:string[]} {
  const differences=fields.filter(([key])=>!same(value(a,key),value(b,key))).map(([key,label])=>`${label} (${display(value(a,key))} vs ${display(value(b,key))})`)
  const missing=requiredRecorded.filter(key=>value(a,key)==null||value(b,key)==null)
  for(const key of missing){const label=fields.find(([k])=>k===key)?.[1]??key;if(!differences.some(d=>d.startsWith(`${label} `)))differences.push(`${label} (not recorded for one or both runs)`)}
  const isPartial=(r:MonteCarloRunLike)=>!!(r.result?.partial??r.partial)||['PARTIAL','CANCELLED','INTERRUPTED','FAILED'].includes(String(r.status??'').toUpperCase())
  if (isPartial(a)!==isPartial(b)) differences.push(`Completion state (${a.status??(isPartial(a)?'PARTIAL':'COMPLETE')} vs ${b.status??(isPartial(b)?'PARTIAL':'COMPLETE')})`)
  const hasIdentityDifference=fields.some(([key])=>identityKeys.has(key)&&!same(value(a,key),value(b,key)))
  const partialDifference=isPartial(a)!==isPartial(b)
  const incompletelyRecorded=missing.length>0
  return {classification:hasIdentityDifference?'INCOMPATIBLE SETUPS':partialDifference||incompletelyRecorded?'PARTIALLY COMPARABLE':differences.length?'PARTIALLY COMPARABLE':'DIRECTLY COMPARABLE',differences}
}
