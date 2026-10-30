import {JSDOM} from 'jsdom';
import {build} from 'esbuild';
import {mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
const dir=await mkdtemp(path.join(tmpdir(),'v105-ui-'));
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost',pretendToBeVisual:true});
for(const key of ['window','document','navigator','HTMLElement','Node','MutationObserver','getComputedStyle','localStorage','sessionStorage','MouseEvent'])Object.defineProperty(globalThis,key,{value:key==='getComputedStyle'?dom.window.getComputedStyle.bind(dom.window):dom.window[key],configurable:true});
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
try{
 const output=path.join(dir,'test.mjs');
 await build({entryPoints:['tests/v10_5_ui.jsx'],outfile:output,bundle:true,platform:'browser',format:'esm',external:['node:assert/strict'],jsx:'automatic',loader:{'.css':'empty','.js':'jsx'},define:{'process.env.NODE_ENV':'"development"'}});
 await import(pathToFileURL(output));
}finally{dom.window.close();await rm(dir,{recursive:true,force:true});}

process.exit(0); // Original App module owns global UI timers; all mounted roots were unmounted above.
