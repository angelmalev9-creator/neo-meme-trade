import { useEffect, useState } from 'react';
import { Activity, AlertTriangle, ArrowUpRight, Clock3, RefreshCw } from 'lucide-react';

/** Public, sanitized, read-only research status. No wallet/transaction API. */
const SHADOW_REPORT_URL = 'https://neo-paper.92-242-188-182.sslip.io/report.json';
const EXPECTED_VERSION = 'NEO_SHADOW_PUBLIC_PAPER_V1';
type VariantReport = {
  completed:number; mark_wins:number; mark_losses:number; unresolved:number;
  open_unverified:number; mark_net_model_pnl_usd:number;
  virtual_mark_balance_usd:number; win_rate_among_resolvable_pct:number|null;
  mark_target_pct:number; mark_stop_pct:number;
};
type LivePaperReport = {
  version:string; as_of:string; status:'updated'|'stale';
  mode:string; source_age_seconds:number; market_events_read:number;
  started_ms:number; orders_created:number; realized_money_usd:number;
  executable_swap_quote_verified:boolean; mark_results_are_not_cash_profit:boolean;
  cross_scenario_results_not_additive:boolean;
  variants:{NET_TARGET_5:VariantReport;NET_TARGET_10:VariantReport};
};
const number=(n:number)=>new Intl.NumberFormat('bg-BG',{maximumFractionDigits:0}).format(n);
const money=(n:number)=>(n>0?'+':n<0?'−':'')+
  '$'+Math.abs(n).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});

