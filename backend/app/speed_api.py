"""Bounded diagnostics APIs. No database writes or user/market payload capture."""
import time
import re
from fastapi import APIRouter, Body, HTTPException, Query
from . import speed

router=APIRouter(prefix='/api/speed-log',tags=['speed diagnostics'])

@router.get('/snapshot')
def snapshot():
    speed.PROFILER.start_sampler()
    return speed.PROFILER.snapshot()

@router.get('/events')
def events(after:int=Query(0,ge=0),limit:int=Query(200,ge=1,le=500)):
    speed.PROFILER.start_sampler()
    speed.PROFILER.runtime()  # Slow version/commit discovery must not hold the hot instrumentation lock.
    # Same lock protects aggregate snapshot and its associated event cursor/window.
    with speed.PROFILER.lock:
        result=speed.PROFILER.snapshot(events=False)
        rows=[e for e in speed.PROFILER.events if e['seq']>after][:limit]
        result['events']=list(rows)
        result['next_seq']=rows[-1]['seq'] if rows else max(after,speed.PROFILER.seq)
    return result

@router.post('/recording')
def recording(body:dict=Body(...)):
    try:
        speed.PROFILER.configure(body.get('profile','light'),capacity=int(body.get('capacity',2000)),
            sample_every=int(body.get('sample_every',20)),interval=float(body.get('sampling_interval_seconds',1)),seconds=int(body.get('seconds',60)))
    except (ValueError,TypeError,OverflowError):raise HTTPException(422,"Invalid diagnostics configuration: use off/light/detailed and the documented numeric bounds")
    speed.PROFILER.start_sampler()
    return speed.PROFILER.snapshot(events=False)

@router.post('/frontend')
def frontend(body:dict=Body(...)):
    # Fixed event type, numeric allowlist and small body schema; no URL, text, tokens or arbitrary labels.
    fields={k:v for k,v in body.items() if k in ('request_ms','render_ms','rows','bytes')}
    if len(body)>8:raise HTTPException(422,'Too many telemetry fields')
    speed.event('frontend','diagnostic_ui_sample',fields=fields)
    for field,name in [('request_ms','frontend.diagnostic_request'),('render_ms','frontend.render_to_commit')]:
        value=fields.get(field)
        if isinstance(value,(int,float)) and 0<=value<=600000:speed.duration(name,value/1000,pid=-1)
    return {'ok':True}

class SpeedMiddleware:
    """Pure ASGI: measures through final streaming response body without buffering it."""
    def __init__(self,app):self.app=app
    async def __call__(self,scope,receive,send):
        if scope['type']!='http' or scope.get('path','').startswith('/api/speed-log'):
            return await self.app(scope,receive,send)
        size=0;status=None
        async def measured_send(message):
            nonlocal size,status
            if message['type']=='http.response.start':status=message['status']
            if message['type']=='http.response.body':size+=len(message.get('body',b''))
            await send(message)
        with speed.span('api.request_through_stream'):
            try:return await self.app(scope,receive,measured_send)
            except (ConnectionResetError,BrokenPipeError) as exc:
                match=re.fullmatch(r'/api/deeptest/run/(dt-[A-Za-z0-9-]+)(?:/.*)?',scope.get('path',''))
                speed.event('transport','client_disconnect','WARN',run_id=match.group(1) if match else None,
                            fields={'error_type':type(exc).__name__,'operation':'asgi_stream_send'})
                raise  # transport failure is not a worker cancellation/status mutation
            finally:speed.event('api','response',fields={'bytes':size,**({'status_code':status} if status is not None else {})})
