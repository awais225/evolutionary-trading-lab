"""Explicit offline node-only reset/recovery. Never invoked by startup."""
from pathlib import Path
import hashlib,json,os,sqlite3,time,uuid,zipfile
NODE_TABLES={'strategies','backtests','validations','matrices','paper_trades','hypotheses','research_memory','research_shortlist','research_runs','generation_stats','mt5_backtests','live_test_configs','live_test_trades','mt5_demo_configs','mt5_demo_trades','strategy_pipeline_states','mt5_historical_runs','live_test_events','deeptest_scenarios','deeptest_trades','deeptest_prefs','deeptest_node_configs','deeptest_confirmations','deeptest_claims','deeptest_batches','generation_window_results','filter_scenario_assignments'}
PRESERVED_TABLES={'datasets','master_datasets','data_conflicts','integrity_reports','feature_meta','calibration','sqlite_sequence','node_registry_revision'}
NODE_PATHS=('manifest.json','nodes','genomes','strategies','research/nodes','research/strategies','research/backtests','research/validations','research/generations','RESEARCH/strategies','RESEARCH/backtests','RESEARCH/validations','RESEARCH/generations','RESEARCH/hypotheses','RESEARCH/experiments','RESEARCH/paper_trading','results','backtests','validations','generations','checkpoints','exports','CACHE/NODES','CACHE/GENOMES','CACHE/BACKTESTS','CACHE/RESULTS','CACHE/CHECKPOINTS','metadata/active_run.json','metadata/pipeline_state.json','manifests/nodes.json','manifests/genomes.json','manifests/backtests.json','manifests/research.json')
KEEP_META={'kill_switch','migrated_at','total_node_target','generation_window_settings'}
NODE_META_PREFIX=('inventory','node_','generation_','research_','pipeline_','active_run','current_run','evolution_seed','v92_registry','v95_research_epoch','v95_restoration_barrier')
def digest(path):
 h=hashlib.sha256()
 with Path(path).open('rb') as f:
  for block in iter(lambda:f.read(1<<20),b''):h.update(block)
 return h.hexdigest()
def market_manifest(root):
 root=Path(root);out={}
 for p in root.rglob('*'):
  if not p.is_file():continue
  rel=p.relative_to(root).as_posix()
  if rel.startswith(('HISTORICAL/','MT5/','XAUUSD/','EURUSD/','BTCUSD/','TEST_DATA/','features/','data_cache/','CACHE/FEATURES/')) or rel in ('manifests/datasets.json','manifests/features.json','metadata_migration_v1.json'):out[rel]={'sha256':digest(p),'bytes':p.stat().st_size}
 return out
def inventory(conn,root):
 tables={r[0]:[c[1] for c in conn.execute('PRAGMA table_info("'+r[0]+'")')] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()};owned={}
 for table,cols in tables.items():
  if table in NODE_TABLES:owned[table]='1=1'
  elif table in ('executions','mt5_manual_orders'):owned[table]='strategy_id IS NOT NULL'
  elif table=='filter_scenarios':owned[table]="UPPER(scope)='NODE' OR node_id IS NOT NULL"
  elif table=='activity':owned[table]="strategy_id IS NOT NULL OR generation IS NOT NULL OR experiment_id IS NOT NULL OR UPPER(category) IN ('EVOLUTION','RESEARCH','VALIDATION','BACKTEST','BACKTESTING','QUALIFICATION')"
  elif table=='events':owned[table]="type LIKE 'lab_%' OR type LIKE 'paper_%' OR type LIKE 'strategy_%' OR type LIKE 'generation_%' OR type LIKE 'research_%' OR type LIKE 'validation_%' OR type LIKE 'backtest_%' OR type LIKE 'live_testing_%' OR payload LIKE '%\"node_id\"%' OR payload LIKE '%\"strategy_id\"%'"
  elif table=='meta':
   keys=[r[0] for r in conn.execute('SELECT key FROM meta') if r[0] not in KEEP_META and r[0].startswith(NODE_META_PREFIX)]
   owned[table]='key IN ('+','.join("'"+k.replace("'","''")+"'" for k in keys)+')' if keys else '0'
  elif table in PRESERVED_TABLES:continue
  elif any(c in cols for c in ('node_id','strategy_id','parent_id','child_id','run_id')):raise RuntimeError('Unclassified possible node owner; reset refused: '+table)
 artifacts={};root=Path(root)
 manifest=root/'manifest.json'
 if manifest.exists():
  allowed={'schema_version','app_version','data_schema_version','latest_persisted_node','latest_completed_node','resume_point','highest_generation','total_persisted_nodes','qualified_nodes','dead_nodes','completed_backtests','completed_validations','updated_at','manifest_status'}
  if set(json.loads(manifest.read_text()))-allowed:raise RuntimeError('Unclassified shared root manifest; reset refused')
 for rel in NODE_PATHS:
  base=root/rel
  if base.is_symlink():raise RuntimeError('Symlink in node reset scope '+str(base))
  paths=list(base.rglob('*')) if base.is_dir() else [base] if base.is_file() else []
  for p in paths:
   if p.is_symlink():raise RuntimeError('Symlink in node reset scope '+str(p))
   if p.is_file():artifacts[p.relative_to(root).as_posix()]={'sha256':digest(p),'bytes':p.stat().st_size}
 return {'tables':{t:{'predicate':w,'rows':conn.execute('SELECT COUNT(*) FROM "'+t+'" WHERE '+w).fetchone()[0]} for t,w in owned.items()},'artifacts':artifacts,'preserved_tables':sorted(set(tables)-set(owned))}
