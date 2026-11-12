import { useMemo } from 'react';
import { Activity, AlertTriangle, CircleHelp, Database, FlaskConical, ShieldAlert } from 'lucide-react';
import {
  VERIFIED_MOMENTUM_VERSION,
  summarizeVerifiedMomentum,
  type ResearchCoin, type ResearchCheck,
} from '../lib/verifiedMomentumResearch';

/**
 * Standalone live-feed observer. No order buttons, transaction calls, wallet
 * credentials, entry/exit thresholds or modification to trading services.
 */
export default function VerifiedMomentumPanel({
  coins, selectedAddress, onSelect,
}: {
  coins: ResearchCoin[];
  selectedAddress: string;
  onSelect: (mint: string) => void;
}) {
  const research = useMemo(() => summarizeVerifiedMomentum(coins), [coins]);
  const selected = research.results.find(row => row.mint === selectedAddress) || research.results[0];
  const preview = research.results.slice(0, 10);
  const displayStatus = (check: ResearchCheck) => check.status === 'observed' ? 'Наблюдавано'
    : check.status === 'attention' ? 'Изисква преглед' : 'Непотвърдено';

  return <section data-testid="verified-momentum-research-v1"
    className="xl:col-span-2 overflow-hidden rounded-3xl border border-cyan-300/15 bg-[#0b0e11]">
    <div className="flex flex-wrap items-start justify-between gap-3 border-b border-white/[0.07] p-4">
      <div>
        <div className="flex items-center gap-2 text-[9px] font-black uppercase tracking-[0.16em] text-cyan-300">
          <FlaskConical className="h-4 w-4" /> Изследователски модул · PAPER · V1
        </div>
        <h2 className="mt-1 text-base font-black text-white">NEO Verified Momentum</h2>
        <p className="mt-1 max-w-2xl text-[11px] leading-5 text-slate-400">
          Проверява дали текущият meme coin фийд има достатъчно доказателства за надежден анализ.
          Наблюдавана активност не означава органичен интерес или прогноза за доходност.
        </p>
      </div>
      <span className="rounded-lg border border-white/10 bg-white/[0.03] px-2.5 py-1.5 text-[10px] font-bold text-slate-300">
        Без търговски сигнали и поръчки
      </span>
    </div>

    <div className="grid grid-cols-2 gap-2 border-b border-white/[0.06] p-3 sm:grid-cols-4">
      <div className="rounded-xl border border-white/[0.06] p-3">
        <div className="text-[10px] text-slate-500">Обработени от фийда</div>
        <div className="mt-1 text-xl font-black tabular-nums text-white">{research.count}</div>
      </div>
      <div className="rounded-xl border border-white/[0.06] p-3">
        <div className="text-[10px] text-slate-500">Свежи snapshots</div>
        <div className="mt-1 text-xl font-black tabular-nums text-cyan-200">{research.activeObservations}</div>
      </div>
      <div className="rounded-xl border border-amber-300/10 p-3">
        <div className="text-[10px] text-slate-500">Невалидни / остарели</div>
        <div className="mt-1 text-xl font-black tabular-nums text-amber-200">{research.invalidOrStale}</div>
      </div>
      <div className="rounded-xl border border-white/[0.06] p-3">
        <div className="text-[10px] text-slate-500">С непълна ключова проверка</div>
        <div className="mt-1 text-xl font-black tabular-nums text-white">{research.missingCritical}</div>
      </div>
    </div>

    <div className="grid gap-3 p-3 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
      <div className="min-w-0 rounded-2xl border border-white/[0.06]">
        <div className="border-b border-white/[0.06] p-3">
          <div className="text-[11px] font-bold text-slate-200">Текущи наблюдения</div>
          <div className="mt-1 text-[10px] text-slate-500">
            Първите до 10 токена от фийда · не са класирани по потенциал за печалба
          </div>
        </div>
        <div className="max-h-[344px] overflow-y-auto p-2">
          {preview.length === 0 && <div className="p-3 text-xs text-slate-500">Фийдът все още няма налични токени.</div>}
          {preview.map(row => <button key={row.mint + ':' + row.pair}
            type="button" onClick={() => onSelect(row.mint)}
            className={'mb-1 flex w-full items-center justify-between gap-2 rounded-xl border p-2.5 text-left transition ' +
              (selected?.mint === row.mint ? 'border-cyan-300/20 bg-cyan-300/[0.055]' :
                'border-white/[0.04] hover:border-white/10 hover:bg-white/[0.025]')}>
            <div className="min-w-0">
              <div className="truncate text-[11px] font-black text-white">{row.symbol || 'Без символ'}</div>
              <div className="mt-0.5 truncate text-[9px] text-slate-500">{row.statusText}</div>
            </div>
            <span className="shrink-0 rounded-lg border border-white/[0.06] px-2 py-1 text-[9px] text-slate-400">
              {row.observed} от {row.checks.length} наблюдавани
            </span>
          </button>)}
        </div>
      </div>

      <div className="min-w-0 rounded-2xl border border-white/[0.06]">
        <div className="border-b border-white/[0.06] p-3">
          <div className="flex items-center gap-2 text-[11px] font-bold text-slate-200">
            <Activity className="h-3.5 w-3.5 text-cyan-200" /> Доказателства {selected ? '· ' + selected.symbol : ''}
          </div>
          <div className="mt-1 text-[10px] text-slate-500">Непотвърдено не се третира като успешно премината проверка.</div>
        </div>
        <div className="max-h-[344px] space-y-1.5 overflow-y-auto p-2.5">
          {!selected && <div className="p-3 text-xs text-slate-500">Избери токен от наблюденията.</div>}
          {selected?.checks.map(check => <div key={check.id}
            className="flex items-start gap-2 rounded-xl border border-white/[0.04] bg-white/[0.015] p-2.5">
            {check.status === 'observed'
              ? <Database className="mt-0.5 h-4 w-4 shrink-0 text-cyan-300" />
              : check.status === 'attention'
                ? <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-amber-300" />
                : <CircleHelp className="mt-0.5 h-4 w-4 shrink-0 text-slate-500" />}
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap justify-between gap-1">
                <span className="text-[10px] font-bold text-white">{check.title}</span>
                <span className={'text-[9px] ' + (check.status === 'observed' ? 'text-cyan-200' : 'text-amber-200')}>
                  {displayStatus(check)}
                </span>
              </div>
              <div className="mt-1 text-[10px] leading-4 text-slate-400">{check.description}</div>
            </div>
          </div>)}
        </div>
      </div>
    </div>

    <div className="flex items-start gap-2 border-t border-white/[0.06] px-4 py-3 text-[10px] leading-5 text-amber-200/80">
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
      <div>
        <span className="font-bold">Оценка на качеството на данните, не инвестиционен сигнал.</span>
        {' '}Не са налични доказани резултати от независим out-of-sample PAPER тест.
        {' '}Този модул не създава, променя или затваря сделки.
        <div className="mt-1 text-[9px] text-slate-600">Версия {VERIFIED_MOMENTUM_VERSION} · текущи данни от NEO market feed</div>
      </div>
    </div>
  </section>;
}
