import { useEffect, useState } from 'react';
import { Activity, AlertCircle, CalendarDays, CheckCircle2, FlaskConical, History, LockKeyhole, RefreshCw, ShieldCheck } from 'lucide-react';

export type ComparisonPeriod = 'today' | 'since_restore';
type CaseId = 'ORIGINAL_OBSERVED' | 'COST_CAP_2PCT' | 'POOL_LOSS_BRAKE' | 'BOTH_SHADOW_FILTERS';

type PaperCase = {
  observed_entries: number;
  observed_wins: number;
  observed_losses: number;
  observed_recorded_pnl_usd: number;
  observed_excluded_entries: number;
};

export type ShadowComparisonReport = {
  version: string;
  period: ComparisonPeriod;
  observation_start: string;
  observation_end: string;
  timezone: string;
  generated_at: number;
  report_only: boolean;
  original_strategy_modified: boolean;
  source: string;
  cases: Record<CaseId, PaperCase>;
  rejection_counts: Record<string, Record<string, number>>;
  observed_rejected_entries: {
    trade_no: number | null;
    symbol: string | null;
    reasons: string[];
    recorded_pnl_usd: number;
  }[];
  assumptions: {
    pre_entry_roundtrip_cost_cap_pct: number;
    loss_brake_min_recent_losses: number;
    loss_brake_lookback_hours: number;
    loss_brake_reentry_hours: number;
  };
};

type Props = {
  fetchJson: (path: string) => Promise<ShadowComparisonReport>;
  activeProfile?: string;
};

const OPTIONS: { id: CaseId; name: string; caption: string; accent: string }[] = [
  { id: 'ORIGINAL_OBSERVED', name: 'Оригинал · 4 октомври', caption: 'Без допълнителни филтри', accent: 'border-slate-300/20' },
  { id: 'COST_CAP_2PCT', name: 'Филтър за разходи', caption: 'Максимум 2% прогнозен round-trip', accent: 'border-sky-400/25' },
  { id: 'POOL_LOSS_BRAKE', name: 'Защита от поредица загуби', caption: 'Два минуса · 3 часа пауза за същия пул', accent: 'border-amber-400/25' },
  { id: 'BOTH_SHADOW_FILTERS', name: 'Двата филтъра заедно', caption: 'Комбинирана изследователска проверка', accent: 'border-emerald-400/25' },
];

const REASON_NAMES: Record<string, string> = {
  PRE_ENTRY_ROUNDTRIP_ABOVE_CAP: 'Разход над 2%',
  UNVERIFIABLE_PRE_ENTRY_COST: 'Непотвърден разход',
  TWO_RECENT_LOSSES_SAME_POOL: 'Поредица загуби от същия пул',
  UNVERIFIABLE_TOKEN_ID: 'Неясен токен / пул',
};

const signedUsd = (value: number) => (value >= 0 ? '+' : '') + value.toFixed(2) + ' $';
const sofiaTime = (iso: string) => new Date(iso).toLocaleString('bg-BG', {
  timeZone: 'Europe/Sofia', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
});
const sofiaClock = (stamp: number) => new Date(stamp).toLocaleTimeString('bg-BG', {
  timeZone: 'Europe/Sofia', hour: '2-digit', minute: '2-digit', second: '2-digit',
});

