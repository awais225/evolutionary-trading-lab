"""Focused proof for the archived watchdog's obsolete cross-run DB injection.
Run separately with pytest; no broker, Lab constructor, generation or orders.
The watchdog may recover only the explicitly selected run. Repeat-timeout
BACKTEST_ERROR (not failed fitness) is an older, separately retained contract.
"""
import time
from types import SimpleNamespace
import pytest

@pytest.mark.parametrize('active',['A','B'])
def test_watchdog_recovers_only_the_explicit_active_run(tmp_path,monkeypatch,active):
    from app.db.database import Database
    from app.orchestrator.lab import Lab
    import app.orchestrator.lab as module
    db=Database(str(tmp_path/'scope.db'))
    try:
        ids={}
        for run in ('A','B'):
            for status in ('BACKTESTING','VALIDATING'):
                sid=db.insert_strategy({'hash':run+status,'run_id':run,'symbol':'XAUUSD','timeframe':'M1','genome':{},'status':status,'data_source':'USER_RESEARCH'})
                # update_strategy intentionally stamps now; seed an actual stale timestamp.
                db.x('UPDATE strategies SET updated_at=? WHERE id=?',(time.time()-300,sid))
                ids[run,status]=sid
        lab=Lab.__new__(Lab);lab.db=db;lab.evo=SimpleNamespace(active_run_id=active)
        lab._last_recovery_check_ts=0;lab._recovered_strategy_ids=set();lab._strategy_recovery_attempts={};lab.counters={}
        monkeypatch.setattr(module.activity,'info',lambda *a,**k:None)
        lab._recover_stuck_strategies(force=True)
        expected={ids[active,'BACKTESTING'],ids[active,'VALIDATING']}
        assert lab._recovered_strategy_ids==expected
        for (run,status),sid in ids.items():
            actual=db.get_strategy(sid)['status']
            assert actual==({'BACKTESTING':'BORN','VALIDATING':'SURVIVED'}[status] if run==active else status)
        attempts=dict(lab._strategy_recovery_attempts)
        lab._recover_stuck_strategies(force=True)
        assert lab._recovered_strategy_ids==expected and lab._strategy_recovery_attempts==attempts
    finally:db.close()
