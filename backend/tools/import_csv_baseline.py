"""Explicit, offline baseline reconstruction; never connects a trading terminal.
Example: python backend/tools/import_csv_baseline.py --directory DATA/HISTORICAL/baseline --source-timezone ICMARKETS_NY_CLOSE --confirm-source-timezone
Requires the original Git LFS CSV objects; a pointer file is not candle data.
"""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.data.history import import_baselines
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--directory',type=Path,required=True)
p.add_argument('--source-timezone',required=True)
p.add_argument('--confirm-source-timezone',action='store_true')
a=p.parse_args()
m=import_baselines(a.directory,a.source_timezone,confirmed=a.confirm_source_timezone)
print(json.dumps({'symbol':m['symbol'],'status':m.get('status'),'datasets':[{'timeframe':r['timeframe'],'bars':r['persistent_readback_bars']} for r in m['imports']]},indent=2))
