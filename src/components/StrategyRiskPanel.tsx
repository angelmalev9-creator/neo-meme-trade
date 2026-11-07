import { useEffect, useMemo, useState } from 'react';
import { AlertCircle, Calculator, CheckCircle2, LockKeyhole, RotateCcw, ShieldCheck, SlidersHorizontal } from 'lucide-react';

const STORAGE_KEY = 'neo-paper-risk-scenario-v1';

type HoldMode = {
  mode: string;
  min_conviction: number;
  max_hold_minutes?: number;
  target_pct: number | null;
  trail_arm_pct: number;
  trail_pct: number;
};
type StrategyConfig = {
  strategy_profile?: string;
  strategy_version?: string;
  entry_policy_version?: string;
  trade_notional_usd: number;
  stop_loss_pct: number;
  stop_execution_buffer_pct?: number;
  stop_trigger_net_pct?: number;
  take_profit_pct: number;
  trailing_pct?: number;
  max_hold_minutes?: number | null;
  max_positions: number;
  max_daily_loss_usd: number;
  max_position_full_loss_risk_usd?: number;
  max_total_exposure_pct?: number;
  max_drawdown_pct?: number;
  scan_seconds: number;
  position_scan_seconds?: number;
  min_liquidity_usd?: number;
  entry_score?: number;
  reentry_seconds?: number;
  entry_flow_window_seconds?: number | null;
  execution_mode?: string;
  paper_only?: boolean;
  user_controls?: {
    enabled?: boolean;
    limits?: Record<string, [number, number]>;
  };
  adaptive_strategy?: {
    entry_limits?: Record<string, number>;
    hold_modes?: HoldMode[];
    config?: Record<string, number | boolean | string | null>;
  };
};
type StrategyStats = {
  demo_equity_usd: number;
  demo_reserved_usd?: number;
  demo_available_usd?: number;
  conservative_open_risk_usd?: number;
  drawdown_pct?: number;
  daily_risk_remaining_usd?: number | null;
  risk_day_pnl_usd?: number;
};
type Draft = {
  amount: string;
  stop: string;
  tp: string;
  risk: string;
  daily: string;
  exposure: string;
  drawdown: string;
};
type PaperCoreSettings = { trade_notional_usd: number; stop_loss_pct: number; take_profit_pct: number };

function initialDraft(c: StrategyConfig): Draft {
  const equity = Number.isFinite(c.trade_notional_usd) ? c.trade_notional_usd : 200;
  return {
    amount: String(equity),
    stop: String(c.stop_loss_pct),
    tp: String(c.take_profit_pct),
    risk: '1',
    daily: String(c.max_daily_loss_usd || 100),
    exposure: String(c.max_total_exposure_pct ?? 100),
    drawdown: String(c.max_drawdown_pct || 25),
  };
}

function NumericField({
  title, value, onChange, unit, min, max, step = '0.1',
}: {
  title: string; value: string; onChange: (next: string) => void;
  unit: string; min: number; max: number; step?: string;
}) {
  return <label className="block rounded-xl border border-white/[0.07] bg-black/20 p-3">
    <div className="mb-2 text-[10px] font-bold text-slate-400">{title}</div>
    <div className="flex items-center gap-2">
      <input type="number" inputMode="decimal" step={step} min={min} max={max}
        value={value} onChange={event => onChange(event.target.value)}
        className="min-w-0 w-full bg-transparent text-base font-black tabular-nums text-white outline-none focus:text-cyan-200" />
      <span className="text-[10px] text-slate-500">{unit}</span>
    </div>
    <div className="mt-1 text-[9px] text-slate-600">{min} – {max}</div>
  </label>;
}

const cash = (n: number) => Number.isFinite(n)
  ? '$' + n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  : '—';
const pct = (n: number | undefined | null) => typeof n === 'number' && Number.isFinite(n)
  ? n.toFixed(2) + '%' : '—';

function LiveMetric({ label, value, note, warning }: {
  label: string; value: string; note?: string; warning?: boolean;
}) {
  return <div className="rounded-xl border border-white/[0.07] bg-white/[0.02] p-3">
    <div className="text-[10px] font-semibold text-slate-500">{label}</div>
    <div className={'mt-1 text-lg font-black tabular-nums ' + (warning ? 'text-amber-200' : 'text-white')}>{value}</div>
    {note && <div className="mt-1 text-[10px] leading-4 text-slate-600">{note}</div>}
  </div>;
}

