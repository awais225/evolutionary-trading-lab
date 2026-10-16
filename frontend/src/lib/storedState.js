import {useState,useEffect,useRef} from 'react';
// UI preferences only; shared source controls synchronize within this window.
export function useStoredState(key,fallback){
 const read=()=>{try{const v=window.sessionStorage.getItem('v92.'+key);return v===null?fallback:JSON.parse(v);}catch{return fallback;}};
 const [value,setValue]=useState(read),current=useRef(value);current.current=value;
 useEffect(()=>{const listener=e=>{if(e.detail?.key===key&&JSON.stringify(e.detail.value)!==JSON.stringify(current.current))setValue(e.detail.value);};window.addEventListener('lab-preference',listener);return()=>window.removeEventListener('lab-preference',listener);},[key]);
 useEffect(()=>{try{window.sessionStorage.setItem('v92.'+key,JSON.stringify(value));window.dispatchEvent(new window.CustomEvent('lab-preference',{detail:{key,value}}));}catch{}},[key,value]);
 return [value,setValue];
}
