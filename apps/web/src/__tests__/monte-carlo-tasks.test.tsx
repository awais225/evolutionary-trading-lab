import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { api } from '../api/client'
import { InstrumentProvider } from '../state/instrument'
import { TasksProvider } from '../state/tasks'
import { MonteCarlo } from '../pages/MonteCarlo'
import { TaskProgressBars } from '../components/TaskProgress'

const node=(id:number,more:any={})=>({node_id:id,name:`Node ${id}`,instrument:'XAUUSD',timeframe:'H1',direction:'LONG',version:1,starred:false,deeptest_status:'DONE',deeptest_run_id:id+10,fingerprint:`fp${id}`,trade_count:8,metrics:{net_profit:50,profit_factor:1.5},readiness:'READY',eligible:true,method_availability:{RESHUFFLE:true,BLOCK_BOOTSTRAP:true,START_POINT:true,EXECUTION_STRESS:false},method_reasons:{},...more})
const task=(id:string,nodeId:number,done:number,status='RUNNING',runId=`run-${nodeId}`)=>({id,category:'MONTE_CARLO',instrument:'XAUUSD',label:`Monte Carlo · Node ${nodeId} · RESHUFFLE`,status,total_units:500,completed_units:done,current_unit:`Node ${nodeId}`,current_unit_progress:null,percentage:done/5,summary:`${done}/500`,error:null,acknowledged:false,payload:{node_id:nodeId,monte_carlo_run_id:runId,estimated_remaining_seconds:done?60:null},created_at:1,finished_at:status==='RUNNING'?null:2})
afterEach(()=>{vi.restoreAllMocks();vi.useRealTimers();localStorage.clear()})
const app=()=> <MemoryRouter><InstrumentProvider><TasksProvider><MonteCarlo/></TasksProvider></InstrumentProvider></MemoryRouter>

