/* Opt-in: V10_REAL_API=http://... V10_NODE_IDS=924,1468 node tests/v10_real_data_ui.mjs
 * API must point at a consistent isolated backup, never an unreviewed production DB.
 * Actual React components + api.js + HTTP + database + engine; no mocked responses.
 */
import {build} from 'esbuild';
import {JSDOM} from 'jsdom';
import {mkdtempSync,rmSync,writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {pathToFileURL,fileURLToPath} from 'node:url';
const base=process.env.V10_REAL_API;
if(!base||process.env.V10_ISOLATED_BACKUP!=='YES')throw Error('Explicit isolated-backup API authorization required');
const ids=(process.env.V10_NODE_IDS||'').split(',').filter(Boolean).map(Number);
if(!ids.length||ids.length>2)throw Error('Supply one or two IDs only');
const here=path.dirname(fileURLToPath(import.meta.url)),dir=mkdtempSync(path.join(tmpdir(),'v10-ui-'));
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:base,pretendToBeVisual:true});
for(const key of ['window','document','navigator','HTMLElement','Element','Node','MouseEvent','localStorage'])Object.defineProperty(globalThis,key,{value:key==='window'?dom.window:dom.window[key],configurable:true,writable:true});
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const realFetch=globalThis.fetch,trace={creates:0,samples:[]};
globalThis.fetch=async(url,opts={})=>{
 const response=await realFetch(new URL(url,base),opts);
 if(String(url).includes('/api/deeptest/batches')){
  const value=await response.clone().json();
  if(opts.method==='POST'&&String(url).endsWith('/batches')){trace.creates++;trace.created=JSON.parse(opts.body);}
  if(value.batch){trace.latest=value.batch;trace.samples.push({time:Date.now(),id:value.batch.id,status:value.batch.status,phase:value.batch.phase,completed:value.batch.completed,validated:value.batch.validated});}
 }
 return response;
};
let result;
try{
 const bundle=path.join(dir,'ui.mjs');
 await build({entryPoints:[path.join(here,'v10_real_data_ui.jsx')],bundle:true,format:'esm',platform:'browser',target:'es2020',outfile:bundle,jsx:'automatic',loader:{'.js':'jsx'},logLevel:'warning',define:{'process.env.NODE_ENV':'"development"'}});
 const mod=await import(pathToFileURL(bundle).href);
 result=await mod.runRealData(ids,trace,Number(process.env.V10_OBSERVATION_SECONDS||300));
 console.log('REAL two-node component→HTTP→engine→persistence integration passed:',JSON.stringify({ids,batch:result.batch.id,status:result.batch.status,results:result.batch.results.map(r=>({node_id:r.node_id,status:r.status,run_id:r.run_id}))}));
}catch(error){result={ok:false,error:error.message,ids,batch:trace.latest};console.error(error.stack);process.exitCode=1;}
finally{
 if(process.env.V10_RECEIPT)writeFileSync(process.env.V10_RECEIPT,JSON.stringify({result,trace},null,2));
 dom.window.close();rmSync(dir,{recursive:true,force:true});
}

// React browser scheduler ports may keep Node alive after jsdom closes.
process.exit(process.exitCode||0);
