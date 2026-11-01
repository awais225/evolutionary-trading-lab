"""Deterministic synthetic persisted rows. No strategy generation/backtest run."""
import json


def seed(db, nodes=64, scenarios_per_node=4, trades_per_scenario=128, population=None):
    from app.deeptest import store as S
    S.ensure_schema(db)
    total=population or nodes
    genome=json.dumps(dict(symbol='EURUSD',timeframe='M1',direction='long',
                           entry_long=dict(type='compare',left='close',cmp='>',right=0),
                           exit=dict(atr_spec='atr:14',sl_atr_mult=0.,tp_atr_mult=0.,max_hold_bars=1)))
    db.xm('INSERT INTO strategies(id,hash,genome,symbol,timeframe,run_id,status,data_source,generation,research_node_num,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
          [(1000+i,'SYNTHETIC-READ-'+str(i),genome,'EURUSD','M1','FIXTURE','QUALIFIED','USER_RESEARCH',i%7,i+1,1783339200.,1783339200.) for i in range(total)])
    for n in range(nodes):
        for s in range(scenarios_per_node):
            rid=f'fixture-{n:05d}-{s:02d}'
            S.insert_scenario(db,dict(run_id=rid,node_id=1000+n,status='PARTIAL' if s%2 else 'COMPLETED',
                created_at=100.+s,coverage_status='PARTIAL' if s%2 else 'COMPLETE',
                settings={'total_capital':200000,'account_currency':'EUR'},
                metrics={'initial_capital':10000,'bars_processed':10000},
                analytics={'summary':{'net_profit':999999,'trades':trades_per_scenario,'max_drawdown_pct':3.99}},
                bars_used=10000,symbol='EURUSD',timeframe='M1',duration_key='1m'))
            db.xm('INSERT INTO deeptest_trades(run_id,trade_no,net_pnl,entry_ts,exit_ts,session,dow) VALUES (?,?,?,?,?,?,?)',
                [(rid,i+1,(-1.25,2.5,-.75)[i%3],1783339200.+i*60,1783339230.+i*60,'london',0) for i in range(trades_per_scenario)])
    return [f'fixture-{n:05d}-{s:02d}' for n in range(nodes) for s in range(scenarios_per_node)]
