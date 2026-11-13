import { AlertTriangle, BookOpenCheck, FlaskConical, ShieldAlert } from 'lucide-react';
import { useMemo } from 'react';
import {
  comparePaperChallenger, PAPER_CHALLENGER_VERSION,
  type PaperAuditTrade, type Snapshot,
} from '../lib/neoPaperChallenger';

const LABELS: Record<Snapshot['mode'],{name:string;detail:string}> = {
  recorded: {
    name:'Записани сделки',
    detail:'Реално отчетената PAPER история без допълнително филтриране',
  },
  cost_evidence: {
    name:'Quote evidence',
    detail:'Изключва записи с липсващи/неблагоприятни доказателства за входната котировка',
  },
  cost_and_pool: {
    name:'Quote + pool cooldown',
    detail:'Същите проверки, плюс хронологична защита след повторни загуби в един пул',
  },
};
const REASONS: Record<string,string> = {
  missing_entry_quote_evidence:'Липсва входна котировка/разход',
  modeled_roundtrip_cost:'Висок моделиран начален разход',
  modeled_worst_case_cost:'Висока най-лоша оценка на разхода',
  modeled_entry_impact:'Висок entry impact',
  independent_price_disagreement:'Несъгласувани независими цени',
  missing_exact_pool:'Няма точна двойка mint + pool',
  previous_verified_paper_pool_losses:'Повторни загуби в същия пул',
};
const money = (v:number) => (v < 0 ? '−' : v > 0 ? '+' : '') +
  '$' + Math.abs(v).toLocaleString('en-US',{maximumFractionDigits:2,minimumFractionDigits:2});

export default function PaperRepoPatternPanel({history}: {history:PaperAuditTrade[]}) {
  const report = useMemo(() => comparePaperChallenger(history),[history]);
  return <section data-testid="paper-repo-patterns"
    className="mt-4 overflow-hidden rounded-3xl border border-cyan-300/15 bg-[#0b0e11]">
    <header className="flex flex-wrap items-start justify-between gap-3 border-b border-white/[0.07] p-4">
      <div>
        <div className="flex items-center gap-2 text-[9px] font-black uppercase tracking-[0.14em] text-cyan-200">
          <FlaskConical className="h-4 w-4"/> PAPER Research · Open-source patterns
        </div>
        <h2 className="mt-1 text-base font-black text-white">NEO Challenger — сравнение на защитни модели</h2>
        <p className="mt-1 max-w-3xl text-[11px] leading-5 text-slate-400">
          Сравнява последните до 100 приключени PAPER сделки с два допълнителни филтъра:
          качество на входната котировка и защита от повтарящи се губещи пулове.
          Не генерира ордери или препоръки за покупки.
        </p>
      </div>
      <span className="rounded-lg border border-white/10 px-2.5 py-1.5 text-[10px] font-bold text-slate-300">
        Само анализ
      </span>
    </header>
    <div className="grid grid-cols-2 gap-2 p-3 sm:grid-cols-3">
      <div className="rounded-xl border border-white/[0.07] p-3">
        <div className="text-[10px] text-slate-500">Разгледани записи</div>
        <div className="mt-1 text-xl font-black text-white">{report.sample_total}</div>
      </div>
      <div className="rounded-xl border border-white/[0.07] p-3">
        <div className="text-[10px] text-slate-500">Липсващи входни доказателства</div>
        <div className="mt-1 text-xl font-black text-amber-200">{report.evidence_missing}</div>
      </div>
      <div className="col-span-2 rounded-xl border border-white/[0.07] p-3 sm:col-span-1">
        <div className="text-[10px] text-slate-500">Непотвърдени ценови crosschecks</div>
        <div className="mt-1 text-xl font-black text-amber-200">{report.unverified_price_crosschecks}</div>
      </div>
    </div>
    <div className="overflow-x-auto px-3 pb-3">
      <table className="w-full min-w-[680px] text-left text-[10px]">
        <thead className="border-b border-white/[0.08] text-[9px] font-bold uppercase text-slate-500">
          <tr>
            <th className="px-3 py-2">Модел</th>
            <th className="px-3 py-2">Включени</th>
            <th className="px-3 py-2">Изключени</th>
            <th className="px-3 py-2">Премахнати печалби/загуби</th>
            <th className="px-3 py-2">Печеливши в подгрупата</th>
            <th className="px-3 py-2">PnL на подгрупата</th>
          </tr>
        </thead>
        <tbody>
          {report.results.map(row => <tr key={row.mode} className="border-b border-white/[0.05]">
            <td className="px-3 py-3">
              <div className="font-black text-white">{LABELS[row.mode].name}</div>
              <div className="mt-1 max-w-[195px] text-[9px] leading-4 text-slate-500">{LABELS[row.mode].detail}</div>
            </td>
            <td className="px-3 py-3 text-slate-200">{row.accepted}</td>
            <td className="px-3 py-3 text-slate-200">{row.rejected}</td>
            <td className="px-3 py-3 text-slate-300">{row.removed_wins} / {row.removed_losses}</td>
            <td className="px-3 py-3 font-bold text-slate-200">{row.accepted_win_pct == null ? '—' : row.accepted_win_pct.toFixed(1) + '%'}</td>
            <td className={'px-3 py-3 font-black ' + (row.accepted_net_usd < 0 ? 'text-red-300' : 'text-cyan-200')}>
              {money(row.accepted_net_usd)}
            </td>
          </tr>)}
        </tbody>
      </table>
    </div>
    {report.results.map(row => row.mode === 'cost_and_pool' && Object.keys(row.reasons).length > 0 &&
      <div key={row.mode} className="border-t border-white/[0.06] px-4 py-3">
        <div className="mb-2 flex items-center gap-1.5 text-[10px] font-bold text-slate-300">
          <BookOpenCheck className="h-3.5 w-3.5"/> Причини за изключване от изследователската подгрупа
        </div>
        <div className="flex flex-wrap gap-2">
          {Object.entries(row.reasons).sort((a,b)=>b[1]-a[1]).map(([reason,n])=>
            <div key={reason} className="rounded-lg border border-white/[0.07] bg-white/[0.02] px-2.5 py-1.5 text-[10px] text-slate-400">
              {REASONS[reason] || reason} <b className="ml-1 text-white">{n}</b>
            </div>)}
        </div>
      </div>)}
    <div className="flex items-start gap-2 border-t border-amber-400/10 bg-amber-400/[0.025] px-4 py-3">
      <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-amber-200"/>
      <div className="text-[10px] leading-5 text-amber-200/80">
        <b>Това не е backtest на нова стратегия и не доказва доходност.</b>
        {' '}Изключването на исторически сделки променя следващите възможности.
        Не се симулират пропуснати входове, реални изпълнения или нови пазарни условия.
        За оценка са нужни последващи, независимо събрани PAPER наблюдения.
      </div>
    </div>
    <div className="flex items-center gap-1.5 border-t border-white/[0.05] px-4 py-2 text-[9px] text-slate-600">
      <AlertTriangle className="h-3 w-3" /> {PAPER_CHALLENGER_VERSION} · без промяна на основния робот
    </div>
  </section>;
}
