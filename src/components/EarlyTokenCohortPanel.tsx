import { useMemo } from 'react';
import { Activity, AlertTriangle, Clock3, Download, FlaskConical, Info, ShieldAlert } from 'lucide-react';
import {
  CATEGORY_LABELS,
  EARLY_COHORT_VERSION,
  EARLY_COHORT_CAPACITY,
  EARLY_COHORT_WINDOWS,
  summarizeEarlyCohorts,
  type CohortRecord, type CohortState, type Horizon,
} from '../lib/earlyCohortResearch';

function metric(value:number|null, suffix='') {
  return value === null || !Number.isFinite(value) ? '—' : value.toLocaleString('bg-BG',{maximumFractionDigits:2})+suffix;
}
function changeOf(record:CohortRecord,horizon:Horizon):string {
  const value=record.outcomes[horizon];
  if (!value) return 'в процес';
  if(value.status==='missed') return 'липсва';
  return (value.changePct > 0?'+':'')+metric(value.changePct,'%');
}
const fmtTime=(n:number)=>new Date(n).toLocaleTimeString('bg-BG',{hour:'2-digit',minute:'2-digit',second:'2-digit'});
const categoriesStyle={
  early_activity_unverified:'text-cyan-200',
  sharp_move_review:'text-amber-200',
  insufficient_evidence:'text-slate-400',
} as const;