def _json(value):
 if isinstance(value,bytes):return {'__bytes__':value.hex()}
 raise TypeError(type(value).__name__)
def _file_restore(z,root):
 for name in z.namelist():
  if name.startswith('artifacts/'):
   p=(root/name[len('artifacts/'):]).resolve()
   if root not in p.parents:raise RuntimeError('Unsafe archive path')
   p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(z.read(name))
def execute(conn,root,backup_parent,confirmation,published_sha):
 if confirmation!='RESET_ALL_NODE_RESEARCH_V9_5':raise ValueError('Explicit reset confirmation required')
 if not published_sha or len(published_sha)!=40:raise ValueError('Published SHA required')
 root=Path(root).resolve();backup=Path(backup_parent).resolve()/('V9_5_'+time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'_'+uuid.uuid4().hex[:8])
 if root==backup or root in backup.parents:raise ValueError('Backup must be outside DATA')
 backup.mkdir(parents=True,mode=0o700);conn.commit();conn.row_factory=sqlite3.Row
 conn.execute('PRAGMA foreign_keys=ON');conn.execute('PRAGMA busy_timeout=5000');conn.execute('BEGIN IMMEDIATE');conn.execute('PRAGMA defer_foreign_keys=ON');committed=False;archive=backup/'node-state.zip'
 try:
  plan=inventory(conn,root);market=market_manifest(root)
  preserved={t:[tuple(r) for r in conn.execute('SELECT * FROM "'+t+'"')] for t in plan['preserved_tables'] if t!='node_registry_revision'}
  partial={t:[tuple(r) for r in conn.execute('SELECT * FROM "'+t+'" WHERE NOT ('+e['predicate']+')')] for t,e in plan['tables'].items() if e['predicate']!='1=1'}
  sequences=[tuple(r) for r in conn.execute('SELECT * FROM sqlite_sequence')]
  with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
   for t,e in plan['tables'].items():
    z.writestr('tables/'+t+'.json',json.dumps([dict(r) for r in conn.execute('SELECT * FROM "'+t+'" WHERE '+e['predicate'])],default=_json));z.writestr('schema/'+t+'.sql',conn.execute('SELECT sql FROM sqlite_master WHERE name=?',(t,)).fetchone()[0] or '')
   for rel in plan['artifacts']:z.write(root/rel,'artifacts/'+rel)
   z.writestr('sequences.json',json.dumps(sequences));z.writestr('inventory.json',json.dumps(plan,indent=2))
  with zipfile.ZipFile(archive) as z:
   if z.testzip():raise RuntimeError('Backup CRC mismatch')
   for t,e in plan['tables'].items():
    if len(json.loads(z.read('tables/'+t+'.json')))!=e['rows']:raise RuntimeError('Backup row mismatch '+t)
   for rel,info in plan['artifacts'].items():
    if hashlib.sha256(z.read('artifacts/'+rel)).hexdigest()!=info['sha256']:raise RuntimeError('Backup artifact mismatch '+rel)
  journal={'state':'BACKUP_VERIFIED','published_sha':published_sha,'backup':str(backup),'inventory':plan,'market_before':market,'backup_sha256':digest(archive)}
  (backup/'reset.json').write_text(json.dumps(journal,indent=2))
  for t,e in plan['tables'].items():conn.execute('DELETE FROM "'+t+'" WHERE '+e['predicate'])
  epoch=uuid.uuid4().hex
  conn.execute("INSERT OR REPLACE INTO meta VALUES ('v95_research_epoch',?)",(epoch,));conn.execute("INSERT OR REPLACE INTO meta VALUES ('v95_restoration_barrier',?)",(json.dumps({'epoch':epoch,'published_sha':published_sha,'reset_at':time.time()}),))
  for rel in plan['artifacts']:(root/rel).unlink()
  for t,rows in preserved.items():
   if [tuple(r) for r in conn.execute('SELECT * FROM "'+t+'"')]!=rows:raise RuntimeError('Preserved table changed '+t)
  for t,rows in partial.items():
   extra=" AND key NOT IN ('v95_research_epoch','v95_restoration_barrier')" if t=='meta' else ''
   expected=[r for r in rows if t!='meta' or r[0] not in ('v95_research_epoch','v95_restoration_barrier')]
   if [tuple(r) for r in conn.execute('SELECT * FROM "'+t+'" WHERE NOT ('+plan['tables'][t]['predicate']+')'+extra)]!=expected:raise RuntimeError('Preserved mixed rows changed '+t)
  if conn.execute('PRAGMA foreign_key_check').fetchall():raise RuntimeError('Foreign-key inconsistency')
  if market_manifest(root)!=market:raise RuntimeError('Market bytes changed')
  conn.commit();committed=True
  after=inventory(conn,root)
  if any(e['rows'] for t,e in after['tables'].items() if t!='meta'):raise RuntimeError('Node reset incomplete')
  journal.update(state='VERIFIED',epoch=epoch,post_reset_tables={t:e['rows'] for t,e in after['tables'].items()},post_reset_artifacts=len(after['artifacts']),market_files_verified=len(market),market_unchanged=True,canonical_sequences_preserved=sequences)
  (backup/'reset.json').write_text(json.dumps(journal,indent=2));(backup/'RESTORE.txt').write_text('Stop the backend and owned workers. Run reset_node_research.py --data-root DATA --restore '+str(backup)+' --confirm RESTORE_NODE_RESEARCH_V9_5 --receipt restore.json. Private backup: never commit. Restore refuses a nonempty new population.\n');return journal
 except BaseException:
  conn.rollback()
  if archive.exists() and not committed:
   with zipfile.ZipFile(archive) as z:_file_restore(z,root)
  if committed:
   journal.update(state='RESET_COMMITTED_VERIFICATION_FAILED',recovery_required=True);(backup/'reset.json').write_text(json.dumps(journal,indent=2))
  raise

