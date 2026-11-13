import { useEffect, useRef, useState } from 'react';
import {
  EARLY_COHORT_STORAGE_KEY,
  advanceEarlyCohorts,
  deserializeEarlyCohorts,
  type CohortCoin,
  type CohortState,
} from '../lib/earlyCohortResearch';

/**
 * Browser-persistent research observations. Always mounted from App so
 * switching tabs does not interrupt sampling. No server-side scheduler.
 */
export function useEarlyCohortResearch(coins:CohortCoin[]) {
  const [study,setStudy]=useState<CohortState>(()=>{
    let saved:string|null=null;
    try { saved=localStorage.getItem(EARLY_COHORT_STORAGE_KEY); } catch { /* optional */ }
    return deserializeEarlyCohorts(saved,Date.now());
  });
  const latestFeed=useRef(coins);
  latestFeed.current=coins;
  const update=()=>{
    setStudy(current=>{
      const next=advanceEarlyCohorts(current,latestFeed.current,Date.now());
      if(next!==current){
        try { localStorage.setItem(EARLY_COHORT_STORAGE_KEY,JSON.stringify(next)); }
        catch { /* storage quotas / browser privacy may restrict persistence */ }
      }
      return next;
    });
  };
  useEffect(()=>{ update(); },[coins]);
  useEffect(()=>{
    const id=window.setInterval(()=>update(),7000);
    return ()=>window.clearInterval(id);
  },[]);
  return study;
}