describe('Monte Carlo user workflow',()=>{
 it('submits independent nodes and reopens persisted partial history',async()=>{
  const taskRows:any[]=[]
  vi.spyOn(api.monteCarlo,'nodes').mockResolvedValue({nodes:[node(1),node(2)]} as any)
  vi.spyOn(api.monteCarlo,'history').mockResolvedValue({runs:[{id:'run-1',node_id:1,deeptest_run_id:11,method:'RESHUFFLE',seed:77,status:'CANCELLED',summary:{simulations_completed:125},robustness:null,warnings:[]},{id:'run-2',node_id:2,deeptest_run_id:12,method:'BLOCK_BOOTSTRAP',seed:77,status:'DONE',summary:{simulations_completed:500},robustness:{score:7},warnings:[]}]} as any)
  const start=vi.spyOn(api.monteCarlo,'start').mockResolvedValue({async:true,runs:[{id:'run-1',task_id:'task-1'},{id:'run-2',task_id:'task-2'}]} as any)
  vi.spyOn(api.monteCarlo,'get').mockImplementation(async(_i,id)=>({id,node_id:id==='run-1'?1:2,deeptest_run_id:id==='run-1'?11:12,method:'RESHUFFLE',seed:77,status:id==='run-1'?'CANCELLED':'DONE',config:{initial_capital:10000},result:{partial:id==='run-1',summary:{simulations_requested:500,simulations_completed:id==='run-1'?125:500,mean_return_pct:1,median_return_pct:1,best_return_pct:2,worst_return_pct:-1,return_percentiles:{'5':-1,'50':1,'95':2},mean_max_drawdown_pct:3,median_max_drawdown_pct:3,drawdown_percentiles:{'95':4,'99':5},survival_rate_pct:99,ruin_rate_pct:1,probability_below_initial_pct:2,probability_negative_return_pct:2},per_simulation:[],representative_curves:[[10000,10100]],equity_percentiles:{'5':[10000,9900],'50':[10000,10100],'95':[10000,10200]},robustness:null,eligibility:null,method_description:'trade-sequence rotation, not candle replay'},error:'cancelled'}) as any)
  vi.spyOn(api.deeptesting,'getRun').mockResolvedValue({metrics:{initial_capital:10000,net_profit:50,max_drawdown_pct:3},equity_curve:[{equity:10000},{equity:10050}]} as any)
  vi.spyOn(api,'instruments').mockResolvedValue([{symbol:'XAUUSD',display_name:'Gold',enabled:true}] as any)
  vi.spyOn(api.tasks,'list').mockImplementation(async()=>({tasks:taskRows,latest:{}} as any))
  render(app()); await screen.findByText('Node 1')
  fireEvent.click(screen.getByLabelText('Select node 1'));fireEvent.click(screen.getByLabelText('Select node 2'))
  fireEvent.click(screen.getByRole('button',{name:'RUN SELECTED NODES'}))
  await waitFor(()=>expect(start).toHaveBeenCalled())
  expect(start.mock.calls[0][1].node_ids).toEqual([1,2])
  await act(async()=>{await new Promise(r=>setTimeout(r,2100))})
  fireEvent.change(screen.getByLabelText('Monte Carlo result'),{target:{value:'run-1'}})
  expect(await screen.findByText(/PARTIAL: 125\/500/)).toBeTruthy()
  expect(screen.getByText(/Original DeepTesting baseline/)).toBeTruthy()
  const chart=screen.getByRole('img',{name:'Original and simulated equity comparison'})
  expect(chart).toBeTruthy()
  const curvePoint=Array.from(document.querySelectorAll('.mc-chart circle')).find(c=>c.querySelector('title')?.textContent?.includes('Sample 1'))
  expect(curvePoint).toBeTruthy();fireEvent.mouseEnter(curvePoint!)
  expect(screen.getByText(/Sample 1 · normalized trade progress/)).toBeTruthy()
  fireEvent.click(screen.getByLabelText('Zoom in chart'));expect(chart.getAttribute('data-zoom')).toBe('1.25')
  fireEvent.click(screen.getByLabelText('Pan chart right'));expect(chart.getAttribute('data-pan')).toBe('25,0')
  fireEvent.click(screen.getByLabelText('Pan chart down'));expect(chart.getAttribute('data-pan')).toBe('25,15')
  fireEvent.click(screen.getByLabelText('Show sample simulation 1'));expect(Array.from(chart.querySelectorAll('title')).map(t=>t.textContent).join(' ')).not.toContain('Sample simulation 1 of')
  fireEvent.click(screen.getByLabelText('Show sample simulation 1'));expect(Array.from(chart.querySelectorAll('title')).map(t=>t.textContent).join(' ')).toContain('Sample simulation 1 of')
  for(const name of ['Original DeepTesting','Median curve','P5 percentile','P95 percentile']){const point=Array.from(chart.querySelectorAll('circle')).find(c=>c.querySelector('title')?.textContent?.includes(name));expect(point,`hover point for ${name}`).toBeTruthy();fireEvent.mouseEnter(point!);expect(document.querySelector('[aria-live="polite"]')?.textContent).toContain(name)}
  const originalVisibility=screen.getByLabelText('Original DeepTesting');fireEvent.click(originalVisibility)
  expect(Array.from(chart.querySelectorAll('title')).map(t=>t.textContent).join(' ')).not.toContain('Persisted original DeepTesting')
  fireEvent.click(originalVisibility)
  const medianVisibility=screen.getByLabelText('Monte Carlo median');fireEvent.click(medianVisibility)
  expect(Array.from(chart.querySelectorAll('title')).map(t=>t.textContent).join(' ')).not.toContain('Monte Carlo median')
  fireEvent.click(medianVisibility)
  const envelopeVisibility=screen.getByLabelText('P5–P95 envelope');fireEvent.click(envelopeVisibility)
  expect(chart.querySelector('path[fill="rgba(72,156,211,.2)"]')).toBeNull()
  fireEvent.click(envelopeVisibility);expect(chart.querySelector('path[fill="rgba(72,156,211,.2)"]')).toBeTruthy()
  fireEvent.click(screen.getByLabelText('Reset chart view'));expect(chart.getAttribute('data-zoom')).toBe('1')
  expect(screen.getByText(/trade-sequence rotation/)).toBeTruthy()
  fireEvent.click(screen.getByLabelText('Compare run run-1'));fireEvent.click(screen.getByLabelText('Compare run run-2'))
  expect(screen.getByText(/INCOMPATIBLE SETUPS/)).toBeTruthy()
 })
 it('filters together, sorts numeric metrics before pagination, persists table preferences and keeps selection',async()=>{
  const rows=Array.from({length:30},(_,i)=>node(i+1,{name:`Node ${i+1}`,timeframe:i%2?'H1':'M15',direction:i%3?'LONG':'SHORT',starred:i%2===0,folder:i%2?'Alpha':'Beta',metrics:{net_profit:i*100,profit_factor:i}}))
  vi.spyOn(api.monteCarlo,'nodes').mockResolvedValue({nodes:rows} as any);vi.spyOn(api.monteCarlo,'history').mockResolvedValue({runs:[]} as any)
  vi.spyOn(api,'instruments').mockResolvedValue([{symbol:'XAUUSD',display_name:'Gold',enabled:true}] as any);vi.spyOn(api.tasks,'list').mockResolvedValue({tasks:[],latest:{}} as any)
  render(app());await screen.findByText('Node 1')
  fireEvent.change(screen.getByLabelText('Page size'),{target:{value:'10'}});expect(screen.getByText(/Page 1\/3/)).toBeTruthy()
  fireEvent.click(screen.getByRole('button',{name:'Net P\/L'}));expect(screen.getAllByRole('row')[1].textContent).toContain('Node 30')
  fireEvent.click(screen.getByLabelText('Select node 30'));fireEvent.click(screen.getByRole('button',{name:'Next'}));expect(screen.getByText(/Page 2\/3/)).toBeTruthy();fireEvent.click(screen.getByRole('button',{name:'Previous'}));expect(screen.getByLabelText('Select node 30')).toBeChecked()
  expect(localStorage.getItem('hero1.mc.table.v1')).toContain('"pageSize":"10"')
  fireEvent.change(screen.getByLabelText('Page size'),{target:{value:'15'}});expect(screen.getByText(/Page 1\/2/)).toBeTruthy()
  fireEvent.change(screen.getByLabelText('Page size'),{target:{value:'25'}});expect(screen.getByText(/Page 1\/2/)).toBeTruthy()
  fireEvent.change(screen.getByLabelText('Page size'),{target:{value:'ALL'}});expect(screen.getByText(/Page 1\/1/)).toBeTruthy()
  fireEvent.change(screen.getByLabelText('Page size'),{target:{value:'10'}})
  fireEvent.change(screen.getByLabelText('Direction filter'),{target:{value:'SHORT'}});fireEvent.change(screen.getByLabelText('Starred filter'),{target:{value:'starred'}})
  expect(screen.getByText(/matching/).textContent).toContain('5 matching')
  expect(document.querySelector('.mc-node-panel .section-title span')?.textContent).toContain('1 selected')
  fireEvent.click(screen.getByRole('button',{name:'Select all filtered eligible'}))
  expect(document.querySelector('.mc-node-panel .section-title span')?.textContent).toContain('6 selected')
 })
 it('cancels the selected UI task by its own ID without targeting sibling tasks',async()=>{
  vi.spyOn(api.monteCarlo,'nodes').mockResolvedValue({nodes:[node(1)]} as any);vi.spyOn(api.monteCarlo,'history').mockResolvedValue({runs:[]} as any)
  vi.spyOn(api,'instruments').mockResolvedValue([{symbol:'XAUUSD',display_name:'Gold',enabled:true}] as any)
  const rows=[task('cancel-me',1,5),task('keep-running',2,2)]
  vi.spyOn(api.tasks,'list').mockResolvedValue({tasks:rows,latest:{}} as any)
  const cancel=vi.spyOn(api.monteCarlo,'cancel').mockResolvedValue({cancel_requested:true} as any)
  render(app());await screen.findByText(/BACKGROUND TASKS/)
  const buttons=screen.getAllByRole('button',{name:'Cancel'});expect(buttons).toHaveLength(2)
  fireEvent.click(buttons[0]);await waitFor(()=>expect(cancel).toHaveBeenCalledTimes(1))
  expect(cancel.mock.calls[0][1]).toBe('cancel-me')
 })
 it('keeps running notices visible and independently auto-acknowledges completed/cancelled while holding failures',async()=>{
  vi.useFakeTimers();const now=Date.now()/1000
  const completed={...task('done-task',1,10,'COMPLETED'),finished_at:now},cancelled={...task('cancel-task',2,3,'CANCELLED'),finished_at:now},failed={...task('failed-task',3,4,'FAILED'),error:'Interrupted by application restart',finished_at:now}
  const rows:any[]=[task('running-task',4,2),completed,cancelled,failed]
  const ack=vi.spyOn(api.tasks,'acknowledgeOne').mockResolvedValue({acknowledged:true,task_id:'x'} as any);vi.spyOn(api.tasks,'list').mockResolvedValue({tasks:rows,latest:{}} as any)
  render(<TasksProvider><TaskProgressBars/></TasksProvider>);await act(async()=>{await Promise.resolve();await Promise.resolve()})
  expect(document.querySelector('[data-category="MONTE_CARLO"].task-running')).toBeTruthy()
  await act(async()=>{vi.advanceTimersByTime(5000);await Promise.resolve();await Promise.resolve()})
  expect(ack).toHaveBeenCalledWith('done-task');expect(ack).toHaveBeenCalledWith('cancel-task');expect(ack).not.toHaveBeenCalledWith('failed-task')
 })
})