export default function StrategyComparisonPanel({ fetchJson, activeProfile }: Props) {
  const [period, setPeriod] = useState<ComparisonPeriod>('since_restore');
  const [report, setReport] = useState<ShadowComparisonReport | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  const [showMore, setShowMore] = useState(false);

  useEffect(() => {
    let disposed = false;
    let inFlight = false;
    let poll: number | undefined;
    setReport(null);
    setError('');
    setLoading(true);

    const refresh = async () => {
      if (disposed || inFlight) return;
      inFlight = true;
      try {
        const data = await fetchJson('/user/shadow-comparison?period=' + period);
        if (disposed) return;
        if (!data.report_only || data.original_strategy_modified || !data.cases?.ORIGINAL_OBSERVED) {
          throw new Error('Получен е непълен или непроверен отчет.');
        }
        setReport(data);
        setError('');
      } catch (reason) {
        if (!disposed) setError(reason instanceof Error ? reason.message : 'Неуспешна заявка за сравнение');
      } finally {
        inFlight = false;
        if (!disposed) setLoading(false);
      }
    };
    void refresh();
    poll = window.setInterval(() => void refresh(), 15_000);
    return () => {
      disposed = true;
      if (poll) window.clearInterval(poll);
    };
  }, [fetchJson, period, revision]);

  const count = report?.cases.ORIGINAL_OBSERVED.observed_entries || 0;
  const rejects = report?.observed_rejected_entries || [];

  return <div className="space-y-4" data-testid="paper-strategy-comparison">
    <section className="overflow-hidden rounded-3xl border border-white/10 bg-[#0b0e11]">
      <div className="flex flex-wrap items-start justify-between gap-4 border-b border-white/[0.07] p-5 sm:p-6">
        <div className="max-w-2xl">
          <div className="flex items-center gap-2 text-[10px] font-black uppercase tracking-[0.16em] text-emerald-300">
            <FlaskConical className="h-4 w-4" /> Независим PAPER експеримент
          </div>
          <h1 className="mt-2 text-xl font-black tracking-tight text-white sm:text-2xl">Сравнение на стратегии</h1>
          <p className="mt-2 text-xs leading-5 text-slate-400">
            Една и съща история от реално наблюдавани PAPER входове, прегледана с оригиналните правила и два допълнителни филтъра. Няма отделни сделки, нов баланс или автоматично включване на филтрите.
          </p>
        </div>
        <div className="inline-flex items-center gap-2 rounded-xl border border-emerald-400/20 bg-emerald-400/[0.06] px-3 py-2 text-[10px] font-black text-emerald-200">
          <LockKeyhole className="h-4 w-4" /> Оригиналът е непокътнат
        </div>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3 px-5 py-4 sm:px-6">
        <div className="flex max-w-full items-center gap-1 overflow-x-auto rounded-xl border border-white/[0.08] bg-black/20 p-1">
          <button type="button" onClick={() => { setPeriod('today'); setShowMore(false); }} aria-pressed={period === 'today'}
            className={'shrink-0 rounded-lg px-3 py-2 text-[11px] font-bold transition ' + (period === 'today' ? 'bg-white/10 text-white' : 'text-slate-500 hover:text-slate-300')}>
            Днес · София
          </button>
          <button type="button" onClick={() => { setPeriod('since_restore'); setShowMore(false); }} aria-pressed={period === 'since_restore'}
            className={'shrink-0 rounded-lg px-3 py-2 text-[11px] font-bold transition ' + (period === 'since_restore' ? 'bg-white/10 text-white' : 'text-slate-500 hover:text-slate-300')}>
            След 05.11 · 12:08
          </button>
        </div>
        <div className="flex items-center gap-3 text-[10px] text-slate-500">
          {report && <span className="flex items-center gap-1"><Activity className="h-3.5 w-3.5" /> Обновено {sofiaClock(report.generated_at)}</span>}
          <button type="button" onClick={() => setRevision(value => value + 1)}
            className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-2 font-bold text-slate-300 hover:bg-white/[0.05]" aria-label="Обнови сравнението">
            <RefreshCw className={'h-3.5 w-3.5 ' + (loading ? 'animate-spin' : '')} /> Обнови
          </button>
        </div>
      </div>
    </section>

    {error && <div role="alert" className="flex items-start gap-2 rounded-2xl border border-amber-400/20 bg-amber-400/[0.06] p-4 text-xs text-amber-200">
      <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
      <span>Не успях да заредя актуалните данни: {error}. {report ? 'Последният успешен отчет остава видим.' : 'Проверете дали личният PAPER акаунт е достъпен.'}</span>
    </div>}

    {!report && <div className="rounded-3xl border border-white/[0.08] bg-[#0b0e11] p-8 text-center text-xs text-slate-500">
      {loading ? 'Зареждам сравнението от личния PAPER акаунт…' : 'Няма наличен отчет за този период.'}
    </div>}

    {report && <>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-[10px] text-slate-500">
        <span className="inline-flex items-center gap-1.5"><CalendarDays className="h-3.5 w-3.5" /> {sofiaTime(report.observation_start)} – {sofiaTime(report.observation_end)} · София</span>
        <span className="inline-flex items-center gap-1.5"><ShieldCheck className="h-3.5 w-3.5" /> {activeProfile || 'PAPER'} · само четене</span>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {OPTIONS.map((option, index) => {
          const item = report.cases[option.id];
          const keptShare = count ? Math.round(100 * item.observed_entries / count) : 0;
          return <article key={option.id} className={'rounded-2xl border bg-[#0b0e11] p-4 ' + option.accent}>
            <div className="flex items-center justify-between gap-2">
              <span className="text-[9px] font-black uppercase tracking-[0.12em] text-slate-500">{index === 0 ? 'Контролна група' : 'Тестов вариант ' + index}</span>
              {index === 0 ? <History className="h-4 w-4 text-slate-400" /> : <FlaskConical className="h-4 w-4 text-sky-300/75" />}
            </div>
            <h3 className="mt-2 min-h-10 text-sm font-bold leading-5 text-white">{option.name}</h3>
            <p className="mt-0.5 min-h-8 text-[10px] leading-4 text-slate-500">{option.caption}</p>
            <div className={'mt-3 text-[26px] font-black tabular-nums tracking-tight ' + (item.observed_recorded_pnl_usd < 0 ? 'text-red-300' : 'text-emerald-300')}>
              {signedUsd(item.observed_recorded_pnl_usd)}
            </div>
            <div className="mt-1 text-[10px] text-slate-500">Исторически резултат на допуснатите входове</div>
            <div className="mt-4 h-1.5 overflow-hidden rounded-full bg-white/[0.07]">
              <div className={'h-full rounded-full ' + (index === 0 ? 'bg-slate-400' : 'bg-sky-400/70')} style={{ width: keptShare + '%' }} />
            </div>
            <div className="mt-2 flex items-center justify-between text-[10px]">
              <span className="text-slate-300">{item.observed_entries} от {count} входа</span>
              <span className="text-slate-500">{item.observed_excluded_entries} изключени</span>
            </div>
            <div className="mt-2 text-[10px] text-slate-500">{item.observed_wins} печеливши · {item.observed_losses} губещи</div>
          </article>;
        })}
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
        <section className="rounded-3xl border border-white/[0.08] bg-[#0b0e11] p-4 sm:p-5">
          <div className="flex items-center gap-2 text-sm font-black text-white"><ShieldCheck className="h-4 w-4 text-sky-300" /> Как работят защитите</div>
          <div className="mt-4 space-y-3">
            <div className="rounded-xl border border-white/[0.06] bg-white/[0.025] p-3">
              <div className="flex items-center justify-between gap-2 text-xs font-bold text-slate-200"><span>Прогнозен разход за незабавна продажба</span><span className="text-sky-300">≤ {report.assumptions.pre_entry_roundtrip_cost_cap_pct}%</span></div>
              <p className="mt-1 text-[11px] leading-5 text-slate-500">Проверява записаната котировка преди входа. Не представлява платена такса или гаранция за бъдеща цена.</p>
            </div>
            <div className="rounded-xl border border-white/[0.06] bg-white/[0.025] p-3">
              <div className="flex flex-wrap items-center justify-between gap-2 text-xs font-bold text-slate-200"><span>Повторни загуби от същия пул</span><span className="text-amber-200">{report.assumptions.loss_brake_min_recent_losses} загуби · {report.assumptions.loss_brake_reentry_hours} ч.</span></div>
              <p className="mt-1 text-[11px] leading-5 text-slate-500">Използва само приключили сделки от същия токен и пул в рамките на {report.assumptions.loss_brake_lookback_hours} часа. Не бърка токени с еднакъв тикер.</p>
            </div>
          </div>
        </section>

        <section className="rounded-3xl border border-white/[0.08] bg-[#0b0e11] p-4 sm:p-5">
          <div className="flex items-center gap-2 text-sm font-black text-white"><History className="h-4 w-4 text-slate-300" /> Последни наблюдавани откази</div>
          <p className="mt-1 text-[11px] leading-5 text-slate-500">Това са исторически входове, които тестовият филтър би отхвърлил. Оригиналният бот е могъл да ги извърши.</p>
          <div className="mt-3 max-h-[370px] overflow-y-auto">
            {!rejects.length ? <div className="rounded-xl border border-white/[0.06] p-4 text-center text-[11px] text-slate-500">Няма отхвърлени входове за периода.</div> : (showMore ? [...rejects].reverse() : [...rejects].reverse().slice(0, 6)).map((trade, index) => <div key={String(trade.trade_no) + '-' + index} className="border-b border-white/[0.055] py-3 last:border-b-0">
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs font-bold text-white">#{trade.trade_no ?? '—'} · {trade.symbol || 'Неизвестен токен'}</span>
                <span className={'text-xs font-bold tabular-nums ' + (trade.recorded_pnl_usd < 0 ? 'text-red-300' : 'text-emerald-300')}>{signedUsd(trade.recorded_pnl_usd)}</span>
              </div>
              <div className="mt-1 flex flex-wrap gap-1.5">
                {trade.reasons.map(reason => <span key={reason} className="rounded-md border border-white/[0.07] bg-white/[0.035] px-1.5 py-1 text-[9px] text-slate-400">{REASON_NAMES[reason] || reason}</span>)}
              </div>
            </div>)}
          </div>
          {rejects.length > 6 && <button onClick={() => setShowMore(value => !value)} type="button" className="mt-3 rounded-lg border border-white/10 px-3 py-2 text-[10px] font-bold text-slate-300 hover:bg-white/[0.04]">{showMore ? 'Покажи по-малко' : 'Виж всички ' + rejects.length + ' отказа'}</button>}
        </section>
      </div>

      <div className="rounded-2xl border border-amber-400/15 bg-amber-400/[0.04] px-4 py-3 text-[11px] leading-5 text-amber-100/85">
        <div className="flex items-center gap-2 font-bold"><AlertCircle className="h-4 w-4" /> Важно за тълкуването</div>
        <p className="mt-1">Сравнението е върху вече наблюдавани PAPER сделки, а не върху четири независими търгуващи бота. Разликата между сумите не е реализирана или доказано избегната загуба. Нови входове, ликвидност, котировки и бъдещи печалби не са моделирани. Оригиналната стратегия и балансът не се променят.</p>
      </div>
      <div className="flex items-center gap-1.5 text-[10px] text-slate-600"><CheckCircle2 className="h-3.5 w-3.5" /> Само PAPER данни · автоматично обновяване на 15 секунди · без реални поръчки</div>
    </>}
  </div>;
}