def restore(conn,root,backup,confirmation):
 if confirmation!='RESTORE_NODE_RESEARCH_V9_5':raise ValueError('Explicit restore confirmation required')
 root=Path(root).resolve();backup=Path(backup).resolve();archive=backup/'node-state.zip';receipt=json.loads((backup/'reset.json').read_text())
 if digest(archive)!=receipt['backup_sha256']:raise RuntimeError('Backup SHA mismatch')
 conn.commit();conn.row_factory=sqlite3.Row;conn.execute('PRAGMA foreign_keys=ON');conn.execute('BEGIN IMMEDIATE');conn.execute('PRAGMA defer_foreign_keys=ON');market=market_manifest(root);previous={}
 try:
  if conn.execute('SELECT COUNT(*) FROM strategies').fetchone()[0]:raise RuntimeError('Refusing nonempty population')
  with zipfile.ZipFile(archive) as z:
   if z.testzip():raise RuntimeError('Backup CRC mismatch')
   for name in z.namelist():
    if name.startswith('tables/'):
     t=Path(name).stem
     if t not in receipt['inventory']['tables']:raise RuntimeError('Unknown archive owner')
     cols={r[1] for r in conn.execute('PRAGMA table_info("'+t+'")')}
     for row in json.loads(z.read(name)):
      if set(row)-cols:raise RuntimeError('Restore schema mismatch')
      vals=[bytes.fromhex(v['__bytes__']) if isinstance(v,dict) and set(v)=={'__bytes__'} else v for v in row.values()]
      conn.execute(('INSERT OR REPLACE' if t=='meta' else 'INSERT')+' INTO "'+t+'" ('+','.join('"'+k+'"' for k in row)+') VALUES ('+','.join('?' for _ in row)+')',vals)
    elif name.startswith('artifacts/'):
     p=(root/name[len('artifacts/'):]).resolve()
     if root not in p.parents:raise RuntimeError('Unsafe archive path')
     previous[p]=p.read_bytes() if p.exists() else None
   _file_restore(z,root)
   for name,watermark in json.loads(z.read('sequences.json')):conn.execute('UPDATE sqlite_sequence SET seq=MAX(seq,?) WHERE name=?',(watermark,name))
  conn.execute("DELETE FROM meta WHERE key='v95_restoration_barrier'");conn.execute("INSERT OR REPLACE INTO meta VALUES ('v95_research_epoch',?)",(uuid.uuid4().hex,))
  if conn.execute('PRAGMA foreign_key_check').fetchall():raise RuntimeError('Restored FK mismatch')
  if market_manifest(root)!=market:raise RuntimeError('Restored market mismatch')
  conn.commit();return {'state':'NODE_RESTORE_VERIFIED','restored_nodes':conn.execute('SELECT COUNT(*) FROM strategies').fetchone()[0],'market_unchanged':True}
 except BaseException:
  conn.rollback()
  for p,data in previous.items():
   if data is None:p.unlink(missing_ok=True)
   else:p.write_bytes(data)
  raise