export default function EarlyTokenCohortPanel({
  study,onSelect,
}: {
  study:CohortState;
  onSelect:(mint:string)=>void;
}) {
  // A re-render occurs every feed update, including when the study has no
  // completed target window yet.
  const now=Date.now();
  const grouped=useMemo(()=>summarizeEarlyCohorts(study,now),[study,now]);
  const latest=useMemo(()=>[...study.records].sort((a,b)=>b.firstSeenAt-a.firstSeenAt).slice(0,12),[study]);
  const due=grouped.reduce((sum,g)=>sum+g.windows['15'].due,0);
  const complete=grouped.reduce((sum,g)=>sum+g.windows['15'].observed,0);
  const missing=grouped.reduce((sum,g)=>sum+g.windows['15'].missing,0);

  function exportObservations() {
    // This file contains only market evidence and research labels; never
    // wallet keys, auth headers or user-specific secrets.
    const blob=new Blob([JSON.stringify({
      version:EARLY_COHORT_VERSION,
      exportedAt:new Date().toISOString(),
      runMode:'LOCAL_BROWSER_OBSERVATION_ONLY',
      sampling:'1/5/15 minutes after first browser observation, not after coin launch',
      monetizableReturn:false,
      study,
    },null,2)],{type:'application/json;charset=utf-8'});
    const url=URL.createObjectURL(blob);
    const a=document.createElement('a');
    a.href=url;a.download='neo-early-paper-observations.json';
    document.body.appendChild(a);a.click();a.remove();
    window.setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  return <section data-testid="neo-early-cohort-paper"
    className="xl:col-span-2 overflow-hidden rounded-3xl border border-cyan-300/15 bg-[#0b0e11]">
    <div className="flex flex-wrap items-start justify-between gap-3 border-b border-white/[0.07] p-4">
      <div>
        <div className="flex items-center gap-2 text-[9px] font-black uppercase tracking-[0.15em] text-cyan-200">
          <FlaskConical className="h-4 w-4" /> Independent PAPER observation · V1
        </div>
        <h2 className="mt-1 text-base font-black text-white">Ранни токени · 1 / 5 / 15 минути</h2>
        <p className="mt-1 max-w-3xl text-[11px] leading-5 text-slate-400">
          Групира токени, видени до 15-ата минута от създаването им, и проследява
          показаната пазарна цена след първото наблюдение. Категорията се заключва
          още при първия snapshot — без използване на бъдещи цени.
        </p>
      </div>
      <button onClick={exportObservations} disabled={study.records.length===0}
        className="inline-flex items-center gap-2 rounded-xl border border-cyan-300/20 bg-cyan-400/[0.05] px-3 py-2 text-[11px] font-bold text-cyan-200 hover:bg-cyan-400/10 disabled:opacity-40">
        <Download className="h-4 w-4" /> Експорт JSON
      </button>
    </div>

    <div className="grid grid-cols-2 gap-2 border-b border-white/[0.06] p-3 sm:grid-cols-4">
      {([
        ['Първоначално наблюдавани',String(study.records.length)],
        ['Достигнали 15 мин.',String(due)],
        ['Събрани 15-мин. цени',String(complete)],
        ['Пропуснати 15-мин. данни',String(missing)],
      ] as [string,string][]).map(([label,value])=>
        <div key={label} className="rounded-xl border border-white/[0.06] bg-white/[0.015] p-3">
          <div className="text-[10px] leading-4 text-slate-500">{label}</div>
          <div className="mt-1 text-xl font-black tabular-nums text-white">{value}</div>
        </div>)}
    </div>

    <div className="grid gap-2 p-3 xl:grid-cols-3">
      {grouped.map(group=>{
        const sample=group.windows['5'];
        return <div key={group.category}
          className="rounded-2xl border border-white/[0.07] bg-white/[0.018] p-3">
          <div className={'flex min-h-9 items-start gap-2 text-[11px] font-black '+
            categoriesStyle[group.category]}>
            {group.category==='early_activity_unverified'?<Activity className="mt-0.5 h-4 w-4 shrink-0"/>:
              group.category==='sharp_move_review'?<ShieldAlert className="mt-0.5 h-4 w-4 shrink-0"/>:
                <Info className="mt-0.5 h-4 w-4 shrink-0"/>}
            {CATEGORY_LABELS[group.category]}
          </div>
          <div className="mt-2 flex items-end justify-between gap-3">
            <div>
              <div className="text-[9px] text-slate-600">Първоначално наблюдавани</div>
              <div className="text-lg font-black text-white">{group.total}</div>
            </div>
            <div className="text-right">
              <div className="text-[9px] text-slate-600">Цена нагоре за 5 мин.</div>
              <div className="text-lg font-black text-white">{sample.increased} / {sample.observed}</div>
            </div>
          </div>
          <div className="mt-2 overflow-x-auto">
            <table className="w-full text-[10px]">
              <thead><tr className="border-b border-white/[0.06] text-slate-600">
                <th className="py-1 text-left">След</th>
                <th className="py-1 text-right">Валидни</th>
                <th className="py-1 text-right">Нагоре</th>
                <th className="py-1 text-right">Медиана</th>
                <th className="py-1 text-right">Липсващи</th>
              </tr></thead>
              <tbody>{EARLY_COHORT_WINDOWS.map(min=>{
                const s=group.windows[String(min) as Horizon];
                return <tr key={min} className="border-b border-white/[0.04] text-slate-400">
                  <td className="py-1.5 text-white">{min} мин.</td>
                  <td className="text-right">{s.observed}</td>
                  <td className="text-right">{s.increased}</td>
                  <td className="text-right">{metric(s.medianObservedChangePct,'%')}</td>
                  <td className="text-right">{s.missing}</td>
                </tr>;
              })}</tbody>
            </table>
          </div>
          <div className="mt-2 text-[9px] leading-4 text-slate-600">
            Положителна промяна на показаната цена ≠ реализирана печалба;
            липсващите данни не се броят като покачване.
          </div>
        </div>;
      })}
    </div>

    <div className="border-t border-white/[0.06] p-3">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <Clock3 className="h-4 w-4 text-cyan-200"/>
        <h3 className="text-xs font-black text-white">Последни наблюдавани токени</h3>
        <span className="text-[10px] text-slate-600">Не са подредени по очаквана доходност</span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[630px] text-left text-[10px]">
          <thead><tr className="border-b border-white/[0.07] text-slate-600">
            <th className="px-2 py-2">Токен</th><th>Начало</th><th>Категория при първо наблюдение</th>
            <th className="text-right">След 1 мин.</th><th className="text-right">След 5 мин.</th><th className="pr-2 text-right">След 15 мин.</th>
          </tr></thead>
          <tbody>{latest.length === 0 ?
            <tr><td colSpan={6} className="px-3 py-5 text-center text-slate-500">
              Няма още токени, засечени до 15-ата минута. Новите наблюдения започват от момента на отваряне на дашборда.
            </td></tr> :
            latest.map(row=><tr key={row.key} className="border-b border-white/[0.04] text-slate-400">
              <td className="max-w-[120px] truncate px-2 py-2">
                <button onClick={()=>onSelect(row.mint)} className="max-w-full truncate font-black text-white hover:text-cyan-200">
                  {row.symbol}
                </button>
              </td>
              <td>{fmtTime(row.firstSeenAt)}</td>
              <td className={categoriesStyle[row.category]}>{CATEGORY_LABELS[row.category]}</td>
              {EARLY_COHORT_WINDOWS.map(min=><td key={min} className="text-right">
                {changeOf(row,String(min) as Horizon)}
              </td>)}
            </tr>)}
          </tbody>
        </table>
      </div>
    </div>

    <div className="flex items-start gap-2 border-t border-amber-400/10 bg-amber-400/[0.03] p-4 text-[10px] leading-5 text-amber-200/80">
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
      <div>
        <b>Важни ограничения:</b> Измерването е 1/5/15 минути след първото наблюдение
        в браузъра, не след създаването на токена. Данните се събират само докато
        дашбордът е отворен и обновява своя фийд; съхраняват се в този браузър.
        Пропуснатите моменти не се попълват назад. Няма гарантирана изпълнима котировка,
        разходи, реални ордери или доказана независимост на портфейлите.
        {study.records.length>=EARLY_COHORT_CAPACITY &&
          <strong className="mt-1 block">Достигнат лимитът от {EARLY_COHORT_CAPACITY} наблюдения. Нови записи не се добавят автоматично.</strong>}
        <div className="mt-1 text-[9px] text-slate-600">{EARLY_COHORT_VERSION} · Без търговски сигнали и промени в engine</div>
      </div>
    </div>
  </section>;
}
