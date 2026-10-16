from pathlib import Path
import subprocess,shutil,time,json,os,sqlite3
src=Path('/home/user/v93-app');r=Path('/home/user/.cache/v93-final-gate');tmp=Path('/home/user/.cache/v93-test-tmp');tmp.mkdir(exist_ok=True)
if not r.exists():
 shutil.copytree(src,r,ignore=shutil.ignore_patterns('DATA','.git','.venv','node_modules','__pycache__','.pytest_cache'))
 (r/'.git').symlink_to(src/'.git',target_is_directory=True)
 (r/'.venv').symlink_to(src/'.venv',target_is_directory=True)
 (r/'frontend/node_modules').symlink_to(src/'frontend/node_modules',target_is_directory=True)
 shutil.copytree(src/'DATA',r/'DATA',ignore=shutil.ignore_patterns('HISTORICAL','USER_HISTORY_ICMARKETS_CANONICAL','*__USER_HISTORY_ICMARKETS_CANONICAL.json','*.db','*.db-wal','*.db-shm','logs','BACKUPS'))
else:
 for directory in ('backend','frontend/src','frontend/tests','tests'):
  shutil.copytree(src/directory,r/directory,dirs_exist_ok=True,ignore=shutil.ignore_patterns('node_modules','__pycache__','dist'))
 shutil.copy2(src/'BUILD_FINGERPRINTS.json',r/'BUILD_FINGERPRINTS.json')
base=Path('/home/user/.cache/v93-gate-base.db')
shutil.copy2('/home/user/.cache/v93/canonical-original.db',base)
(r/'DATA/DATABASE').mkdir(exist_ok=True)
env={**os.environ,'EVOLUTIONARY_LAB_DATA_ROOT':str(r/'DATA'),'TMPDIR':str(tmp),'SQLITE_TMPDIR':str(tmp),'PATH':'/home/user/bin:'+os.environ['PATH']}
files=sorted(str(f.relative_to(r)) for f in (r/'tests').glob('test*.py'));results=[]
configs={str(p.relative_to(r)):p.read_bytes() for p in (r/'CONFIG').glob('*') if p.is_file()}
for i in range(0,len(files),7):
 n=i//7+1;db=r/'DATA/DATABASE/lab_state.db'
 for suffix in ('','-wal','-shm'):
  p=Path(str(db)+suffix)
  if p.exists():p.unlink()
 shutil.copy2(base,db)
 for p,content in configs.items():(r/p).write_bytes(content)
 if n in (3,4):
  with open(f'/home/user/V9_3_FINAL_GATE_{n}_RESTORE.log','w') as log:
   restored=subprocess.run([str(r/'.venv/bin/python'),'-c',"import sys;sys.path.insert(0,'backend');from app.data.restoration import get_restoration_engine;get_restoration_engine().restore_all(emit_logs=False)"],cwd=r,env=env,stdout=log,stderr=subprocess.STDOUT)
   if restored.returncode:raise RuntimeError('real history restoration prerequisite failed')
 log=Path(f'/home/user/V9_3_FINAL_GATE_{n}.log');t=time.monotonic()
 with log.open('w') as out:p=subprocess.run([str(r/'.venv/bin/python'),'-m','pytest','-q',*files[i:i+7]],cwd=r,env=env,stdout=out,stderr=subprocess.STDOUT)
 result={'batch':n,'files':files[i:i+7],'exit':p.returncode,'seconds':time.monotonic()-t,'tail':log.read_text().splitlines()[-1]};results.append(result);Path('/home/user/V9_3_FINAL_GATE_RESULTS.json').write_text(json.dumps(results,indent=2));print(json.dumps(result),flush=True)
print('ALL BATCHES FINISHED',flush=True)
raise SystemExit(int(any(x['exit'] for x in results)))
