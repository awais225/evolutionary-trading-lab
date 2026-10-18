"""Sequential, isolated pytest groups with memory telemetry and resumable receipts.
No exclusions, assertion changes, skip injection, or success on interrupted runs.
Call with a disposable source copy, an external DATA fixture and a JSON list of
pytest argument groups. Never point this harness at active application DATA.
"""
import argparse,hashlib,json,os,signal,subprocess,sys,time,xml.etree.ElementTree as ET
from pathlib import Path
import psutil,uuid

def digest(root):
    folders=['backend/app','backend/tools','frontend/src','frontend/tests','tests','CONFIG']
    h=hashlib.sha256()
    for folder in folders:
        for p in sorted((root/folder).rglob('*')):
            if not p.is_file() or '__pycache__' in p.parts:continue
            if p.suffix not in ('.py','.js','.jsx','.mjs','.css','.json','.yaml','.yml'):continue
            h.update(p.relative_to(root).as_posix().encode()+b'\0'+p.read_bytes())
    h.update((root/'BUILD_FINGERPRINTS.json').read_bytes())
    return h.hexdigest()

def events(cg):
    return {k:int(v) for k,v in (s.split() for s in (cg/'memory.events').read_text().splitlines())}

def owned_survivors(token,seen):
    """PID+creation-time and unique environment token; never kill by name/port."""
    survivors={}
    for pid,created in seen.items():
        try:
            proc=psutil.Process(pid)
            if proc.create_time()==created and proc.is_running() and proc.status()!=psutil.STATUS_ZOMBIE:survivors[pid]=proc
        except (psutil.NoSuchProcess,psutil.AccessDenied):pass
    for proc in psutil.process_iter():
        try:
            if proc.uids().real==os.getuid() and proc.status()!=psutil.STATUS_ZOMBIE and proc.environ().get('V95_ACCEPTANCE_OWNER_TOKEN')==token:survivors[proc.pid]=proc
        except (psutil.NoSuchProcess,psutil.AccessDenied):pass
    return list(survivors.values())

def stop_owned(procs):
    for proc in procs:
        try:proc.terminate()
        except psutil.NoSuchProcess:pass
    _,alive=psutil.wait_procs(procs,timeout=2)
    for proc in alive:
        try:proc.kill()
        except psutil.NoSuchProcess:pass
    psutil.wait_procs(alive,timeout=2)

