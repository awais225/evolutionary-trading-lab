from pathlib import Path
import json,os,subprocess,shutil,time
r=Path('/home/user/.cache/v93-final-gate');src=Path('/home/user/v93-app')
results=json.loads(Path('/home/user/V9_3_FINAL_GATE_RESULTS.json').read_text());assert len(results)==11
assert [(x['batch'],x['exit']) for x in results if x['exit']] == [(5,1)]
shutil.copy2(src/'BUILD_FINGERPRINTS.json',r/'BUILD_FINGERPRINTS.json')
db=r/'DATA/DATABASE/lab_state.db'
for suffix in ('','-wal','-shm'):
 p=Path(str(db)+suffix)
 if p.exists():p.unlink()
shutil.copy2('/home/user/.cache/v93-gate-base.db',db)
env={**os.environ,'EVOLUTIONARY_LAB_DATA_ROOT':str(r/'DATA'),'TMPDIR':'/home/user/.cache/v93-test-tmp','SQLITE_TMPDIR':'/home/user/.cache/v93-test-tmp','PATH':'/home/user/bin:'+os.environ['PATH']}
result=results[4];t=time.monotonic()
with open('/home/user/V9_3_FINAL_GATE_5_INDEX_SYNC_RETRY.log','w') as log:
 p=subprocess.run([str(r/'.venv/bin/python'),'-m','pytest','-q',*result['files']],cwd=r,env=env,stdout=log,stderr=subprocess.STDOUT)
result={**result,'exit':p.returncode,'seconds':time.monotonic()-t,'tail':Path('/home/user/V9_3_FINAL_GATE_5_INDEX_SYNC_RETRY.log').read_text().splitlines()[-1],
 'retry_reason':'Synchronize release index after isolated source snapshot captured preceding backend fingerprint; unchanged final functional source. Full seven-file batch rerun.',
 'preceding_attempt_exit':results[4]['exit'],'preceding_attempt_summary':results[4]['tail']}
results[4]=result;Path('/home/user/V9_3_FINAL_GATE_RESULTS.json').write_text(json.dumps(results,indent=2)+'\n');print(json.dumps(result),flush=True)
raise SystemExit(int(any(x['exit'] for x in results)))
