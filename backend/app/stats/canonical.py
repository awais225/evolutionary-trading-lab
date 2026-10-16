"""V9.3 historical-statistics contract. Never edits generation or live metrics."""
from typing import TypedDict, Optional
from ..research.registry import numeric

class CanonicalStatistics(TypedDict):
    schema_version: int
    node_id: int
    source: str
    status: str
    metrics: dict
    provenance: dict


def attach(row, source='effective'):
    if source not in ('effective','generation','deeptest'):
        raise ValueError('metric_source must be effective, generation or deeptest')
    dt=row.get('deeptest') or {};gen=row.get('generation_metrics')
    kind=row.get('metrics_source') or 'none'
    if source=='generation':
        kind='generation' if gen else 'none';row['metrics']=dict(gen or {})
        row['metrics_source']=kind;row['metrics_source_label']='Generation record' if gen else 'UNAVAILABLE'
    elif source=='deeptest' and not kind.startswith('deeptest'):
        row['metrics']={};kind='none';row['metrics_source']='none';row['metrics_source_label']='NOT DEEPTESTED'
    metrics={k:numeric(v) for k,v in (row.get('metrics') or {}).items()}
    warnings=[]
    wr=metrics.get('win_rate')
    if wr is not None and not 0<=wr<=1:
        metrics['win_rate']=None;warnings.append('Invalid win-rate fraction excluded from ranking; original generation record retained')
    row['metrics']=metrics
    is_deep=kind.startswith('deeptest');coverage=dt.get('coverage_status') if is_deep else None
    status=('PARTIAL' if is_deep and (dt.get('preferred_status')=='PARTIAL' or coverage not in ('COMPLETE',None)) else 'COMPLETED' if is_deep else 'GENERATION' if kind=='generation' else 'UNAVAILABLE')
    research=row.get('backtest_coverage') or {};origin=research.get('research_backtest') or {};generation_origin=(row.get('generation_record') or {}).get('provenance') or {}
    row['historical_statistics']={'schema_version':2,'node_id':row['node_id'],'source':kind,'status':status,'metrics':metrics,'warnings':warnings,
        'provenance':{'symbol':row.get('symbol'),'timeframe':row.get('timeframe'),'research_run_id':row.get('run_id'),
        'scenario_id':dt.get('preferred_run_id') if is_deep else None,'dataset_id':dt.get('dataset_id') if is_deep else origin.get('dataset_id'),
        'dataset_fingerprint':dt.get('dataset_fingerprint') if is_deep else None,'start_ts':dt.get('actual_start') if is_deep else None,
        'end_ts':dt.get('actual_end') if is_deep else None,'trade_count':metrics.get('trades'),'coverage_status':coverage,
        'finished_at':dt.get('finished_at') if is_deep else generation_origin.get('created_at'),'generation_record':None if is_deep else row.get('generation_record'),'generation_coverage_note':None if is_deep else 'Exact timestamp span unavailable unless present in the original record; current imported/fetched coverage is not substituted','freshness':'immutable historical snapshot' if is_deep else 'stored generation record',
        'calculation_version':'deeptest-realised-initial-capital-v1' if is_deep else 'original-generation-engine',
        'units':'return/drawdown/win_rate are fractions; UI multiplies by100'}}
    return row


def rank(rows,descending=True):
    def key(row):
        v=numeric((row.get('metrics') or {}).get('win_rate'))
        partial=(row.get('historical_statistics') or {}).get('status')=='PARTIAL'
        return (v is None,partial,(-v if descending else v) if v is not None else 0,int(row['node_id']))
    return sorted(rows,key=key)
