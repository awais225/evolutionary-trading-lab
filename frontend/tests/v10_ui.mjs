// Deterministic V10 component contracts with synthetic HTTP responses.
import {build} from 'esbuild';
import {JSDOM} from 'jsdom';
import {mkdtempSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {pathToFileURL,fileURLToPath} from 'node:url';
const here=path.dirname(fileURLToPath(import.meta.url)),dir=mkdtempSync(path.join(tmpdir(),'v10-ui-fixture-'));
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost/',pretendToBeVisual:true});
for(const key of ['window','document','navigator','HTMLElement','Element','Node','MouseEvent','localStorage','sessionStorage'])Object.defineProperty(globalThis,key,{value:key==='window'?dom.window:dom.window[key],configurable:true,writable:true});
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
let code=0,payload=()=>({});
globalThis.fetch=async(url,opts={})=>{const body=await payload(String(url),opts.method||'GET',opts.body);return {ok:true,status:200,headers:{get:()=> 'application/json'},json:async()=>body};};
try{
 const bundle=path.join(dir,'ui.mjs');
 await build({entryPoints:[path.join(here,'v10_ui.jsx')],bundle:true,format:'esm',platform:'browser',target:'es2020',outfile:bundle,jsx:'automatic',loader:{'.js':'jsx'},logLevel:'warning',define:{'process.env.NODE_ENV':'"development"'}});
 const mod=await import(pathToFileURL(bundle).href);payload=mod.payloadFor;
 const result=await mod.runSmoke();
 console.log(`v10 fixture UI harness: ${result.total-result.failures.length}/${result.total} behaviours OK`);
 for(const f of result.failures)console.error('FAIL',f.label,f.error);
 if(result.failures.length)code=1;
}catch(error){console.error(error);code=2;}
finally{dom.window.close();rmSync(dir,{recursive:true,force:true});}
process.exit(code);
