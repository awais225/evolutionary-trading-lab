export function monteCarloTableTxt(rows:any[],instrument:string,prefs:any,sort:{key:string;dir:string}):string {
 const esc=(v:any)=>String(v??'N/A').replace(/[\r\n\t]/g,' ').replace(/\|/g,'\\|')
 const fields=['node_id','name','instrument','timeframe','direction','version','fingerprint','deeptest_status','readiness','trade_count','net_profit','profit_factor','win_rate','max_drawdown_amount','max_drawdown_pct','range_start','range_end','starred']
 const lines=[`HERO1 Monte Carlo filtered node table`,`Instrument scope: ${instrument}`,`Filters: ${JSON.stringify({search:prefs.search,timeframe:prefs.timeframe,direction:prefs.direction,category:prefs.origin,testing:prefs.testing,folder:prefs.folder,starred:prefs.starred})}`,`Sort: ${sort.key} ${sort.dir}`,fields.join(' | '),...rows.map(r=>[r.node_id,r.name,r.instrument,r.timeframe,r.direction,r.version,r.fingerprint,r.deeptest_status,r.readiness,r.trade_count,r.metrics?.net_profit,r.metrics?.profit_factor,r.metrics?.win_rate,r.metrics?.max_drawdown_amount,r.metrics?.max_drawdown_pct,r.date_range?.start,r.date_range?.end,r.starred].map(esc).join(' | '))]
 return lines.join('\n')
}
