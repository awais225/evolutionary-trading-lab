import {describe,it,expect} from 'vitest'
import {monteCarloTableTxt} from '../lib/monteCarloTableExport'

describe('filtered node TXT export',()=>{
 it('exports the full filtered/sorted view with instrument, filters, sort, identity, metrics and N/A values',()=>{
  const txt=monteCarloTableTxt([{node_id:4,name:'A | B',instrument:'XAUUSD',timeframe:'H1',direction:'LONG',version:2,fingerprint:'fp',deeptest_status:'DONE',readiness:'READY',trade_count:7,metrics:{net_profit:12.5,profit_factor:null,win_rate:.6},date_range:{start:1,end:2},starred:true},{node_id:8,name:'No metrics',instrument:'XAUUSD'}],'XAUUSD',{search:'A',timeframe:'H1',direction:'LONG',origin:'CUSTOM',testing:'DONE',folder:'',starred:'starred'},{key:'net_profit',dir:'desc'})
  expect(txt).toContain('Instrument scope: XAUUSD');expect(txt).toContain('"starred":"starred"');expect(txt).toContain('Sort: net_profit desc');expect(txt).toContain('A \\| B');expect(txt).toContain('12.5');expect(txt).toContain('N/A');expect(txt.indexOf('A \\| B')).toBeLessThan(txt.indexOf('No metrics'))
 })
})