export default function LiveSelectivePaperPanel(){
  const [report,setReport]=useState<LivePaperReport|null>(null);
  const [loadedAt,setLoadedAt]=useState(0);
  const [error,setError]=useState<string|null>(null);
  useEffect(()=>{
    let alive=true;
    const fetchReport=async()=>{
      try{
        const controller=new AbortController();
        const limit=setTimeout(()=>controller.abort(),10000);
        let response:Response;
        try {
          response=await fetch(SHADOW_REPORT_URL,{cache:'no-store',signal:controller.signal});
        }finally{
          clearTimeout(limit);
        }
        if(!response.ok)throw new Error('HTTP '+response.status);
        const data=await response.json() as LivePaperReport;
        if(data.version!==EXPECTED_VERSION || !data.mark_results_are_not_cash_profit
          || data.orders_created!==0 || !data.variants?.NET_TARGET_5 || !data.variants?.NET_TARGET_10)
          throw new Error('Неподдържан или непълен PAPER отчет');
        if(alive){setReport(data);setLoadedAt(Date.now());setError(null);}
      }catch(e){
        if(alive)setError(e instanceof Error?e.message:'Неуспешно изтегляне на PAPER данните');
      }
    };
    fetchReport();
    const timer=setInterval(fetchReport,30000);
    return()=>{alive=false;clearInterval(timer);};
  },[]);
  const age=report ? Date.now()-Date.parse(report.as_of):Infinity;
  const freshness=!!report && Number.isFinite(age)&&age>=-30000&&age<180000&&report.status==='updated';
  return <section data-testid="neo-selective-early-live-paper" className="mt-4 overflow-hidden rounded-3xl border border-cyan-300/15 bg-[#0b0e11]">
    <div className="flex flex-wrap items-start justify-between gap-3 border-b border-white/[0.07] p-4">
      <div>
        <div className="flex items-center gap-2 text-[9px] font-black uppercase tracking-[0.15em] text-cyan-200">
          <Activity className="h-4 w-4"/> Самостоятелен VPS · Live PAPER наблюдение
        </div>
        <h2 className="mt-1 text-base font-black text-white">NEO Selective Early — 2 варианта</h2>
        <p className="mt-1 max-w-3xl text-[11px] leading-5 text-slate-400">
          Сървърно наблюдение на нови Solana пулове с виртуални цели +5% и +10%.
          Няма реални покупки, продажби или доказана изпълнима цена.
        </p>
      </div>
      <div className="flex items-center gap-2 rounded-xl border border-white/10 px-3 py-2">
        {freshness?<Activity className="h-3.5 w-3.5 text-cyan-300"/>:
          <AlertTriangle className="h-3.5 w-3.5 text-amber-300"/>}
        <span className={'text-[10px] font-bold '+(freshness?'text-cyan-200':'text-amber-200')}>
          {!report&&error?'НЯМА ВРЪЗКА':freshness?'PAPER ДАННИТЕ СА АКТУАЛНИ':'ДАННИТЕ НЕ СА ПОТВЪРДЕНИ КАТО АКТУАЛНИ'}
        </span>
      </div>
    </div>

    {error&&<div className="flex items-center gap-2 border-b border-amber-400/10 px-4 py-2 text-[10px] text-amber-200">
      <AlertTriangle className="h-3.5 w-3.5 shrink-0"/> Последната проверка не успя: {error}
    </div>}

    {report ? <>
      <div className="grid grid-cols-2 gap-2 border-b border-white/[0.06] p-3 sm:grid-cols-4">
        {([
          ['Обработени ценови наблюдения',number(report.market_events_read)],
          ['Реални ордери',String(report.orders_created)],
          ['Реално реализирани',money(report.realized_money_usd)],
          ['Обновено',new Date(report.as_of).toLocaleTimeString('bg-BG',{hour:'2-digit',minute:'2-digit',second:'2-digit'})],
        ] as [string,string][]).map(([label,value])=>
          <div key={label} className="rounded-xl border border-white/[0.06] bg-white/[0.015] p-3">
            <div className="text-[10px] text-slate-500">{label}</div>
            <div className="mt-1 text-base font-black tabular-nums text-white">{value}</div>
          </div>)}
      </div>

      <div className="grid gap-3 p-3 lg:grid-cols-2">
        {(['NET_TARGET_5','NET_TARGET_10'] as const).map(id=>{
          const v=report.variants[id];
          const net=v.mark_net_model_pnl_usd;
          return <div key={id} className="min-w-0 rounded-2xl border border-white/[0.08] bg-white/[0.015] p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="text-xs font-black text-white">SELECTIVE EARLY · +{v.mark_target_pct}%</div>
              <span className="rounded-lg border border-white/[0.09] px-2 py-1 text-[9px] text-slate-400">
                Моделиран стоп {v.mark_stop_pct}%
              </span>
            </div>
            <div className="mt-3 text-[9px] font-bold uppercase tracking-wider text-slate-500">
              Моделирана нетна промяна · не е реална печалба
            </div>
            <div className={'mt-1 text-2xl font-black tabular-nums '+(net<0?'text-red-300':'text-cyan-200')}>{money(net)}</div>
            <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
              {([
                ['Приключени',v.completed],['Положителни',v.mark_wins],
                ['Отрицателни',v.mark_losses],['Непотвърдени',v.unresolved+v.open_unverified],
              ] as [string,number][]).map(([name,value])=>
                <div key={name} className="rounded-lg border border-white/[0.05] px-2 py-2">
                  <div className="text-[9px] text-slate-500">{name}</div>
                  <div className="mt-0.5 text-sm font-black tabular-nums text-white">{value}</div>
                </div>)}
            </div>
            <div className="mt-2 text-[10px] text-slate-500">
              Положителни сред проверимите по модела: {v.win_rate_among_resolvable_pct===null?'—':v.win_rate_among_resolvable_pct.toFixed(1)+'%'}
              {' '}· Изключва непотвърдените изходи
            </div>
          </div>;
        })}
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-white/[0.06] px-4 py-2">
        <span className="flex items-center gap-1 text-[10px] text-slate-600">
          <Clock3 className="h-3.5 w-3.5"/> Последна успешна проверка в браузъра:
          {' '}{loadedAt?new Date(loadedAt).toLocaleTimeString('bg-BG'):'—'}
        </span>
        <a href={SHADOW_REPORT_URL} target="_blank" rel="noreferrer"
          className="inline-flex items-center gap-1 text-[10px] font-bold text-cyan-200 hover:text-cyan-100">
          Отвори суровия read-only отчет <ArrowUpRight className="h-3 w-3"/>
        </a>
      </div>
    </> : <div className="flex items-center gap-2 p-5 text-[11px] text-slate-400">
      <RefreshCw className="h-4 w-4"/> {error?'Няма достъпен сървърен отчет.':'Зареждане на данните от отделния PAPER сървър…'}
    </div>}

    <div className="border-t border-amber-400/10 bg-amber-400/[0.025] px-4 py-3 text-[10px] leading-5 text-amber-200/80">
      <span className="font-black">Само изследователски PAPER данни:</span>
      {' '}пазарните маркировки не са изпълнени сделки; разходите са приблизително моделирани,
      непотвърдените изходи не са печалби, а резултатите от двата варианта не се сумират.
      Основният engine остава отделен. Не е доказана доходност при реална търговия.
    </div>
  </section>;
}
