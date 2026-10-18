"""Bounded CPU tasks; only explicitly owned processes may be stopped."""
import multiprocessing as mp,os,time,threading
_ACTIVE={};_LOCK=threading.RLock()
class Cancelled(RuntimeError):pass
def _after_fork():
 global _ACTIVE,_LOCK
 _ACTIVE={};_LOCK=threading.RLock()
if hasattr(os,'register_at_fork'):os.register_at_fork(after_in_child=_after_fork)
def active_tasks():
 with _LOCK:return [dict(info) for child,info in _ACTIVE.values() if info['parent_pid']==os.getpid() and child.is_alive()]
def _entry(pipe,fn,args):
 os.environ['LAB_NO_FEATURE_PERSIST']='1'
 try:pipe.send((True,fn(*args)))
 except BaseException as exc:pipe.send((False,f'{type(exc).__name__}: {exc}'))
 finally:pipe.close()
def call(fn,args,stop,timeout=240):
 if stop.is_set():raise Cancelled('Cancelled before scheduling')
 ctx=mp.get_context('spawn' if os.name=='nt' else 'fork');reader,writer=ctx.Pipe(duplex=False);child=ctx.Process(target=_entry,args=(writer,fn,args),daemon=True)
 child.start();writer.close();deadline=time.monotonic()+timeout
 with _LOCK:_ACTIVE[child.pid]=(child,dict(pid=child.pid,parent_pid=os.getpid(),task=fn.__name__,started_at=time.time(),timeout_seconds=timeout))
 try:
  while True:
   if stop.is_set():raise Cancelled('Operator cancellation; in-flight computation discarded')
   if time.monotonic()>=deadline:raise TimeoutError(f'{fn.__name__} exceeded {timeout}s; isolated worker terminated')
   if reader.poll(.05):
    ok,result=reader.recv()
    if not ok:raise RuntimeError(result)
    return result
   if not child.is_alive():raise RuntimeError(f'{fn.__name__} worker exited {child.exitcode} without result')
 finally:
  reader.close()
  if child.is_alive():child.terminate()
  child.join(timeout=1)
  if child.is_alive():child.kill();child.join(timeout=1)
  if child.is_alive():raise RuntimeError(f'Owned task PID {child.pid} still alive after bounded terminate/kill; STOPPING')
  with _LOCK:_ACTIVE.pop(child.pid,None)
