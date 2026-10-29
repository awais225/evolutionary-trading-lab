export async function speedRequest(path,body){
 const response=await fetch(`/api/speed-log/${path}`,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
 if(!response.ok)throw new Error(`Speed diagnostics HTTP ${response.status}`);
 return response.json();
}
export function downloadSnapshot(text){
 const url=URL.createObjectURL(new Blob([text],{type:'application/json'}));
 const link=document.createElement('a');link.href=url;link.download='speed-log-'+new Date().toISOString().replace(/[:.]/g,'-')+'.json';document.body.appendChild(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
export async function copyAllSnapshot({load=()=>speedRequest('snapshot'),clipboard=text=>navigator.clipboard.writeText(text),download=downloadSnapshot,limit=2*1024*1024}={}){
 const snapshot=await load();const text=JSON.stringify(snapshot,null,2);
 const bytes=new TextEncoder().encode(text).length;
 if(bytes>limit){download(text);return {text,snapshot,status:`Complete snapshot exported (${bytes} bytes); above the safe clipboard limit. Nothing was claimed copied.`};}
 try{await clipboard(text);return {text,snapshot,status:`Copied ALL retained diagnostics (${bytes} bytes, snapshot ${snapshot.snapshot_id}). Display filters were ignored.`};}
 catch(error){download(text);return {text,snapshot,status:`Clipboard failed; complete snapshot download requested (${bytes} bytes). No partial copy was substituted.`};}
}
