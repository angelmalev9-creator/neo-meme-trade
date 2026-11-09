import { useMemo } from 'react';
import { AlertTriangle, ArrowDownRight, ArrowUpRight, FileSearch } from 'lucide-react';

export type AuditedPaperTrade = {
  id?: string;
  symbol?: string;
  closed_at?: number;
  pnl_pct?: number;
  pnl_usd?: number;
  exit_reason?: string;
  stop_loss_pct?: number;
  exit_policy?: string;
};

const dollar = (amount: number) => '$' + Math.abs(amount).toFixed(2);
const signedDollar = (amount: number) => (amount > 0 ? '+' : amount < 0 ? '−' : '') + dollar(amount);
const signedPercent = (amount: number) => (amount > 0 ? '+' : amount < 0 ? '−' : '') + Math.abs(amount).toFixed(2) + '%';

const LEGACY_EARLY_EXITS = new Set([
  'CONVICTION_EXIT', 'ORDERFLOW_EXIT', 'CONVICTION_PROFIT_LOCK',
  'ADAPTIVE_TRAILING', 'ADAPTIVE_MAX_HOLD', 'ABSOLUTE_MAX_HOLD',
  'EXIT_IMPACT_EMERGENCY', 'LIQUIDITY_EMERGENCY',
]);

function classify(trades: AuditedPaperTrade[], defaultStopPct: number) {
  const rows = trades.filter(t =>
    typeof t.closed_at === 'number' && Number.isFinite(t.closed_at) &&
    typeof t.pnl_pct === 'number' && Number.isFinite(t.pnl_pct) &&
    typeof t.pnl_usd === 'number' && Number.isFinite(t.pnl_usd))
    .sort((a, b) => (b.closed_at || 0) - (a.closed_at || 0)).slice(0, 100);
  const wins = rows.filter(t => (t.pnl_usd || 0) > 0);
  const losses = rows.filter(t => (t.pnl_usd || 0) < 0);
  const excess = rows.filter(t => {
    const stop = t.stop_loss_pct && Number.isFinite(t.stop_loss_pct)
      ? Math.abs(t.stop_loss_pct) : Math.abs(defaultStopPct);
    return stop > 0 && (t.pnl_pct || 0) < -stop - 0.25;
  });
  const smallWins = wins.filter(t => (t.pnl_pct || 0) < 3);
  const reasons = rows.reduce<Record<string, number>>((result, trade) => {
    const reason = trade.exit_reason || 'UNKNOWN';
    result[reason] = (result[reason] || 0) + 1;
    return result;
  }, {});
  const net = rows.reduce((sum, t) => sum + (t.pnl_usd || 0), 0);
  const avgWin = wins.length ? wins.reduce((sum, t) => sum + (t.pnl_usd || 0), 0) / wins.length : 0;
  const avgLoss = losses.length ? losses.reduce((sum, t) => sum + (t.pnl_usd || 0), 0) / losses.length : 0;
  const earlyExitWins = wins.filter(t => LEGACY_EARLY_EXITS.has(t.exit_reason || '')).length;
  return { rows, wins, losses, excess, smallWins, reasons, net, avgWin, avgLoss, earlyExitWins };
}

