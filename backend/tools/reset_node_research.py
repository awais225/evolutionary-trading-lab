"""Deliberate offline V9.5 release reset; no implicit startup hook.
Run --plan first. An operator must verify implementation and cleanup publication
receipts before supplying --published-sha. This CLI never pushes or stops services.
"""
import argparse,json,os,sqlite3,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--data-root',required=True);p.add_argument('--backup-parent');p.add_argument('--restore');p.add_argument('--confirm');p.add_argument('--published-sha');p.add_argument('--plan',action='store_true');p.add_argument('--receipt',required=True);a=p.parse_args()
 root=Path(a.data_root).resolve();database=root/'DATABASE/lab_state.db'
 if not database.is_file() or database.open('rb').read(16)!=b'SQLite format 3\0':raise SystemExit('Actual SQLite database required, not an LFS pointer')
 import psutil
 backend=Path(__file__).resolve().parents[1]
 repository=backend.parent
 for proc in psutil.process_iter(['pid','name','cmdline','cwd']):
  if proc.pid==os.getpid():continue
  try:
   cmd=' '.join(proc.info['cmdline'] or []);cwd=Path(proc.info['cwd'] or '/').resolve()
   if ('app.main:app' in cmd or 'uvicorn' in cmd or 'app.orchestrator' in cmd or 'multiprocessing.spawn' in cmd) and (repository==cwd or repository in cwd.parents or str(backend) in cmd):raise SystemExit(f'Owned application/worker PID {proc.pid} remains; stop it before offline reset')
  except (psutil.NoSuchProcess,psutil.AccessDenied):continue
 from app.research_run.release_reset import execute,inventory,restore
 conn=sqlite3.connect(database,timeout=5)
 try:
  if a.plan:result=inventory(conn,root)
  elif a.restore:result=restore(conn,root,a.restore,a.confirm)
  elif a.backup_parent:result=execute(conn,root,a.backup_parent,a.confirm,a.published_sha)
  else:raise SystemExit('--plan, --restore, or --backup-parent required')
  Path(a.receipt).write_text(json.dumps(result,indent=2));print(json.dumps({'state':result.get('state','PLAN_ONLY'),'receipt':a.receipt}))
 finally:conn.close()
if __name__=='__main__':main()
