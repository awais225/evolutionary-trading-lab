import {describe,it,expect} from 'vitest'
import {compareMonteCarloRuns} from '../lib/monteCarloComparison'
const base:any={id:'a',status:'COMPLETED',node_version:2,fingerprint:'fp',instrument:'XAUUSD',timeframe:'H1',deeptest_run_id:10,config:{actual_range:{start:1,end:2},initial_capital:10000,risk_pct:1,max_active_trades:1,method:'RESHUFFLE',simulations:500,seed:12,random_points:100,displayed_curves:10,block_size:10,stress_multiplier:1.5,execution_cost_assumptions:{spread_mode:'no_spread',commission_per_lot:0},source_data_readiness:'READY',known_logic_compliance_failures:0},result:{partial:false}}

describe('saved Monte Carlo run comparison',()=>{
 it('classifies identical persisted identity and configuration as direct',()=>{expect(compareMonteCarloRuns(base,{...base,id:'b',config:{...base.config,actual_range:{start:1,end:2}}}).classification).toBe('DIRECTLY COMPARABLE')})
 it.each([
  ['deeptest_run_id',11,'DeepTesting run ID'],['fingerprint','other','Strategy fingerprint'],['node_version',3,'Strategy version'],
  ['instrument','EURUSD','Instrument'],['timeframe','M15','Timeframe'],['date range',{start:2,end:3},'Tested date range'],['method','START_POINT','Simulation method'],
 ] as const)('labels identity mismatch: %s',(_key,_v,label)=>{
   const b:any={...base}; if(_key==='date range')b.config={...base.config,actual_range:_v};else b[_key]=_v
   const review=compareMonteCarloRuns(base,b);expect(review.classification).toBe('INCOMPATIBLE SETUPS');expect(review.differences.join(' ')).toContain(label)
 })
 it.each([
  ['initial_capital',20000,'Starting capital'],['risk_pct',2,'Risk percentage'],['max_active_trades',2,'Maximum active trades'],
  ['simulations',100,'Simulation count'],['seed',99,'Seed'],['block_size',3,'Bootstrap block size'],['stress_multiplier',2,'Execution stress multiplier'],['execution_cost_assumptions',{spread_mode:'fixed',commission_per_lot:2},'Execution-cost assumptions'],['source_data_readiness','BLOCKED','Data readiness'],['known_logic_compliance_failures',1,'Known logic/compliance failures'],
 ] as const)('explains setup difference: %s', (key,value,label)=>{const b={...base,config:{...base.config,[key]:value}};const review=compareMonteCarloRuns(base,b);expect(review.classification).toBe('PARTIALLY COMPARABLE');expect(review.differences.join(' ')).toContain(label)})
 it('identifies partial versus completed results without combining scores',()=>{const b={...base,status:'CANCELLED',result:{partial:true}};const review=compareMonteCarloRuns(base,b);expect(review.classification).toBe('PARTIALLY COMPARABLE');expect(review.differences.join(' ')).toContain('Completion state')})
})