def main():
    a=argparse.ArgumentParser(description=__doc__)
    for key in ['source','candidate','data','active-data','groups','output']:a.add_argument('--'+key,required=True)
    a.add_argument('--timeout',type=int,default=240);a.add_argument('--rss-limit-mb',type=int,default=1280);a.add_argument('--resume',action='store_true');args=a.parse_args()
    source=Path(args.source).resolve();candidate=Path(args.candidate).resolve();data=Path(args.data).resolve();active=Path(args.active_data).resolve();output=Path(args.output).resolve();output.mkdir(parents=True,exist_ok=True)
    if data==active or source==candidate or data==candidate or candidate in data.parents:raise SystemExit('Disposable source and distinct external DATA required')
    if os.path.samefile(data/'DATABASE/lab_state.db',active/'DATABASE/lab_state.db'):raise SystemExit('Test DB aliases active DB')
    groups=json.loads(Path(args.groups).read_text());flat=[s for group in groups for s in group]
    if len(set(flat))!=len(flat):raise SystemExit('Duplicate test selections; refuse double counting')
    frozen=digest(candidate)
    if digest(source)!=frozen:raise SystemExit('Source copy differs from candidate')
    originals={f:(source/f).read_bytes() for f in ['CONFIG/lab_config.yaml','CONFIG/settings.json']}
    cg=Path('/sys/fs/cgroup/user');results=[]
    for i,group in enumerate(groups,1):
        receipt=output/f'{i:03}.json'
        if args.resume and receipt.exists():
            old=json.loads(receipt.read_text())
            if old.get('completed') and old.get('source_sha256')==frozen and old.get('selection')==group:results.append(old);continue
        for f,raw in originals.items():(source/f).write_bytes(raw)
        log=output/f'{i:03}.log';junit=output/f'{i:03}.xml';before=events(cg);start=time.monotonic();samples=[];peak=0;blocked=None;token=uuid.uuid4().hex;seen={}
        cmd=[sys.executable,'-m','pytest','-q','-ra','--tb=short','-o','faulthandler_timeout=60','--junitxml='+str(junit),*group]
        env={**os.environ,'EVOLUTIONARY_LAB_DATA_ROOT':str(data),'PYTHONPATH':str(source/'backend'),'LAB_AUTOSTART':'0','V95_ACCEPTANCE_OWNER_TOKEN':token,'PATH':'/home/user/bin:'+os.environ['PATH']}
        with log.open('w') as stream:
            p=subprocess.Popen(cmd,cwd=source,env=env,stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
            while p.poll() is None:
                try:
                    proc=psutil.Process(p.pid);owned=[proc]+proc.children(recursive=True);rss=sum(x.memory_info().rss for x in owned if x.is_running());seen.update({x.pid:x.create_time() for x in owned});peak=max(peak,rss);samples.append((round(time.monotonic()-start,3),rss,int((cg/'memory.current').read_text())))
                    if rss>args.rss_limit_mb*1024**2:blocked='MEMORY_GUARD_BLOCKED';break
                    if time.monotonic()-start>args.timeout:blocked='TIMEOUT_BLOCKED';break
                except (psutil.NoSuchProcess,ProcessLookupError):pass
                time.sleep(.1)
            if blocked:
                os.killpg(p.pid,signal.SIGTERM)
                try:p.wait(timeout=3)
                except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait()
            else:p.wait()
        survivors=owned_survivors(token,seen);survivor_ids=[x.pid for x in survivors]
        if survivors:
            blocked=blocked or 'OWNED_WORKERS_REMAINED';stop_owned(survivors)
        remaining=[x.pid for x in owned_survivors(token,seen)]
        (output/f'{i:03}.csv').write_text('seconds,owned_rss_bytes,cgroup_bytes\n'+'\n'.join(','.join(map(str,r)) for r in samples)+'\n')
        counts={'passed':0,'failed':0,'errors':0,'skipped':0,'deselected':0};ids=[]
        if junit.exists() and blocked is None:
            for case in ET.parse(junit).iter('testcase'):
                ids.append(case.get('classname','')+'::'+case.get('name',''))
                key='errors' if case.find('error') is not None else 'failed' if case.find('failure') is not None else 'skipped' if case.find('skipped') is not None else 'passed';counts[key]+=1
        after=events(cg);completed=p.returncode==0 and blocked is None and after.get('oom_kill',0)==before.get('oom_kill',0) and digest(candidate)==frozen
        result={'batch':i,'selection':group,'source_sha256':frozen,'command':cmd,'cwd':str(source),'data_root':str(data),'exit':p.returncode,'completed':completed,'blocked':blocked,'counts':counts if completed or (blocked is None and p.returncode==1) else None,'nodeids':ids if completed else [],'owner_token':token,'owned_pids_observed':sorted(seen),'owned_survivors_before_cleanup':survivor_ids,'owned_survivors_after_cleanup':remaining,'peak_owned_rss_bytes':peak,'seconds':round(time.monotonic()-start,3),'oom_before':before,'oom_after':after,'log':str(log),'junit':str(junit)}
        receipt.write_text(json.dumps(result,indent=2));results.append(result)
        totals={k:sum((r.get('counts') or {}).get(k,0) for r in results if r['completed']) for k in counts}
        (output/'summary.json').write_text(json.dumps({'source_sha256':frozen,'groups_expected':len(groups),'groups_completed':sum(r['completed'] for r in results),'counts_completed_only':totals,'results':results},indent=2));print(json.dumps({k:result[k] for k in ['batch','exit','completed','blocked','counts','peak_owned_rss_bytes','seconds']}),flush=True)
        if not completed:raise SystemExit('BLOCKED; no acceptance pass. Resolve the recorded batch and --resume.')
    print('ALL GROUPS COMPLETED',flush=True)
if __name__=='__main__':main()
