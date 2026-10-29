import {JSDOM} from 'jsdom';
import {build} from 'esbuild';
import {mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
const dir=await mkdtemp(path.join(tmpdir(),'v103-ui-'));
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost',pretendToBeVisual:true});
for(const key of ['window','document','navigator','HTMLElement','Node','MutationObserver','getComputedStyle'])Object.defineProperty(globalThis,key,{value:key==='getComputedStyle'?dom.window.getComputedStyle.bind(dom.window):dom.window[key],configurable:true});
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
try{
 const output=path.join(dir,'test.mjs');
 await build({entryPoints:['tests/v10_3_speed_ui.jsx'],outfile:output,bundle:true,platform:'node',format:'esm',packages:'external',jsx:'automatic'});
 // External packages resolve from frontend; the runner itself remains in TEMP.
 const {createRequire}=await import('node:module');
 const require=createRequire(pathToFileURL(path.resolve('package.json')));
 // esbuild external React resolution requires node_modules reachable from temporary output.
 const {symlink}=await import('node:fs/promises');await symlink(path.resolve('node_modules'),path.join(dir,'node_modules'));
 await import(pathToFileURL(output));
}finally{dom.window.close();await rm(dir,{recursive:true,force:true});}
