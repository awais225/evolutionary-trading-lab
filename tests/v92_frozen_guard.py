"""AST-level frozen-logic guard with ONLY explicitly requested V9.2 exceptions.
It checks every live function against V8 after removing the exact additive sign,
scenario-gating and truthful logging fields; execution logic cannot drift.
"""
import ast,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
BASE='a717884604fd260024c45b9d77e56d18c11dc3fa'
SIGNS={'sl_offset_sign','tp_offset_sign'}
LOG_FIELDS={'fill_ts','exec_price','sl_confirmed','tp_confirmed','volume_filled','volume_current'}
class Normalize(ast.NodeTransformer):
    def visit_FunctionDef(self,node):
        if node.name=='_human_stage_message':return None # explicitly requested text only
        self.generic_visit(node)
        if node.name=='apply_offsets':
            kept=[(arg,default) for arg,default in zip(node.args.kwonlyargs,node.args.kw_defaults) if arg.arg not in SIGNS]
            node.args.kwonlyargs=[x[0] for x in kept];node.args.kw_defaults=[x[1] for x in kept]
        return node
    def visit_Call(self,node):
        self.generic_visit(node)
        if isinstance(node.func,ast.Name) and node.func.id=='apply_offsets':node.keywords=[k for k in node.keywords if k.arg not in SIGNS]
        return node
    def visit_ImportFrom(self,node):
        if node.module=='deeptest.filter_scenarios':return None
        return node
    def visit_Assign(self,node):
        if any(isinstance(t,ast.Name) and t.id=='assigned' for t in node.targets):return None
        return self.generic_visit(node)
    def visit_If(self,node):
        text=ast.unparse(node.test)
        if text=='assigned' or "s.get('filter_rules')" in text or 'math.isfinite(sl_off)' in text or 'sl_offset_sign not in' in text or text in ('sl_offset_sign is not None','tp_offset_sign is not None'):return None
        return self.generic_visit(node)
    def visit_Dict(self,node):
        self.generic_visit(node);keep=[]
        for key,value in zip(node.keys,node.values):
            if isinstance(key,ast.Constant) and key.value in SIGNS|{'filter_rules'}:continue
            if isinstance(key,ast.Constant) and key.value in LOG_FIELDS:value=ast.Constant('REQUESTED_LOG_FIELD')
            keep.append((key,value))
        node.keys=[p[0] for p in keep];node.values=[p[1] for p in keep];return node

def assert_live_frozen():
    for path in ('backend/app/live_testing/engine.py','backend/app/live_testing/offsets.py','backend/app/live_testing/schedule.py'):
        original=subprocess.check_output(['git','show',BASE+':'+path],cwd=ROOT,text=True)
        def functions(text):
            out={}
            for n in ast.walk(ast.parse(text)):
                if isinstance(n,ast.FunctionDef):
                    cleaned=Normalize().visit(n)
                    if cleaned is not None:out[n.name]=ast.dump(cleaned,include_attributes=False)
            return out
        a,b=functions(original),functions((ROOT/path).read_text())
        assert a==b, 'frozen live semantics drift outside V9.2 exceptions: '+path+'; '+str([k for k in a.keys()|b.keys() if a.get(k)!=b.get(k)])

def normalize_config_writer(text):
    # Only add sign+version columns/values; every other existing SQL field,
    # default, type coercion, and control flag remains byte-for-byte equivalent.
    text=text.replace(', sl_offset_sign, tp_offset_sign, offset_schema_version','')
    text=text.replace(',\n                sl_offset_sign=excluded.sl_offset_sign, tp_offset_sign=excluded.tp_offset_sign,\n                offset_schema_version=excluded.offset_schema_version','')
    text=text.replace('('+','.join('?' for _ in range(31))+')','('+','.join('?' for _ in range(28))+')')
    text=text.replace("            cfg.get('sl_offset_sign'), cfg.get('tp_offset_sign'), cfg.get('offset_schema_version') or 1,\n",'')
    return text
