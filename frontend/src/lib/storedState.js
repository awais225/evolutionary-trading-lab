import {useState,useEffect} from 'react';
// UI preferences only. Canonical rows, metrics and scenarios remain server-owned.
export function useStoredState(key,fallback){
 const [value,setValue]=useState(()=>{try{const v=window.sessionStorage.getItem('v92.'+key);return v===null?fallback:JSON.parse(v);}catch{return fallback;}});
 useEffect(()=>{try{window.sessionStorage.setItem('v92.'+key,JSON.stringify(value));}catch{}},[key,value]);
 return [value,setValue];
}
