import { useMemo } from 'react';
import { AlertTriangle, FlaskConical, LockKeyhole, MoveUpRight } from 'lucide-react';
import { type CohortState } from '../lib/earlyCohortResearch';
import { evaluateEarlyPaperCandidate } from '../lib/earlyPaperCandidate';

/** Explicit challenger preview: never claims the old main engine was switched. */
export default function EarlyPaperCandidatePanel({
  study,activeProfile,onOpenStudy,
}:{
  study:CohortState;
  activeProfile:string|null;
  onOpenStudy:()=>void;
}) {
  const report=useMemo(()=>evaluateEarlyPaperCandidate(study),[study]);
  const grouped=[5,10].map(target=>({
    target,
    windows:report.metrics.filter(m=>m.targetPct===target),
  }));
  return <section data-testid="early-paper-primary-candidate"
    className="mt-4 overflow-hidden rounded-3xl border border-cyan-300/20 bg-[#0b0e11]">
    <div className="flex flex-wrap items-start justify-between gap-3 border-b border-white/[0.06] p-4">
      <div>
        <div className="flex items-center gap-2 text-[9px] font-black uppercase tracking-[0.17em] text-cyan-200">
          <FlaskConical className="h-4 w-4"/> Предложение за новата основна PAPER стратегия
        </div>
        <h3 className="mt-1 text-base font-black text-white">Early Pool · проверка на целите +5% / +10%</h3>
        <p className="mt-1 max-w-3xl text-[11px] leading-5 text-slate-400">
          Сравнява действително наблюдавани цени 1, 5 и 15 минути след първата поява
          на токена във фийда. Целите са изследователски сценарии, а не
          автоматични ордери или обещания за доходност.
        </p>
      </div>
      <span className="flex shrink-0 items-center gap-1.5 rounded-lg border border-amber-300/20 bg-amber-300/[0.035] px-2.5 py-1.5 text-[10px] font-bold text-amber-200">
        <LockKeyhole className="h-3.5 w-3.5"/> Не е активирана в engine
      </span>
    </div>

    <div className="grid gap-2 px-3 pt-3 sm:grid-cols-3">
      <div className="rounded-xl border border-white/[0.06] bg-white/[0.015] p-3">
        <div className="text-[9px] text-slate-500">Текуща стратегия на backend-а</div>
        <div className="mt-1 truncate text-[11px] font-bold text-white">{activeProfile || 'Няма потвърдена връзка'}</div>
      </div>
      <div className="rounded-xl border border-white/[0.06] bg-white/[0.015] p-3">
        <div className="text-[9px] text-slate-500">Нови наблюдавани пулове</div>
        <div className="mt-1 text-lg font-black text-white">{report.newPoolsObserved}</div>
      </div>
      <div className="rounded-xl border border-white/[0.06] bg-white/[0.015] p-3">
        <div className="text-[9px] text-slate-500">Начална цена с доказан timestamp</div>
        <div className="mt-1 text-lg font-black text-cyan-200">{report.eligibleFirstMarks}</div>
      </div>
    </div>

    <div className="grid gap-3 p-3 lg:grid-cols-2">
      {grouped.map(({target,windows})=><div key={target}
        className="overflow-hidden rounded-2xl border border-white/[0.07]">
        <div className="flex items-center justify-between border-b border-white/[0.06] p-3">
          <div className="text-[11px] font-black text-white">
            Хипотетичен праг <span className="text-cyan-200">+{target}%</span>
          </div>
          <div className="text-[9px] text-slate-500">Наблюдавана пазарна цена</div>
        </div>
        <table className="w-full text-[10px]">
          <thead className="text-slate-500"><tr className="border-b border-white/[0.05]">
            <th className="px-3 py-2 text-left">След</th>
            <th className="px-2 py-2 text-right">Измерени</th>
            <th className="px-2 py-2 text-right">Над прага</th>
            <th className="px-2 py-2 text-right">Дял</th>
            <th className="px-3 py-2 text-right">Без данни</th>
          </tr></thead>
          <tbody>{windows.map(row=><tr key={row.horizon}
            className="border-b border-white/[0.04] text-slate-300">
            <td className="px-3 py-2 text-white">{row.horizon} мин.</td>
            <td className="px-2 py-2 text-right">{row.observed}</td>
            <td className="px-2 py-2 text-right font-bold text-cyan-200">{row.observedAtOrAbove}</td>
            <td className="px-2 py-2 text-right">{row.observedSharePct===null?'—':row.observedSharePct.toFixed(1)+'%'}</td>
            <td className="px-3 py-2 text-right text-amber-200">{row.missing+row.pending}</td>
          </tr>)}</tbody>
        </table>
      </div>)}
    </div>

    <div className="flex flex-wrap items-center justify-between gap-2 border-t border-white/[0.06] px-4 py-3">
      <p className="max-w-3xl text-[10px] leading-5 text-amber-200/75">
        <AlertTriangle className="mr-1 inline h-3.5 w-3.5"/>
        Прагът е достигнат в конкретна наблюдавана точка, не непременно между точките.
        Това не е доходност от сделка: липсват потвърдени изпълними цени, ликвидност за продажба и разходи.
        Пазарният PAPER engine продължава да използва показания по-горе активен профил.
      </p>
      <button onClick={onOpenStudy}
        className="inline-flex items-center gap-1.5 rounded-lg border border-cyan-300/20 px-3 py-2 text-[10px] font-bold text-cyan-200 hover:bg-cyan-300/[0.06]">
        Отвори наблюденията <MoveUpRight className="h-3.5 w-3.5"/>
      </button>
    </div>
  </section>;
}
