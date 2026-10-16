import React,{useState} from "react";
/** Disclosure only: no API, orders, cancellation, risk mutation or unmounting. */
export default function ManualPanelDisclosure({children}){
 const [open,setOpen]=useState(false);
 return <section className="panel"><button type="button" className="btn ghost" aria-expanded={open} aria-controls="manual-order-content" onClick={()=>setOpen(v=>!v)} style={{display:'flex',justifyContent:'space-between',width:'100%',padding:14}}><b>MANUAL TRADING PANEL</b><span aria-hidden="true">{open?'⌄':'›'}</span></button><div id="manual-order-content" hidden={!open} style={open?undefined:{display:'none'}}>{children}</div></section>;
}