export default function StrategyRiskPanel({
  config, stats, onApplyPaper,
}: {
  config: StrategyConfig;
  stats: StrategyStats;
  onApplyPaper: (settings: PaperCoreSettings) => Promise<void>;
}) {
  const [draft, setDraft] = useState<Draft>(() => {
    const defaults = initialDraft(config);
    try {
      const cached = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null') as Partial<Draft> | null;
      if (cached && typeof cached === 'object') {
        return Object.fromEntries((Object.keys(defaults) as (keyof Draft)[])
          .map(key => [key, typeof cached[key] === 'string' ? cached[key] : defaults[key]])) as Draft;
      }
    } catch { /* browser persistence is optional */ }
    return defaults;
  });
  const [savedLocally, setSavedLocally] = useState(false);
  const [applying, setApplying] = useState(false);
  const [feedback, setFeedback] = useState('');
  const [dirty, setDirty] = useState(false);
  const controlsEnabled = !!config.user_controls?.enabled && !!config.paper_only;
  const equity = Math.max(0, stats.demo_equity_usd || 0);
  const positionSize = Math.max(0, config.trade_notional_usd || 0);
  const stopBuffer = Math.max(0, config.stop_execution_buffer_pct || 0);
  const plannedStopUsd = positionSize * (config.stop_loss_pct + stopBuffer) / 100;
  const plannedRiskPct = equity > 0 ? 100 * plannedStopUsd / equity : 0;
  const fullLossPct = equity > 0 ? 100 * positionSize / equity : 0;
  const dd = stats.drawdown_pct ?? 0;
  const effectiveRisk = typeof stats.daily_risk_remaining_usd === 'number'
    ? stats.daily_risk_remaining_usd : null;

  const fields = useMemo(() => {
    const values = Object.fromEntries(Object.entries(draft)
      .map(([k, v]) => [k, v.trim() !== '' ? Number(v) : NaN])) as Record<keyof Draft, number>;
    const bounds: Record<keyof Draft, [number, number]> = {
      amount: [10, Math.max(10, Math.min(positionSize, 5000))],
      stop: [0.5, Math.max(0.5, config.stop_loss_pct)],
      tp: [0.5, 200],
      risk: [0.1, 2],
      daily: [1, Math.max(1, config.max_daily_loss_usd)],
      exposure: [1, Math.max(1, config.max_total_exposure_pct ?? 100)],
      drawdown: [1, 50],
    };
    const violations = (Object.keys(values) as (keyof Draft)[])
      .filter(key => !Number.isFinite(values[key]) || values[key] < bounds[key][0] || values[key] > bounds[key][1]);
    return { values, bounds, violations };
  }, [draft, positionSize, config.stop_loss_pct, config.max_daily_loss_usd, config.max_total_exposure_pct]);
  const draftStopCost = fields.values.amount * (fields.values.stop + stopBuffer) / 100;
  const budget = equity * fields.values.risk / 100;
  const riskWithinBudget = draftStopCost <= budget + 1e-9;
  const scenarioDaily = Math.max(0, Math.min(fields.values.daily, config.max_daily_loss_usd));
  const scenarioDailyRemaining = scenarioDaily + (stats.risk_day_pnl_usd ?? 0);
  const scenarioExposureUsd = equity * fields.values.exposure / 100;
  const scenarioWouldPause = scenarioDailyRemaining <= 0 || dd >= fields.values.drawdown;
  const riskStatus = !Number.isFinite(draftStopCost) ? 'Невалидни стойности'
    : !riskWithinBudget ? 'Планираният риск надвишава сценарийния лимит'
      : scenarioWouldPause ? 'Сценарият би спрял новите входове' : 'В рамките на сценарийните лимити';

  useEffect(() => {
    if (!dirty && config.user_controls?.enabled) {
      setDraft(previous => ({ ...previous, amount: String(config.trade_notional_usd),
        stop: String(config.stop_loss_pct), tp: String(config.take_profit_pct) }));
    }
  }, [config.trade_notional_usd, config.stop_loss_pct, config.take_profit_pct, config.user_controls?.enabled, dirty]);

  const setField = (key: keyof Draft, value: string) => {
    setDraft(before => ({ ...before, [key]: value }));
    setSavedLocally(false);
    setDirty(true);
    setFeedback('');
  };
  const saveScenario = () => {
    if (fields.violations.length) {
      setFeedback('Провери стойностите извън посочените граници.');
      return;
    }
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(draft));
      setSavedLocally(true);
      setDirty(false);
      setFeedback('Сценарият е записан само в този браузър. Не е изпратен към бота.');
    } catch {
      setFeedback('Браузърът не разрешава локално запазване.');
    }
  };
  const resetScenario = () => {
    try { localStorage.removeItem(STORAGE_KEY); } catch { /* optional */ }
    setDraft(initialDraft(config));
    setDirty(false);
    setSavedLocally(false);
    setFeedback('Върнати са текущите PAPER стойности.');
  };
  const applySupported = async () => {
    if (!controlsEnabled || fields.violations.length) return;
    const limits = config.user_controls?.limits;
    const entries: [keyof PaperCoreSettings, number][] = [
      ['trade_notional_usd', fields.values.amount],
      ['stop_loss_pct', fields.values.stop],
      ['take_profit_pct', fields.values.tp],
    ];
    if (entries.some(([key, value]) => {
      const [lower, upper] = limits?.[key] || [0, Infinity];
      return value < lower || value > upper;
    })) {
      setFeedback('Една от стойностите е извън границите, разрешени от backend-а.');
      return;
    }
    setApplying(true);
    setFeedback('');
    try {
      await onApplyPaper(Object.fromEntries(entries) as PaperCoreSettings);
      setDirty(false);
      setFeedback('Трите поддържани PAPER параметъра са записани в backend-а. Другите полета остават само сценарий.');
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : 'Неуспешно записване.');
    } finally {
      setApplying(false);
    }
  };

  return <section data-testid="strategy-risk-panel" className="mt-4 overflow-hidden rounded-3xl border border-cyan-300/15 bg-[#0b0e11]">
    <div className="flex flex-wrap items-center justify-between gap-3 border-b border-white/[0.06] px-4 py-4">
      <div>
        <div className="flex items-center gap-2 text-[9px] font-bold uppercase tracking-[0.16em] text-cyan-300/80">
          <ShieldCheck className="h-3.5 w-3.5" /> Контролен център · PAPER
        </div>
        <h2 className="mt-1 text-base font-black text-white">Риск и параметри на стратегията</h2>
        <div className="mt-1 text-[10px] text-slate-500">
          {config.strategy_profile || '—'} · {config.strategy_version || '—'}
        </div>
      </div>
      <span className="flex items-center gap-1.5 rounded-lg border border-amber-400/15 bg-amber-400/[0.06] px-2.5 py-1.5 text-[10px] font-bold text-amber-200">
        <LockKeyhole className="h-3.5 w-3.5" /> {controlsEnabled ? '3 PAPER параметъра се редактират' : 'Основната стратегия е заключена'}
      </span>
    </div>

    <div className="p-4">
      <div className="mb-3 text-[10px] font-bold uppercase tracking-[0.12em] text-slate-500">Реално активни параметри от backend-а</div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-4">
        <LiveMetric label="Капитал на сделка" value={cash(positionSize)} />
        <LiveMetric label="Stop loss · net" value={pct(config.stop_loss_pct)} note="Тригер, не гарантиран максимален спад" />
        <LiveMetric label="Базов Take profit" value={pct(config.take_profit_pct)} note="Адаптивните режими може да имат други цели" />
        <LiveMetric label="Trailing" value={pct(config.trailing_pct)} />
        <LiveMetric label="Планиран риск при стоп" value={cash(plannedStopUsd)}
          note={pct(plannedRiskPct) + ' от PAPER equity; включва конфигуриран буфер, без реалните такси'} warning />
        <LiveMetric label="Пълен риск на капитала" value={cash(positionSize)}
          note={pct(fullLossPct) + ' от equity, без допълнителни такси'} warning />
        <LiveMetric label="Дневен лимит" value={cash(config.max_daily_loss_usd)}
          note={'Остава ' + (effectiveRisk == null ? '—' : cash(effectiveRisk))} />
        <LiveMetric label="Текущ drawdown" value={pct(dd)}
          note={'Активен лимит: ' + (config.max_drawdown_pct ? pct(config.max_drawdown_pct) : 'няма')} warning={dd > 10} />
        <LiveMetric label="Максимум позиции" value={String(config.max_positions)} />
        <LiveMetric label="Макс. експозиция" value={pct(config.max_total_exposure_pct)} />
        <LiveMetric label="Пълен риск на позиция · лимит" value={cash(config.max_position_full_loss_risk_usd ?? 0)} />
        <LiveMetric label="Сканиране" value={config.scan_seconds + 's'}
          note={'Позиции: ' + (config.position_scan_seconds ?? 0) + 's'} />
      </div>

      <details className="mt-3 rounded-xl border border-white/[0.06] bg-white/[0.02] p-3 text-xs">
        <summary className="cursor-pointer font-bold text-slate-300">Всички базови критерии за вход и задържане</summary>
        <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {([
            ['Минимален score', String(config.entry_score ?? config.adaptive_strategy?.entry_limits?.min_score ?? '—')],
            ['Минимална ликвидност', cash(config.min_liquidity_usd ?? 0)],
            ['Минимална conviction', String(config.adaptive_strategy?.entry_limits?.min_conviction ?? '—')],
            ['Order flow прозорец', String(config.entry_flow_window_seconds ?? '—') + ' сек.'],
            ['Мин. on-chain сделки', String(config.adaptive_strategy?.entry_limits?.min_flow_trades ?? '—')],
            ['Мин. flow ratio', String(config.adaptive_strategy?.entry_limits?.min_flow_buy_sell_usd_ratio ?? '—')],
            ['Мин. уникални портфейли', String(config.adaptive_strategy?.entry_limits?.min_unique_wallets ?? '—')],
            ['Допустим price impact', pct(config.adaptive_strategy?.config?.max_entry_impact_pct as number | undefined)],
            ['Max roundtrip cost', pct(config.adaptive_strategy?.config?.max_roundtrip_cost_pct as number | undefined)],
            ['Cooldown', String(config.reentry_seconds ?? '—') + ' сек.'],
            ['Базово max hold', config.max_hold_minutes == null ? 'Без базова стойност' : config.max_hold_minutes + ' мин.'],
          ] as [string,string][]).map(([label, value]) => <div key={label} className="flex justify-between gap-2 border-b border-white/[0.05] py-2">
            <span className="text-[10px] text-slate-500">{label}</span>
            <span className="text-[10px] font-bold text-slate-200">{value}</span>
          </div>)}
        </div>
        {!!config.adaptive_strategy?.hold_modes?.length && <div className="mt-3">
          <div className="mb-2 font-bold text-slate-300">Адаптивни режими</div>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[480px] text-[10px]"><thead className="text-slate-500">
              <tr><th className="py-2 text-left">Режим</th><th>Conviction</th><th>Цел</th><th>Trail arm</th><th>Trail</th><th>Hold</th></tr>
            </thead><tbody>
            {config.adaptive_strategy.hold_modes.map(mode => <tr key={mode.mode} className="border-t border-white/[0.05] text-center">
              <td className="py-2 text-left font-bold text-white">{mode.mode}</td>
              <td>{mode.min_conviction}</td><td>{mode.target_pct == null ? 'динамична' : mode.target_pct + '%'}</td>
              <td>{mode.trail_arm_pct}%</td><td>{mode.trail_pct}%</td>
              <td>{mode.max_hold_minutes == null ? '—' : mode.max_hold_minutes + ' мин.'}</td>
            </tr>)}
            </tbody></table>
          </div>
        </div>}
      </details>
    </div>

    <div className="border-t border-white/[0.07] bg-cyan-400/[0.025] p-4">
      <div className="flex flex-wrap items-center gap-2">
        <SlidersHorizontal className="h-4 w-4 text-cyan-200" />
        <h3 className="text-sm font-black text-white">Редактор на PAPER параметри</h3>
        <span className="rounded-lg border border-white/10 px-2 py-1 text-[9px] text-slate-400">What-if сценарий</span>
      </div>
      <p className="mt-2 text-[11px] leading-5 text-slate-400">
        Редактирай полетата и виж как би се променил планираният риск. Запазеният сценарий се пази в браузъра.
        {controlsEnabled ? ' Само капиталът, стопът и целта могат отделно да се изпратят към активния PAPER backend.' :
          ' При този заключен профил не се разрешава директна промяна на активната стратегия.'}
      </p>
      <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        <NumericField title="Капитал на позиция" value={draft.amount} unit="$" step="1"
          min={fields.bounds.amount[0]} max={fields.bounds.amount[1]} onChange={x => setField('amount',x)} />
        <NumericField title="Stop loss" value={draft.stop} unit="%"
          min={fields.bounds.stop[0]} max={fields.bounds.stop[1]} onChange={x => setField('stop',x)} />
        <NumericField title="Take profit" value={draft.tp} unit="%"
          min={fields.bounds.tp[0]} max={fields.bounds.tp[1]} onChange={x => setField('tp',x)} />
        <NumericField title="Планиран риск / equity" value={draft.risk} unit="%"
          min={fields.bounds.risk[0]} max={fields.bounds.risk[1]} onChange={x => setField('risk',x)} />
        <NumericField title="Дневен лимит на загуба" value={draft.daily} unit="$" step="1"
          min={fields.bounds.daily[0]} max={fields.bounds.daily[1]} onChange={x => setField('daily',x)} />
        <NumericField title="Максимална експозиция" value={draft.exposure} unit="%"
          min={fields.bounds.exposure[0]} max={fields.bounds.exposure[1]} onChange={x => setField('exposure',x)} />
        <NumericField title="Drawdown лимит" value={draft.drawdown} unit="%"
          min={fields.bounds.drawdown[0]} max={fields.bounds.drawdown[1]} onChange={x => setField('drawdown',x)} />
        <div className="rounded-xl border border-cyan-400/10 bg-cyan-400/[0.035] p-3">
          <div className="flex items-center gap-1.5 text-[10px] font-bold text-cyan-200"><Calculator className="h-3.5 w-3.5"/> Сценариен риск</div>
          <div className="mt-2 text-xl font-black tabular-nums text-white">{cash(draftStopCost)}</div>
          <div className="mt-1 text-[10px] text-slate-500">Лимит {cash(budget)}</div>
          <div className={'mt-1 text-[10px] leading-4 ' + (riskWithinBudget && !scenarioWouldPause ? 'text-cyan-200' : 'text-amber-200')}>{riskStatus}</div>
        </div>
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        <button onClick={saveScenario} disabled={fields.violations.length > 0}
          className="rounded-xl border border-cyan-300/20 bg-cyan-400/10 px-4 py-2.5 text-[11px] font-bold text-cyan-200 hover:bg-cyan-400/15 disabled:opacity-40">
          {savedLocally ? <CheckCircle2 className="mr-1 inline h-3.5 w-3.5" /> : null}Запази PAPER сценарий
        </button>
        {controlsEnabled && <button onClick={applySupported} disabled={applying || fields.violations.length > 0}
          className="rounded-xl border border-emerald-400/20 bg-emerald-400/10 px-4 py-2.5 text-[11px] font-bold text-emerald-200 disabled:opacity-40">
          {applying ? 'Прилагам...' : 'Приложи 3-те разрешени настройки към PAPER'}
        </button>}
        <button onClick={resetScenario} className="inline-flex items-center gap-1.5 rounded-xl border border-white/10 px-4 py-2.5 text-[11px] font-bold text-slate-400 hover:text-white">
          <RotateCcw className="h-3.5 w-3.5"/> Върни текущите
        </button>
      </div>
      {feedback && <div className="mt-2 text-[10px] text-slate-300">{feedback}</div>}
      <div className="mt-3 flex items-start gap-2 text-[10px] leading-5 text-amber-200/80">
        <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        Сценарият не променя работещия бот. Изчисленията са ориентировъчни, без гарантирани стопове или печалби.
        Реалният риск зависи и от ликвидността, таксите, market impact и невъзможност за продажба.
      </div>
      <div className="mt-1 text-[9px] text-slate-600">
        Сценарийна максимална експозиция: {cash(scenarioExposureUsd)} · оставащ дневен лимит: {cash(Math.max(0,scenarioDailyRemaining))}
      </div>
    </div>
  </section>;