export default function StopProfitAuditPanel({
  history, stopPct, currentProfile,
}: {
  history: AuditedPaperTrade[];
  stopPct: number;
  currentProfile?: string;
}) {
  const audit = useMemo(() => classify(history, stopPct), [history, stopPct]);
  const reasons = Object.entries(audit.reasons).sort((a,b) => b[1] - a[1]).slice(0,7);
  return <section data-testid="paper-exit-integrity-panel" className="mt-4 overflow-hidden rounded-2xl border border-amber-400/15 bg-[#0b0e11]">
    <div className="flex flex-wrap items-center justify-between gap-2 border-b border-white/[0.07] p-4">
      <div>
        <div className="flex items-center gap-1.5 text-[9px] font-bold uppercase tracking-[0.14em] text-amber-200/90">
          <FileSearch className="h-3.5 w-3.5" /> PAPER диагностика · само за четене
        </div>
        <h3 className="mt-1 text-sm font-black text-white">Защо стопът се надхвърля и печалбите са малки</h3>
        <p className="mt-1 text-[10px] text-slate-500">{currentProfile || 'Текущ профил'} · последните до 100 затворени записа</p>
      </div>
      <span className="rounded-lg border border-white/[0.08] px-2.5 py-1 text-[10px] text-slate-400">{audit.rows.length} проверени</span>
    </div>
    {audit.rows.length === 0 ? <p className="p-4 text-xs text-slate-400">Все още няма достатъчно приключени PAPER сделки за оценка.</p> : <>
      <div className="grid grid-cols-2 gap-2 p-3 sm:grid-cols-4">
        <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-3">
          <div className="text-[10px] text-slate-500">Записан нетен PnL</div>
          <div className={'mt-1 text-lg font-black ' + (audit.net < 0 ? 'text-red-300' : 'text-white')}>{signedDollar(audit.net)}</div>
          <div className="text-[9px] text-slate-600">{audit.wins.length} печеливши / {audit.losses.length} губещи</div>
        </div>
        <div className="rounded-xl border border-red-500/10 bg-red-500/[0.025] p-3">
          <div className="text-[10px] text-slate-500">Под записания стоп</div>
          <div className="mt-1 text-lg font-black text-red-300">{audit.excess.length}</div>
          <div className="text-[9px] text-slate-600">С над 0,25 процентни пункта</div>
        </div>
        <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-3">
          <div className="text-[10px] text-slate-500">Печалби под +3% net</div>
          <div className="mt-1 text-lg font-black text-white">{audit.smallWins.length} / {audit.wins.length}</div>
          <div className="text-[9px] text-slate-600">{audit.earlyExitWins} печеливши при ранни изходи</div>
        </div>
        <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-3">
          <div className="text-[10px] text-slate-500">Средна печалба / загуба</div>
          <div className="mt-1 flex items-center gap-1 text-xs font-bold">
            <ArrowUpRight className="h-3 w-3 text-cyan-300" /><span className="text-cyan-200">{signedDollar(audit.avgWin)}</span>
            <ArrowDownRight className="ml-1 h-3 w-3 text-red-300" /><span className="text-red-300">{signedDollar(audit.avgLoss)}</span>
          </div>
          <div className="mt-1 text-[9px] text-slate-600">Само от показаната история</div>
        </div>
      </div>
      {reasons.length > 0 && <div className="border-t border-white/[0.05] px-4 py-3">
        <div className="text-[10px] font-bold text-slate-300">Най-чести причини за затваряне</div>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {reasons.map(([reason,n]) => <span key={reason} className="rounded-lg border border-white/[0.07] bg-white/[0.025] px-2 py-1 text-[10px] text-slate-400">{reason} <b className="text-white">{n}</b></span>)}
        </div>
      </div>}
      {audit.excess.length > 0 && <details className="border-t border-white/[0.05] px-4 py-3">
        <summary className="cursor-pointer text-[11px] font-bold text-amber-200">Покажи сделките, приключили под стопа ({audit.excess.length})</summary>
        <div className="mt-3 max-h-60 overflow-auto">
          <table className="w-full min-w-[420px] text-left text-[10px]">
            <thead className="text-slate-500"><tr><th className="py-1">Монета</th><th>Записан стоп</th><th>Реален PAPER PnL</th><th>Причина</th></tr></thead>
            <tbody>{audit.excess.slice(0,25).map((t,i) => <tr key={t.id || i} className="border-t border-white/[0.05] text-slate-400">
              <td className="py-2 font-bold text-white">{t.symbol || '—'}</td>
              <td>−{(t.stop_loss_pct || stopPct).toFixed(2)}%</td>
              <td className="font-bold text-red-300">{signedPercent(t.pnl_pct || 0)}</td>
              <td>{t.exit_reason || 'UNKNOWN'}</td>
            </tr>)}</tbody>
          </table>
        </div>
      </details>}
      <div className="flex gap-2 border-t border-amber-300/10 bg-amber-400/[0.025] p-4 text-[10px] leading-5 text-amber-100/75">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
        Това е проверка на историческите записи, не доказателство за пропуснат ордер. PAPER стопът е условие за изход, не гарантирана крайна загуба. Източникът history може да съдържа по-стари версии или технически анулирани сделки; за окончателна сверка са нужни quote и audit логовете. Нито една настройка или сделка не се променя.
      </div>
    </>}
  </section>;
}
