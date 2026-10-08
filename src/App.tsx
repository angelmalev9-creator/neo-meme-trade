import LabPairedPanel, { type LabPairedSnapshot } from './components/LabPairedPanel';
import PaperTrainingPanel, { type PaperTrainingSnapshot } from './components/PaperTrainingPanel';
import { Fragment, useEffect, useMemo, useState, type ReactNode } from 'react';
import {
  Activity, Bot, CircleDollarSign, ExternalLink, FlaskConical, Gauge, RefreshCw, Search,
  ShieldCheck, Sparkles, TrendingDown, TrendingUp, WalletCards, Zap,
} from 'lucide-react';
import { Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { supabase } from './lib/supabase';

const API = 'https://neo-meme-api.169-58-211-177.sslip.io';

type Signal = { kind: 'positive' | 'neutral' | 'risk'; title: string; detail: string };
type TxWindow = { buys: number; sells: number };
type Coin = {
  address: string; pairAddress: string; dexId: string; dexUrl: string;
  name: string; symbol: string; imageUrl: string; description: string;
  priceUsd: number; marketCap: number; fdv: number; liquidityUsd: number;
  volume: Record<'m5' | 'h1' | 'h6' | 'h24', number>;
  priceChange: Record<'m5' | 'h1' | 'h6' | 'h24', number>;
  txns: Record<'m5' | 'h1' | 'h6' | 'h24', TxWindow>;
  ageMinutes: number | null; sources: string[]; boostAmount: number;
  score: number; riskScore: number; posture: 'SETUP' | 'WATCH' | 'WAIT' | 'SKIP';
  signals: Signal[]; updatedAt: number;
};
type Position = {
  id: string; address: string; pairAddress: string; name: string; symbol: string;
  imageUrl: string; entry_price: number; current_price: number; peak_price: number;
  execution_entry_price?: number; execution_exit_price?: number; execution_verification_version?: string;
  notional_usd: number; score: number; current_score: number; opened_at: number;
  updated_at: number; pnl_pct: number; pnl_usd: number;
  why_entry: string[]; risks_at_entry: string[]; closed_at?: number;
  exit_price?: number; exit_reason?: string; trade_no?: number; session_id?: string;
  balance_before?: number; balance_after?: number; dex_url?: string;
};
type PricePoint = { ts: number; price: number; liquidity: number; volumeH1: number; score: number };
type LabPosition = { symbol: string; address: string; strategy_id: string; opened_at: number; pnl_pct: number; notional_usd: number };
type WhyQuiet = { since: number | null; scans: number; candidates: number; last_rule_match_at: number | null; reasons: { reason: string; count: number; share: number | null }[] } | null;
type LabBook = { id: string; name: string; starting_balance: number; balance: number; position: LabPosition | null; history: Position[]; why_quiet?: WhyQuiet };
type LabTrade = {
  trade_no: number | null; symbol: string | null; address: string | null; open: boolean;
  opened_at: number | null; closed_at: number | null; hold_seconds: number | null;
  entry_price: number | null; execution_entry_price: number | null;
  exit_price: number | null; execution_exit_price: number | null; current_price: number | null;
  notional_usd: number | null; pnl_usd: number | null; pnl_pct: number | null; exit_reason: string | null;
  observed_exit_pnl_pct?: number | null; pre_exit_pnl_pct?: number | null; pre_exit_gap_seconds?: number | null; exit_fill_model?: string | null;
};
type LabBookTrades = { found: boolean; id: string; name?: string; trades: LabTrade[]; total: number; shown: number };
type LabStats = { trades: number; wins: number; losses: number; win_rate: number; profit_factor: number | null; realized_pnl: number; equity: number; return_pct: number; open: boolean };
type StrategyLab = { paired?: LabPairedSnapshot; status: string; updated_at: number; started_at: number; books: Record<string, LabBook>; stats: Record<string, LabStats>; error?: string; activity_config?: { rejection_labels?: Record<string, string>; exit_overrides?: Record<string, { stop_loss?: number; take_profit?: number }> } };
type LiveTrade = { ts: number; direction: 'BUY' | 'SELL'; token_amount: number; usd_amount: number; wallet: string; note: string; address: string; pairAddress: string; symbol: string; signature: string; slot: number };
type FlowStats = { seconds: number; trades: number; buys: number; sells: number; buy_usd: number; sell_usd: number; buy_sell_usd_ratio: number; unique_wallets: number; max_buy_usd: number; max_sell_usd: number };
type TapeStatus = { status?: string; tracked_pairs?: number; updated_at?: number; source?: string; error?: string | null };
type EntryDiagnostics = {
  status?: string; message?: string; policy_version?: string; strategy?: string; checked_at?: number;
  candidates?: number; evaluated?: number; signal_passed?: number; quoted?: number; opened?: number; max_positions?: number;
  rejections?: Record<string, number>; reason_labels?: Record<string, string>;
  examples?: { symbol: string; reasons: string[]; metrics: Record<string, unknown> }[];
};
type MonitorState = {
  running: boolean; status: string; message: string; last_scan_at: number; scan_count: number;
  feed: Coin[]; positions: Position[]; history: Position[];
  events: { ts: number; text: string }[];
  live_tape: LiveTrade[]; live_tape_status: TapeStatus;
  strategy_lab: StrategyLab;
  paper_training?: PaperTrainingSnapshot;
  entry_diagnostics?: EntryDiagnostics;
  learning?: {
    mode: string; trades_used: number; loss_streak: number; loss_streak_brake: boolean; avoided: string[];
    best: { bucket: string; trades: number; mean_pct: number; win_rate: number; avoided: boolean }[];
    worst: { bucket: string; trades: number; mean_pct: number; win_rate: number; avoided: boolean }[];
  } | null;
  stats: { feed_count: number; open_positions: number; closed_trades: number; wins: number; win_rate: number; realized_today_usd: number; demo_starting_balance_usd: number; demo_balance_usd: number; demo_equity_usd: number; demo_available_usd: number; demo_reserved_usd: number; unrealized_pnl_usd: number; realized_total_usd: number; return_pct: number; demo_started_at: number; demo_session_id: string; metrics?: { lifetime?: { net_pnl_usd?: number } } };
  config: { signal_strategy?: string; strategy_profile?: string; entry_policy_version?: string; exit_policy?: string; scan_seconds: number; position_scan_seconds?: number; max_positions: number; stop_loss_pct: number; take_profit_pct: number; trade_notional_usd: number; max_daily_loss_usd: number; starting_balance_usd: number; paper_only?: boolean };
};
type TokenDetail = { coin: Coin; history: PricePoint[]; position: Position | null; trades: Position[]; live_tape?: LiveTrade[]; flow?: FlowStats };
type FlowWindow = { buys: number; sells: number; buyers: number; sellers: number; buy_usd?: number; sell_usd?: number; complete?: boolean };
type CoinFlow = {
  at: number; age_minutes: number | null; available_windows: string[];
  tape: { windows: Record<string, FlowWindow>; covered_seconds: number; events: number; status?: string; coverage?: string; updated_at?: number };
  gecko: { windows: Record<string, FlowWindow>; volume_usd?: Record<string, number>; price_change_pct?: Record<string, number>; fetched_at?: number; error?: string | null };
  dexscreener: Record<string, TxWindow>; longer_windows_note?: string;
};
type Filter = 'ALL' | 'SETUP' | 'WATCH' | 'NEW' | 'BOOSTED';
type Tab = 'engine' | 'lab' | 'coins';
const WINDOW_LABELS: Record<string, string> = { m1: '1м', m5: '5м', m15: '15м', m30: '30м', h1: '1ч', h6: '6ч', h24: '24ч' };

const fmtMoney = (value = 0) => value >= 1_000_000 ? `$${(value / 1_000_000).toFixed(2)}M` : value >= 1_000 ? `$${(value / 1_000).toFixed(1)}K` : `$${value.toFixed(0)}`;
const fmtPrice = (value = 0) => value >= 1 ? `$${value.toFixed(4)}` : value >= 0.01 ? `$${value.toFixed(6)}` : value >= 0.0001 ? `$${value.toFixed(8)}` : `$${value.toPrecision(5)}`;
const ageLabel = (minutes: number | null) => minutes == null ? '—' : minutes < 60 ? `${Math.round(minutes)}m` : minutes < 1440 ? `${(minutes / 60).toFixed(1)}h` : `${(minutes / 1440).toFixed(1)}d`;
const shortAddress = (value: string) => `${value.slice(0, 4)}…${value.slice(-4)}`;
const timeLabel = (stamp: number) => stamp ? new Date(stamp).toLocaleTimeString('bg-BG', { hour: '2-digit', minute: '2-digit' }) : '—';
const tapeTimeLabel = (stamp: number) => stamp ? new Date(stamp).toLocaleTimeString('bg-BG', { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '—';
const fullTimeLabel = (stamp: number) => stamp ? new Date(stamp).toLocaleString('bg-BG', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '—';
const holdLabel = (seconds: number | null) => {
  if (seconds == null) return '—';
  const total = Math.max(0, Math.round(seconds));
  const hours = Math.floor(total / 3600), minutes = Math.floor((total % 3600) / 60), rest = total % 60;
  return hours ? `${hours}ч ${minutes}м ${rest}с` : minutes ? `${minutes}м ${rest}с` : `${rest}с`;
};
const signed = (value: number, digits = 2) => `${value >= 0 ? '+' : ''}${value.toFixed(digits)}`;
const agoLabel = (stamp?: number | null) => {
  if (!stamp) return '—';
  const seconds = Math.max(0, Math.round((Date.now() - stamp) / 1000));
  return seconds < 60 ? `преди ${seconds}с` : seconds < 3600 ? `преди ${Math.floor(seconds / 60)}м` : `преди ${(seconds / 3600).toFixed(1)}ч`;
};

async function authedJson<T>(path: string): Promise<T> {
  if (!supabase) throw new Error('Supabase unavailable');
  const { data: { session } } = await supabase.auth.getSession();
  const token = session?.access_token;
  if (!token) throw new Error('Session expired');
  const response = await fetch(`${API}${path}`, { cache: 'no-store', headers: { Authorization: `Bearer ${token}` } });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json() as Promise<T>;
}

function ScoreBadge({ coin }: { coin: Coin }) {
  const cls = coin.posture === 'SETUP' ? 'border-emerald-400/25 bg-emerald-400/10 text-emerald-300' : coin.posture === 'WATCH' ? 'border-sky-400/20 bg-sky-400/10 text-sky-300' : coin.posture === 'WAIT' ? 'border-amber-400/20 bg-amber-400/10 text-amber-200' : 'border-red-400/20 bg-red-400/10 text-red-300';
  return <div className={`rounded-lg border px-2 py-1 text-[10px] font-black ${cls}`}>{coin.score.toFixed(0)} · {coin.posture}</div>;
}

function Change({ value, compact = false }: { value: number; compact?: boolean }) {
  const positive = value >= 0;
  return <span className={`inline-flex items-center gap-1 font-black ${compact ? 'text-[10px]' : 'text-sm'} ${positive ? 'text-emerald-300' : 'text-red-300'}`}>
    {positive ? <TrendingUp className="h-3 w-3" /> : <TrendingDown className="h-3 w-3" />}{positive ? '+' : ''}{value.toFixed(1)}%
  </span>;
}

function Metric({ label, value, hint, tone }: { label: string; value: string; hint?: string; tone?: 'up' | 'down' }) {
  return <div className="rounded-2xl border border-white/[0.07] bg-white/[0.025] p-3.5">
    <div className="text-[9px] font-black uppercase tracking-[0.16em] text-slate-600">{label}</div>
    <div className={`mt-1 text-lg font-black ${tone === 'up' ? 'text-emerald-300' : tone === 'down' ? 'text-red-300' : 'text-white'}`}>{value}</div>
    {hint && <div className="mt-1 text-[9px] text-slate-600">{hint}</div>}
  </div>;
}

function Card({ title, kicker, right, children, className = '' }: { title: string; kicker?: string; right?: ReactNode; children: ReactNode; className?: string }) {
  return <section className={`overflow-hidden rounded-3xl border border-white/10 bg-[#0b0e11] ${className}`}>
    <div className="flex items-center justify-between gap-3 border-b border-white/[0.07] px-4 py-3">
      <div>{kicker && <div className="text-[9px] font-black uppercase tracking-[0.16em] text-slate-600">{kicker}</div>}<h2 className="mt-0.5 text-sm font-black text-white">{title}</h2></div>
      {right}
    </div>
    {children}
  </section>;
}

function CoinAvatar({ coin, size = 'md' }: { coin: Coin; size?: 'sm' | 'md' | 'lg' }) {
  const dimensions = size === 'lg' ? 'h-14 w-14' : size === 'sm' ? 'h-8 w-8' : 'h-10 w-10';
  return coin.imageUrl ? <img src={coin.imageUrl} alt="" className={`${dimensions} shrink-0 rounded-xl border border-white/10 object-cover`} /> : <div className={`${dimensions} flex shrink-0 items-center justify-center rounded-xl border border-white/10 bg-white/[0.04] text-xs font-black text-emerald-300`}>{coin.symbol.slice(0, 2)}</div>;
}

function RejectionBars({ rows, total }: { rows: { key: string; label: string; count: number }[]; total: number }) {
  if (!rows.length) return <div className="text-[10px] text-slate-600">Няма отказани кандидати.</div>;
  const max = Math.max(...rows.map(r => r.count), 1);
  return <div className="space-y-1.5">{rows.map(row => <div key={row.key}>
    <div className="flex items-center justify-between gap-2 text-[10px]"><span className="truncate text-slate-300">{row.label}</span><span className="shrink-0 font-mono text-slate-500">{row.count}{total ? ` · ${Math.round(row.count / total * 100)}%` : ''}</span></div>
    <div className="mt-0.5 h-1 rounded-full bg-white/[0.05]"><div className="h-1 rounded-full bg-amber-300/70" style={{ width: `${Math.max(3, row.count / max * 100)}%` }} /></div>
  </div>)}</div>;
}

export default function App() {
  const [state, setState] = useState<MonitorState | null>(null);
  const [error, setError] = useState('');
  const [tab, setTab] = useState<Tab>(() => { try { return (sessionStorage.getItem('neo-tab') as Tab) || 'engine'; } catch { return 'engine'; } });
  const [selectedAddress, setSelectedAddress] = useState('');
  const [openBook, setOpenBook] = useState('');
  const [bookTrades, setBookTrades] = useState<LabBookTrades | null>(null);
  const [bookTradesError, setBookTradesError] = useState('');
  const [showQuietBooks, setShowQuietBooks] = useState(false);
  const [detail, setDetail] = useState<TokenDetail | null>(null);
  const [flow, setFlow] = useState<CoinFlow | null>(null);
  const [flowWindow, setFlowWindow] = useState('m5');
  const [filter, setFilter] = useState<Filter>('ALL');
  const [search, setSearch] = useState('');
  const [busy, setBusy] = useState(false);
  const [showAllHistory, setShowAllHistory] = useState(false);
  const [refreshTick, setRefreshTick] = useState(0);
  const startingBalance = state?.stats.demo_starting_balance_usd ?? 1000;
  const lifetimePnl = state?.stats.metrics?.lifetime?.net_pnl_usd;
  const allTimeBalance = startingBalance + (lifetimePnl ?? ((state?.stats.demo_balance_usd ?? startingBalance) - startingBalance));
  const allTimeReturnPct = ((allTimeBalance - startingBalance) / Math.max(startingBalance, 1)) * 100;

  const switchTab = (next: Tab) => { setTab(next); try { sessionStorage.setItem('neo-tab', next); } catch { /* optional */ } };
  const openCoin = (address: string) => { setSelectedAddress(address); switchTab('coins'); };

  useEffect(() => {
    let cancelled = false;
    let retryTimer: number | undefined;
    let pollTimer: number | undefined;
    let inFlight = false;
    let hasValidState = false;
    try {
      const cached = sessionStorage.getItem('neo-live-state-v1');
      if (cached) {
        const parsed = JSON.parse(cached) as { savedAt: number; state: MonitorState };
        if (Date.now() - parsed.savedAt < 15_000) { hasValidState = true; setState(parsed.state); setError(''); }
      }
    } catch { /* cache is optional */ }
    const schedulePoll = () => { if (!cancelled) pollTimer = window.setTimeout(() => void load(0), 2000); };
    const load = async (attempt = 0) => {
      if (cancelled || inFlight) return;
      inFlight = true;
      let retryScheduled = false;
      try {
        const next = await authedJson<MonitorState>('/user/state');
        if (cancelled) return;
        hasValidState = true;
        setState(next);
        setError('');
        try { sessionStorage.setItem('neo-live-state-v1', JSON.stringify({ savedAt: Date.now(), state: next })); } catch { /* optional */ }
        setSelectedAddress(current => current || next.feed[0]?.address || '');
      } catch (err) {
        if (cancelled) return;
        if (attempt < 3) { retryScheduled = true; retryTimer = window.setTimeout(() => void load(attempt + 1), [100, 250, 500][attempt]); }
        else if (!hasValidState) { const message = err instanceof Error ? err.message : 'Backend unavailable'; setError(message.includes('aborted') ? 'Backend unavailable' : message); }
      } finally { inFlight = false; if (!retryScheduled) schedulePoll(); }
    };
    void load();
    return () => { cancelled = true; if (retryTimer) window.clearTimeout(retryTimer); if (pollTimer) window.clearTimeout(pollTimer); };
  }, [refreshTick]);

  useEffect(() => {
    if (!selectedAddress || tab !== 'coins') return;
    let cancelled = false;
    const loadToken = async () => {
      try { const next = await authedJson<TokenDetail>(`/user/token?address=${encodeURIComponent(selectedAddress)}`); if (!cancelled) setDetail(next); } catch { /* feed still works */ }
      try { const next = await authedJson<CoinFlow>(`/user/coin-flow?address=${encodeURIComponent(selectedAddress)}`); if (!cancelled) setFlow(next); } catch { if (!cancelled) setFlow(null); }
    };
    void loadToken();
    const timer = window.setInterval(loadToken, 2000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [selectedAddress, tab]);

  useEffect(() => {
    setBookTrades(null);
    setBookTradesError('');
    if (!openBook) return;
    let cancelled = false;
    const loadBook = async () => {
      try { const next = await authedJson<LabBookTrades>(`/user/lab-book?id=${encodeURIComponent(openBook)}`); if (!cancelled) { setBookTrades(next); setBookTradesError(''); } }
      catch { if (!cancelled) setBookTradesError('Сделките не могат да се заредят в момента.'); }
    };
    void loadBook();
    const timer = window.setInterval(loadBook, 10000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [openBook]);

  const selectedCoin = useMemo(() => state?.feed.find(c => c.address === selectedAddress) || detail?.coin || state?.feed[0] || null, [state, selectedAddress, detail]);
  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return (state?.feed || []).filter(coin => {
      const matchesSearch = !q || coin.symbol.toLowerCase().includes(q) || coin.name.toLowerCase().includes(q) || coin.address.toLowerCase().includes(q);
      const matchesFilter = filter === 'ALL' || filter === coin.posture || (filter === 'NEW' && (coin.ageMinutes ?? 999999) < 60) || (filter === 'BOOSTED' && coin.sources.some(s => s.includes('boost')));
      return matchesSearch && matchesFilter;
    });
  }, [state, filter, search]);
  const chartData = useMemo(() => (detail?.history || []).map(p => ({ ...p, label: timeLabel(p.ts) })), [detail]);
  const diagnostics = state?.entry_diagnostics;
  const rejectionRows = useMemo(() => Object.entries(diagnostics?.rejections || {}).sort((a, b) => b[1] - a[1]).slice(0, 8).map(([key, count]) => ({ key, count, label: diagnostics?.reason_labels?.[key] || key })), [diagnostics]);
  const labBooks = useMemo(() => Object.values(state?.strategy_lab?.books || {}).filter(book => book.id !== 'ASTRA_6_BRAIN').sort((a, b) => (state?.strategy_lab?.stats?.[b.id]?.equity ?? b.balance) - (state?.strategy_lab?.stats?.[a.id]?.equity ?? a.balance)), [state]);
  const activeBooks = labBooks.filter(book => (state?.strategy_lab?.stats?.[book.id]?.trades ?? 0) > 0 || book.position);
  const quietBooks = labBooks.filter(book => !activeBooks.includes(book));
  const labLabels = state?.strategy_lab?.activity_config?.rejection_labels || {};
  const exitOverrides = state?.strategy_lab?.activity_config?.exit_overrides || {};
  const setupCount = state?.feed.filter(c => c.posture === 'SETUP').length || 0;
  const historyRows = showAllHistory ? state?.history || [] : (state?.history || []).slice(0, 15);

  const refreshDashboard = () => { setBusy(true); setRefreshTick(value => value + 1); window.setTimeout(() => setBusy(false), 500); };
  const resetMyDemo = async () => {
    if (!supabase) return;
    try {
      const { data: { session } } = await supabase.auth.getSession();
      const token = session?.access_token;
      if (!token) throw new Error('Session expired');
      const response = await fetch(`${API}/user/reset`, { method: 'POST', cache: 'no-store', headers: { Authorization: `Bearer ${token}` } });
      if (!response.ok) throw new Error(`Backend HTTP ${response.status}`);
      setState(await response.json() as MonitorState);
      setError('');
      setRefreshTick(value => value + 1);
    } catch (err) { setError(err instanceof Error ? err.message : 'Reset failed'); }
  };

  const dexEmbed = selectedCoin?.pairAddress ? `https://dexscreener.com/solana/${selectedCoin.pairAddress}?embed=1&theme=dark&trades=0&info=0` : '';
  const engineQuiet = !!state && state.running && (state.stats.open_positions ?? 0) === 0 && diagnostics?.status !== 'opened';
  const windowChoices = (flow?.available_windows || ['m1', 'm5', 'm15', 'm30', 'h1', 'h6', 'h24']);
  const activeWindow = windowChoices.includes(flowWindow) ? flowWindow : windowChoices[windowChoices.length - 1] || 'm5';
  const shownTape = flow?.tape.windows[activeWindow];
  const shownGecko = flow?.gecko.windows[activeWindow];
  const shownDex = flow?.dexscreener?.[activeWindow];

  const tabs: { id: Tab; label: string; icon: ReactNode; badge?: string }[] = [
    { id: 'engine', label: 'Engine', icon: <Bot className="h-3.5 w-3.5" />, badge: state ? `${state.stats.open_positions}/${state.config.max_positions}` : undefined },
    { id: 'lab', label: 'Лаборатория', icon: <FlaskConical className="h-3.5 w-3.5" />, badge: state ? `${activeBooks.length} активни` : undefined },
    { id: 'coins', label: 'Койн фийд', icon: <Activity className="h-3.5 w-3.5" />, badge: state ? `${state.stats.feed_count}` : undefined },
  ];

  return <div className="min-h-screen bg-[#07090b] text-slate-200">
    <header className="sticky top-0 z-50 border-b border-white/[0.07] bg-[#07090b]/95 backdrop-blur-xl">
      <div className="mx-auto flex max-w-[1800px] flex-wrap items-center justify-between gap-3 px-4 py-3 sm:px-6 lg:px-8">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl border border-emerald-400/20 bg-emerald-400/10"><Zap className="h-5 w-5 text-emerald-300" /></div>
          <div>
            <div className="flex items-center gap-2"><span className="text-base font-black text-white">NEO Meme Coins</span><span className="rounded-md border border-amber-400/20 bg-amber-400/10 px-1.5 py-0.5 text-[8px] font-black tracking-[0.14em] text-amber-200">PAPER</span></div>
            <div className="text-[10px] text-slate-600">Solana · симулация без реални пари</div>
          </div>
        </div>
        <nav className="order-last flex w-full gap-1 overflow-x-auto rounded-2xl border border-white/[0.07] bg-white/[0.02] p-1 sm:order-none sm:w-auto">
          {tabs.map(item => <button key={item.id} onClick={() => switchTab(item.id)} className={`flex shrink-0 items-center gap-2 rounded-xl px-3 py-2 text-[11px] font-black transition ${tab === item.id ? 'bg-emerald-400/15 text-emerald-200' : 'text-slate-500 hover:text-white'}`}>{item.icon}{item.label}{item.badge && <span className={`rounded-md px-1.5 py-0.5 text-[9px] ${tab === item.id ? 'bg-emerald-400/20' : 'bg-white/[0.06]'}`}>{item.badge}</span>}</button>)}
        </nav>
        <div className="flex items-center gap-2">
          <div className={`hidden items-center gap-2 rounded-xl border px-3 py-2 text-[10px] font-black sm:flex ${state?.status === 'monitoring' ? 'border-emerald-400/20 bg-emerald-400/[0.06] text-emerald-300' : 'border-amber-400/20 bg-amber-400/[0.06] text-amber-200'}`}><span className={`h-2 w-2 rounded-full ${state?.status === 'monitoring' ? 'animate-pulse bg-emerald-300' : 'bg-amber-300'}`} />{state?.status === 'monitoring' ? 'BACKEND ONLINE' : (state?.status || 'CONNECTING').toUpperCase()}</div>
          <button onClick={refreshDashboard} disabled={busy} className="flex h-10 items-center gap-2 rounded-xl border border-white/10 bg-white/[0.03] px-3 text-[10px] font-black text-white hover:bg-white/[0.06] disabled:opacity-40"><RefreshCw className={`h-3.5 w-3.5 ${busy ? 'animate-spin' : ''}`} /> ОБНОВИ</button>
        </div>
      </div>
    </header>

    <main className="mx-auto max-w-[1800px] px-4 py-5 sm:px-6 lg:px-8">
      {error && <div className="mb-4 rounded-2xl border border-red-500/20 bg-red-500/[0.06] px-4 py-3 text-xs text-red-200">{error}</div>}

      {tab === 'engine' && <>
        <section className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
          <Metric label="Баланс" value={`$${allTimeBalance.toFixed(2)}`} hint={`старт $${startingBalance.toFixed(0)} · ${signed(allTimeReturnPct)}%`} tone={allTimeReturnPct >= 0 ? 'up' : 'down'} />
          <Metric label="Днес" value={`${signed(state?.stats.realized_today_usd ?? 0)}$`} hint={`нереализирано ${signed(state?.stats.unrealized_pnl_usd ?? 0)}$`} tone={(state?.stats.realized_today_usd ?? 0) >= 0 ? 'up' : 'down'} />
          <Metric label="Отворени" value={`${state?.stats.open_positions ?? 0} / ${state?.config.max_positions ?? '—'}`} hint={`до $${state?.config.trade_notional_usd ?? '—'} на позиция`} />
          <Metric label="Затворени" value={String(state?.stats.closed_trades ?? 0)} hint={state?.stats.closed_trades ? `win rate ${state.stats.win_rate.toFixed(0)}%` : 'още няма'} />
          <Metric label="Последен scan" value={state?.last_scan_at ? agoLabel(state.last_scan_at) : '—'} hint={`${state?.stats.feed_count ?? 0} койна · ${setupCount} SETUP`} />
          <Metric label="Статус" value={state ? (state.running ? 'РАБОТИ' : 'ПАУЗА') : '—'} hint={`${state?.config.signal_strategy ?? '—'} · стоп −${state?.config.stop_loss_pct ?? '—'}%`} tone={state?.running ? 'up' : 'down'} />
        </section>

        <section className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
          <Card kicker="Engine" title={engineQuiet ? 'Защо в момента не търгува' : 'Какво проверява преди вход'} right={<ShieldCheck className="h-4 w-4 text-emerald-300" />}>
            <div className="p-4">
              {!diagnostics ? <div className="text-xs text-slate-500">Чакам първия scan…</div> : <>
                <div className={`rounded-2xl border p-3 text-xs leading-5 ${engineQuiet ? 'border-amber-400/20 bg-amber-400/[0.05] text-amber-100' : 'border-emerald-400/15 bg-emerald-400/[0.04] text-emerald-100'}`}>{diagnostics.message || state?.message || '—'}</div>
                <div className="mt-3 grid grid-cols-5 gap-2 text-center">
                  {([['Кандидати', diagnostics.candidates], ['Проверени', diagnostics.evaluated], ['Минали филтъра', diagnostics.signal_passed], ['Котирани', diagnostics.quoted], ['Отворени', diagnostics.opened]] as [string, number | undefined][]).map(([label, value]) => <div key={label} className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-2"><div className="text-base font-black text-white">{value ?? 0}</div><div className="mt-0.5 text-[8px] font-black uppercase tracking-wider text-slate-600">{label}</div></div>)}
                </div>
                <div className="mt-4 text-[9px] font-black uppercase tracking-[0.16em] text-slate-600">Откази на последния scan · {agoLabel(diagnostics.checked_at)}</div>
                <div className="mt-2"><RejectionBars rows={rejectionRows} total={diagnostics.candidates ?? 0} /></div>
                {!!diagnostics.examples?.length && <details className="mt-3 text-[10px] text-slate-500"><summary className="cursor-pointer font-black text-slate-400">Примери ({diagnostics.examples.length})</summary>
                  <div className="mt-2 space-y-1">{diagnostics.examples.map((ex, i) => <div key={`${ex.symbol}-${i}`} className="rounded-lg border border-white/[0.05] px-2 py-1.5"><span className="font-black text-white">${ex.symbol}</span> · {ex.reasons.map(r => diagnostics.reason_labels?.[r] || r).join(', ')}</div>)}</div></details>}
                <div className="mt-3 text-[9px] text-slate-600">Стратегия {diagnostics.strategy ?? state?.config.signal_strategy} · {diagnostics.policy_version} · scan на {state?.config.scan_seconds}s · позиции на {state?.config.position_scan_seconds ?? 1}s</div>
              </>}
            </div>
          </Card>

          <div className="space-y-4">
            <Card kicker="Позиции" title="Отворени PAPER позиции" right={<WalletCards className="h-4 w-4 text-emerald-300" />}>
              <div className="space-y-2 p-4">{(state?.positions || []).length === 0 && <div className="rounded-xl border border-dashed border-white/[0.08] p-4 text-center text-[10px] leading-5 text-slate-600">Няма отворена позиция. Причината е вляво.</div>}{state?.positions.map(position => <button key={position.id} onClick={() => openCoin(position.address)} className="w-full rounded-2xl border border-white/[0.07] bg-white/[0.02] p-3 text-left hover:border-emerald-400/20"><div className="flex items-center justify-between gap-2"><div><div className="text-xs font-black text-white">${position.symbol}</div><div className="mt-0.5 text-[9px] text-slate-600">#{position.trade_no ?? '—'} · вход {fmtPrice(position.execution_entry_price ?? position.entry_price)} · ${position.notional_usd.toFixed(0)}</div></div><div className={`text-sm font-black ${position.pnl_pct >= 0 ? 'text-emerald-300' : 'text-red-300'}`}>{signed(position.pnl_pct)}%</div></div><div className="mt-2 flex items-center justify-between text-[9px] text-slate-600"><span>{signed(position.pnl_usd)}$ · score {position.current_score?.toFixed(0) ?? position.score.toFixed(0)}</span><span>{holdLabel((Date.now() - position.opened_at) / 1000)}</span></div></button>)}</div>
            </Card>
            <Card kicker="Дневник" title="Какво прави NEO" right={<Bot className="h-4 w-4 text-emerald-300" />}>
              <div className="space-y-3 p-4">{(state?.events || []).slice(0, 8).map(event => <div key={`${event.ts}-${event.text}`} className="flex gap-2.5"><div className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-300" /><div><div className="text-[10px] leading-4 text-slate-400">{event.text}</div><div className="mt-0.5 text-[8px] text-slate-700">{tapeTimeLabel(event.ts)}</div></div></div>)}</div>
            </Card>
          </div>
        </section>

        {state?.learning && <section data-testid="engine-learning" className="mt-4 rounded-2xl border border-white/[0.07] bg-white/[0.02] p-4 text-xs leading-6 text-slate-400">
          <span className="font-black text-white">Учене:</span> {state.learning.trades_used} сделки · поредни загуби {state.learning.loss_streak}{state.learning.loss_streak_brake ? ' · размерът е наполовина' : ''}.
          {state.learning.best.length > 0 && <> Най-добри: {state.learning.best.slice(0, 3).map(row => `${row.bucket} ${signed(row.mean_pct)}% (${row.trades})`).join(' · ')}.</>}
          {state.learning.worst.length > 0 && <> Най-слаби: {state.learning.worst.slice(0, 3).map(row => `${row.bucket} ${signed(row.mean_pct)}% (${row.trades})`).join(' · ')}.</>}
          {state.learning.avoided.length > 0 && <span className="text-amber-200"> Избягвани: {state.learning.avoided.join(' · ')}.</span>}
        </section>}

        <Card className="mt-4" kicker="История" title="Затворени PAPER сделки" right={<div className="flex items-center gap-2 text-[9px] text-slate-600"><CircleDollarSign className="h-4 w-4 text-emerald-300" /> {state?.stats.closed_trades ?? 0} затворени</div>}>
          <div className="overflow-x-auto"><table className="w-full min-w-[1100px] text-left"><thead><tr className="border-b border-white/[0.06] text-[8px] font-black uppercase tracking-[0.14em] text-slate-700"><th className="px-4 py-3"># / Coin</th><th className="px-4 py-3">Вход — време</th><th className="px-4 py-3">Вход — цена</th><th className="px-4 py-3">Изход — време</th><th className="px-4 py-3">Изход — цена</th><th className="px-4 py-3">Размер</th><th className="px-4 py-3">Резултат</th><th className="px-4 py-3">Баланс</th><th className="px-4 py-3">Задържане</th><th className="px-4 py-3">Причина</th><th className="px-4 py-3"></th></tr></thead><tbody>{historyRows.length === 0 ? <tr><td colSpan={11} className="px-4 py-8 text-center text-xs text-slate-600">Историята ще се появи след първата затворена PAPER позиция.</td></tr> : historyRows.map((trade, historyIndex) => <tr key={trade.id} onClick={() => openCoin(trade.address)} className="cursor-pointer border-b border-white/[0.04] text-xs hover:bg-white/[0.02]"><td className="px-4 py-3"><div className="font-black text-white">#{trade.trade_no ?? (state?.history.length ?? 0) - historyIndex} · ${trade.symbol}</div><div className="mt-1 text-[9px] text-slate-700">{shortAddress(trade.address)}</div></td><td className="px-4 py-3 text-[10px] text-slate-500">{fullTimeLabel(trade.opened_at)}</td><td className="px-4 py-3 text-slate-300">{fmtPrice(trade.execution_entry_price ?? trade.entry_price)}</td><td className="px-4 py-3 text-[10px] text-slate-500">{fullTimeLabel(trade.closed_at || trade.updated_at)}</td><td className="px-4 py-3 text-slate-300">{fmtPrice(trade.execution_exit_price ?? trade.exit_price ?? trade.current_price)}</td><td className="px-4 py-3 text-slate-400">${trade.notional_usd.toFixed(2)}</td><td className={`px-4 py-3 font-black ${(trade.pnl_pct || 0) >= 0 ? 'text-emerald-300' : 'text-red-300'}`}>{signed(trade.pnl_usd || 0)}$ · {signed(trade.pnl_pct || 0)}%</td><td className="px-4 py-3 text-slate-400">${(trade.balance_after ?? 0).toFixed(2)}</td><td className="px-4 py-3 text-slate-500">{holdLabel(trade.closed_at ? (trade.closed_at - trade.opened_at) / 1000 : null)}</td><td className="px-4 py-3 text-slate-400">{trade.exit_reason || '—'}</td><td className="px-4 py-3">{trade.dex_url && <a onClick={e => e.stopPropagation()} href={trade.dex_url} target="_blank" rel="noreferrer" className="text-slate-500 hover:text-white"><ExternalLink className="h-3.5 w-3.5" /></a>}</td></tr>)}</tbody></table></div>
          {(state?.history.length ?? 0) > 15 && <div className="border-t border-white/[0.06] p-3 text-center"><button onClick={() => setShowAllHistory(v => !v)} className="text-[10px] font-black text-slate-400 hover:text-white">{showAllHistory ? 'Покажи последните 15' : `Покажи всички ${state?.history.length}`}</button></div>}
        </Card>
        <div className="mt-3 flex items-center justify-between text-[9px] text-slate-700"><span>Сесия {state?.stats.demo_session_id || '—'} · от {fullTimeLabel(state?.stats.demo_started_at || 0)}</span><button onClick={() => { if (window.confirm('Да започна ли НОВА ЛИЧНА demo сесия с $1,000? Старата история се архивира.')) resetMyDemo(); }} className="rounded-lg border border-white/[0.07] px-2 py-1 font-black text-slate-600 hover:text-white">RESET DEMO → $1,000</button></div>
      </>}

      {tab === 'lab' && <>
        <Card kicker="Лаборатория" title="Тестови стратегии · натисни стратегия за всяка нейна сделка" right={<div className={`rounded-lg border px-2.5 py-1.5 text-[9px] font-black ${state?.strategy_lab?.status === 'online' ? 'border-emerald-400/20 bg-emerald-400/10 text-emerald-300' : 'border-amber-400/20 bg-amber-400/10 text-amber-200'}`}>{(state?.strategy_lab?.status || 'CONNECTING').toUpperCase()}</div>}>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[1000px] text-left">
              <thead><tr className="border-b border-white/[0.06] text-[8px] font-black uppercase tracking-[0.14em] text-slate-700"><th className="px-4 py-3">Стратегия</th><th className="px-4 py-3">Стоп / Цел</th><th className="px-4 py-3">Баланс</th><th className="px-4 py-3">Резултат</th><th className="px-4 py-3">Сделки</th><th className="px-4 py-3">Win rate</th><th className="px-4 py-3">PF</th><th className="px-4 py-3">Отворена позиция</th></tr></thead>
              <tbody>
                {(showQuietBooks ? labBooks : activeBooks).map(book => {
                  const st = state?.strategy_lab?.stats?.[book.id];
                  const pnl = st?.realized_pnl ?? (book.balance - book.starting_balance);
                  const expanded = openBook === book.id;
                  const exits = exitOverrides[book.id] || {};
                  const quiet = book.why_quiet;
                  return <Fragment key={book.id}><tr className={`border-b border-white/[0.04] text-xs hover:bg-white/[0.02] ${expanded ? 'bg-white/[0.03]' : ''}`}>
                    <td className="px-4 py-3"><button type="button" aria-expanded={expanded} onClick={() => setOpenBook(expanded ? '' : book.id)} className="flex items-center gap-2 text-left font-black text-white hover:text-cyan-200"><span className="text-cyan-300">{expanded ? '▾' : '▸'}</span>{book.name}</button><div className="mt-1 font-mono text-[8px] text-slate-700">{book.id}</div></td>
                    <td className="px-4 py-3 text-[10px] text-slate-400">−{exits.stop_loss ?? 3}% / +{exits.take_profit ?? 10}%</td>
                    <td className="px-4 py-3 font-black text-white">${book.balance.toFixed(2)}<div className="mt-1 text-[9px] font-normal text-slate-600">старт ${book.starting_balance.toFixed(0)}</div></td>
                    <td className={`px-4 py-3 font-black ${pnl >= 0 ? 'text-emerald-300' : 'text-red-300'}`}>{signed(pnl)}$<div className="mt-1 text-[9px]">{signed(st?.return_pct ?? 0)}%</div></td>
                    <td className="px-4 py-3 text-slate-400">{st?.trades ?? 0}<div className="mt-1 text-[9px] text-slate-700">{st?.wins ?? 0}W / {st?.losses ?? 0}L</div></td>
                    <td className="px-4 py-3 font-black text-white">{st?.trades ? `${st.win_rate.toFixed(1)}%` : '—'}</td>
                    <td className="px-4 py-3 text-slate-300">{st?.profit_factor != null ? st.profit_factor.toFixed(2) : st?.wins && !st.losses ? '∞' : '—'}</td>
                    <td className="px-4 py-3">{book.position ? <button onClick={() => openCoin(book.position!.address)} className="rounded-xl border border-cyan-400/15 bg-cyan-400/[0.05] px-3 py-2 text-left"><div className="font-black text-cyan-200">${book.position.symbol}</div><div className={`mt-1 text-[9px] font-black ${(book.position.pnl_pct || 0) >= 0 ? 'text-emerald-300' : 'text-red-300'}`}>{signed(book.position.pnl_pct || 0)}% · ${book.position.notional_usd.toFixed(0)}</div></button> : <span className="text-[9px] text-slate-700">{quiet?.reasons?.[0] ? `чака · ${labLabels[quiet.reasons[0].reason] || quiet.reasons[0].reason}` : 'чака setup'}</span>}</td>
                  </tr>
                  {expanded && <tr className="border-b border-cyan-400/10 bg-black/20"><td colSpan={8} className="px-4 py-3">
                    {quiet && <div className="mb-3 rounded-2xl border border-white/[0.06] bg-white/[0.02] p-3">
                      <div className="text-[9px] font-black uppercase tracking-[0.16em] text-slate-600">Защо (не) влиза · {quiet.candidates} проверени кандидата в {quiet.scans} scan-а от {fullTimeLabel(quiet.since ?? 0)}{quiet.last_rule_match_at ? ` · последно минал филтъра ${agoLabel(quiet.last_rule_match_at)}` : ' · нито един кандидат не е минал филтъра'}</div>
                      <div className="mt-2 max-w-xl"><RejectionBars rows={quiet.reasons.map(r => ({ key: r.reason, count: r.count, label: labLabels[r.reason] || r.reason }))} total={quiet.candidates} /></div>
                    </div>}
                    {bookTradesError && <div className="text-xs text-amber-200">{bookTradesError}</div>}
                    {!bookTradesError && !bookTrades && <div className="text-xs text-slate-500">Зареждане на сделките…</div>}
                    {bookTrades && bookTrades.trades.length === 0 && <div className="text-xs text-slate-500">Тази стратегия още няма сделки.</div>}
                    {bookTrades && bookTrades.trades.length > 0 && <div className="overflow-x-auto">
                      <div className="mb-2 text-[9px] text-slate-600">{bookTrades.total} затворени сделки{bookTrades.shown < bookTrades.total ? ` · показани последните ${bookTrades.shown}` : ''} · цените са симулираното изпълнение</div>
                      <table data-testid="lab-book-trades" className="w-full min-w-[900px] text-left">
                        <thead><tr className="border-b border-white/[0.06] text-[8px] font-black uppercase tracking-[0.14em] text-slate-700"><th className="px-3 py-2"># / Монета</th><th className="px-3 py-2">Вход — време</th><th className="px-3 py-2">Вход — цена</th><th className="px-3 py-2">Изход — време</th><th className="px-3 py-2">Изход — цена</th><th className="px-3 py-2">Задържане</th><th className="px-3 py-2">Резултат</th><th className="px-3 py-2">Причина</th></tr></thead>
                        <tbody>{bookTrades.trades.map((trade, index) => <tr key={`${trade.trade_no ?? index}-${trade.opened_at ?? index}`} className="border-b border-white/[0.04] text-[11px]">
                          <td className="px-3 py-2"><button onClick={() => trade.address && openCoin(trade.address)} className="font-black text-white hover:text-cyan-200">#{trade.trade_no ?? '—'} · ${trade.symbol ?? '—'}</button></td>
                          <td className="px-3 py-2 text-slate-400">{fullTimeLabel(trade.opened_at ?? 0)}</td>
                          <td className="px-3 py-2 text-slate-300">{fmtPrice(trade.execution_entry_price ?? trade.entry_price ?? 0)}</td>
                          <td className="px-3 py-2 text-slate-400">{trade.open ? <span className="font-black text-cyan-300">отворена</span> : fullTimeLabel(trade.closed_at ?? 0)}</td>
                          <td className="px-3 py-2 text-slate-300">{trade.open ? `${fmtPrice(trade.current_price ?? 0)} сега` : fmtPrice(trade.execution_exit_price ?? trade.exit_price ?? 0)}</td>
                          <td className="px-3 py-2 text-slate-400">{holdLabel(trade.hold_seconds)}</td>
                          <td className={`px-3 py-2 font-black ${(trade.pnl_pct ?? 0) >= 0 ? 'text-emerald-300' : 'text-red-300'}`}>{signed(trade.pnl_usd ?? 0)}$ · {signed(trade.pnl_pct ?? 0)}%</td>
                          <td className="px-3 py-2 text-slate-500">{trade.open ? '—' : trade.exit_reason ?? '—'}{!trade.open && trade.exit_fill_model === 'LIMIT_AT_TARGET' && trade.observed_exit_pnl_pct != null && <div className="mt-0.5 text-[9px] text-slate-600">лимит на целта · пазарът беше {signed(trade.observed_exit_pnl_pct)}%</div>}{!trade.open && (trade.exit_reason ?? '').startsWith('STOP_LOSS') && trade.pre_exit_pnl_pct != null && trade.pre_exit_gap_seconds != null && <div className="mt-0.5 text-[9px] text-slate-600">скок от {signed(trade.pre_exit_pnl_pct)}% за {trade.pre_exit_gap_seconds.toFixed(1)}с</div>}</td>
                        </tr>)}</tbody>
                      </table>
                    </div>}
                  </td></tr>}
                  </Fragment>;
                })}
                {!labBooks.length && <tr><td colSpan={8} className="px-4 py-8 text-center text-xs text-slate-600">Лабораторията още не е изпратила данни.</td></tr>}
              </tbody>
            </table>
          </div>
          {quietBooks.length > 0 && <div className="border-t border-white/[0.06] p-3 text-center"><button onClick={() => setShowQuietBooks(v => !v)} className="text-[10px] font-black text-slate-400 hover:text-white">{showQuietBooks ? 'Скрий стратегиите без сделки' : `Покажи и ${quietBooks.length} стратегии без нито една сделка (с причината защо)`}</button></div>}
        </Card>
        <div className="mt-4"><LabPairedPanel data={state?.strategy_lab?.paired} /></div>
        <PaperTrainingPanel data={state?.paper_training} />
      </>}

      {tab === 'coins' && <section className="grid gap-4 xl:grid-cols-[390px_minmax(0,1fr)]">
        <Card kicker="Live feed" title="Койнове, които NEO следи" right={<Sparkles className="h-4 w-4 text-emerald-300" />}>
          <div className="border-b border-white/[0.07] p-3">
            <div className="relative"><Search className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-600" /><input value={search} onChange={e => setSearch(e.target.value)} placeholder="Coin, symbol или address…" className="h-10 w-full rounded-xl border border-white/10 bg-black/20 pl-9 pr-3 text-xs text-white outline-none placeholder:text-slate-700 focus:border-emerald-400/30" /></div>
            <div className="mt-3 flex gap-1.5 overflow-x-auto pb-1">{(['ALL', 'SETUP', 'WATCH', 'NEW', 'BOOSTED'] as Filter[]).map(item => <button key={item} onClick={() => setFilter(item)} className={`shrink-0 rounded-lg border px-2.5 py-1.5 text-[9px] font-black ${filter === item ? 'border-emerald-400/25 bg-emerald-400/10 text-emerald-300' : 'border-white/[0.07] bg-white/[0.02] text-slate-500 hover:text-white'}`}>{item}</button>)}</div>
          </div>
          <div className="max-h-[820px] overflow-y-auto p-2">
            {filtered.length === 0 && <div className="p-6 text-center text-xs text-slate-600">Няма coins за този филтър.</div>}
            {filtered.map((coin, index) => {
              const active = selectedCoin?.address === coin.address;
              return <button key={coin.address} onClick={() => setSelectedAddress(coin.address)} className={`mb-1.5 w-full rounded-2xl border p-3 text-left transition ${active ? 'border-emerald-400/25 bg-emerald-400/[0.055]' : 'border-white/[0.06] bg-white/[0.015] hover:border-white/10 hover:bg-white/[0.03]'}`}>
                <div className="flex items-center gap-3"><div className="w-5 shrink-0 text-center text-[9px] font-black text-slate-700">#{index + 1}</div><CoinAvatar coin={coin} /><div className="min-w-0 flex-1"><div className="truncate text-xs font-black text-white">${coin.symbol}</div><div className="mt-0.5 truncate text-[9px] text-slate-600">{coin.name} · {ageLabel(coin.ageMinutes)}</div></div><ScoreBadge coin={coin} /></div>
                <div className="mt-3 grid grid-cols-4 gap-2 text-[9px]"><div><div className="text-slate-700">PRICE</div><div className="mt-0.5 font-bold text-white">{fmtPrice(coin.priceUsd)}</div></div><div><div className="text-slate-700">5M</div><div className="mt-0.5"><Change value={coin.priceChange.m5} compact /></div></div><div><div className="text-slate-700">LIQ</div><div className="mt-0.5 font-bold text-slate-300">{fmtMoney(coin.liquidityUsd)}</div></div><div><div className="text-slate-700">B/S 5M</div><div className="mt-0.5 font-bold"><span className="text-emerald-300">{coin.txns.m5.buys}</span><span className="text-slate-600"> / </span><span className="text-red-300">{coin.txns.m5.sells}</span></div></div></div>
              </button>;
            })}
          </div>
        </Card>

        <div className="min-w-0 space-y-4">
          {selectedCoin ? <>
            <div className="overflow-hidden rounded-3xl border border-white/10 bg-[#0b0e11]">
              <div className="flex flex-col gap-4 border-b border-white/[0.07] p-4 sm:flex-row sm:items-center sm:justify-between">
                <div className="flex min-w-0 items-center gap-3"><CoinAvatar coin={selectedCoin} size="lg" /><div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><h1 className="text-xl font-black text-white">${selectedCoin.symbol}</h1><ScoreBadge coin={selectedCoin} /><span className="rounded-md border border-white/[0.07] bg-white/[0.03] px-2 py-1 text-[9px] font-black text-slate-500">{selectedCoin.dexId.toUpperCase()}</span></div><div className="mt-1 truncate text-xs text-slate-500">{selectedCoin.name} · {shortAddress(selectedCoin.address)} · {ageLabel(selectedCoin.ageMinutes)}</div></div></div>
                <div className="flex items-end gap-4 sm:text-right"><div><div className="text-2xl font-black text-white">{fmtPrice(selectedCoin.priceUsd)}</div><div className="mt-1 flex items-center gap-2 sm:justify-end"><Change value={selectedCoin.priceChange.m5} /><span className="text-[9px] text-slate-600">5m</span></div></div>{selectedCoin.dexUrl && <a href={selectedCoin.dexUrl} target="_blank" rel="noreferrer" className="flex h-10 w-10 items-center justify-center rounded-xl border border-white/10 bg-white/[0.03] text-slate-400 hover:text-white"><ExternalLink className="h-4 w-4" /></a>}</div>
              </div>
              <div className="grid grid-cols-2 gap-px bg-white/[0.05] sm:grid-cols-4">{([['Market cap', fmtMoney(selectedCoin.marketCap || selectedCoin.fdv)], ['Ликвидност', fmtMoney(selectedCoin.liquidityUsd)], ['Обем 1ч', fmtMoney(selectedCoin.volume.h1)], ['1ч', `${signed(selectedCoin.priceChange.h1, 1)}%`]] as [string, string][]).map(([label, value]) => <div key={label} className="bg-[#0b0e11] p-4"><div className="text-[9px] font-black uppercase tracking-[0.14em] text-slate-700">{label}</div><div className="mt-1 text-base font-black text-white">{value}</div></div>)}</div>
            </div>

            <Card kicker="Купувачи · продавачи" title={`$${selectedCoin.symbol} · по периоди`} right={<div className="text-[9px] text-slate-500">{flow?.tape.status === 'online' ? `on-chain · покритие ${flow.tape.coverage ?? '—'}` : 'on-chain tape: свързване…'}</div>}>
              <div className="flex flex-wrap gap-1.5 border-b border-white/[0.07] p-3">
                {['m1', 'm5', 'm15', 'm30', 'h1', 'h6', 'h24'].map(key => { const enabled = windowChoices.includes(key); return <button key={key} disabled={!enabled} onClick={() => setFlowWindow(key)} title={enabled ? '' : 'Койнът е по-млад от този период'} className={`rounded-lg border px-2.5 py-1.5 text-[10px] font-black ${activeWindow === key ? 'border-emerald-400/25 bg-emerald-400/10 text-emerald-300' : enabled ? 'border-white/[0.07] bg-white/[0.02] text-slate-400 hover:text-white' : 'border-white/[0.04] text-slate-800'}`}>{WINDOW_LABELS[key]}</button>; })}
                <span className="ml-auto self-center text-[9px] text-slate-700">{flow?.longer_windows_note}</span>
              </div>
              <div className="grid grid-cols-2 gap-px bg-white/[0.05] md:grid-cols-4">
                <div className="bg-[#0b0e11] p-3"><div className="text-[8px] font-black uppercase text-slate-700">Покупки · {WINDOW_LABELS[activeWindow]}</div><div className="mt-1 text-xl font-black text-emerald-300">{shownGecko?.buys ?? shownDex?.buys ?? shownTape?.buys ?? '—'}</div><div className="text-[9px] text-slate-600">{shownGecko ? `${shownGecko.buyers} различни купувачи` : shownTape ? `${shownTape.buyers} портфейла on-chain` : ''}</div></div>
                <div className="bg-[#0b0e11] p-3"><div className="text-[8px] font-black uppercase text-slate-700">Продажби · {WINDOW_LABELS[activeWindow]}</div><div className="mt-1 text-xl font-black text-red-300">{shownGecko?.sells ?? shownDex?.sells ?? shownTape?.sells ?? '—'}</div><div className="text-[9px] text-slate-600">{shownGecko ? `${shownGecko.sellers} различни продавачи` : shownTape ? `${shownTape.sellers} портфейла on-chain` : ''}</div></div>
                <div className="bg-[#0b0e11] p-3"><div className="text-[8px] font-black uppercase text-slate-700">On-chain $ · {WINDOW_LABELS[activeWindow]}</div><div className="mt-1 text-sm font-black"><span className="text-emerald-300">{fmtMoney(shownTape?.buy_usd ?? 0)}</span><span className="text-slate-600"> / </span><span className="text-red-300">{fmtMoney(shownTape?.sell_usd ?? 0)}</span></div><div className="text-[9px] text-slate-600">{shownTape ? (shownTape.complete ? `${shownTape.buys}B / ${shownTape.sells}S проверени` : `частично · tape покрива ${holdLabel(flow?.tape.covered_seconds ?? 0)}`) : 'няма on-chain данни'}</div></div>
                <div className="bg-[#0b0e11] p-3"><div className="text-[8px] font-black uppercase text-slate-700">Обем · {WINDOW_LABELS[activeWindow]}</div><div className="mt-1 text-sm font-black text-white">{flow?.gecko.volume_usd?.[activeWindow] != null ? fmtMoney(flow.gecko.volume_usd[activeWindow]) : '—'}</div><div className="text-[9px] text-slate-600">{flow?.gecko.price_change_pct?.[activeWindow] != null ? `цена ${signed(flow.gecko.price_change_pct[activeWindow], 1)}%` : flow?.gecko.error ? 'GeckoTerminal недостъпен' : ''}</div></div>
              </div>
              <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-2 text-[9px] text-slate-600"><span>Последните 60с on-chain: <span className="font-black text-emerald-300">{detail?.flow?.buys ?? 0} покупки</span> · <span className="font-black text-red-300">{detail?.flow?.sells ?? 0} продажби</span> · {detail?.flow?.unique_wallets ?? 0} портфейла · {fmtMoney(detail?.flow?.buy_usd ?? 0)} / {fmtMoney(detail?.flow?.sell_usd ?? 0)}</span><span>обновява се на 2с · {flow ? agoLabel(flow.at) : ''}</span></div>
              <div className="max-h-[260px] overflow-y-auto border-t border-white/[0.06]">
                {(detail?.live_tape || []).length ? (detail?.live_tape || []).slice(0, 40).map(tx => <a key={tx.signature} href={`https://solscan.io/tx/${tx.signature}`} target="_blank" rel="noreferrer" className="grid grid-cols-[62px_48px_minmax(70px,1fr)_92px_86px] items-center gap-2 border-b border-white/[0.05] px-4 py-2 text-[9px] hover:bg-white/[0.025]"><span className="font-mono text-slate-600">{tapeTimeLabel(tx.ts)}</span><span className={`font-black ${tx.direction === 'BUY' ? 'text-emerald-300' : 'text-red-300'}`}>{tx.direction}</span><span className="truncate font-black text-white">{fmtMoney(tx.usd_amount)}</span><span className="truncate font-mono text-slate-500">{shortAddress(tx.wallet)}</span><span className={`truncate text-right font-black ${tx.note.includes('WHALE') ? 'text-amber-300' : tx.direction === 'BUY' ? 'text-emerald-300/70' : 'text-red-300/70'}`}>{tx.note}</span></a>) : <div className="p-6 text-center text-[10px] leading-5 text-slate-600">{flow?.tape.coverage ? 'Още няма on-chain сделка за този pool в tape-а.' : 'On-chain tape-ът не покрива този pool; показват се данните от GeckoTerminal/DexScreener.'}</div>}
              </div>
            </Card>

            <Card kicker="Графика" title={`$${selectedCoin.symbol} / SOL`} right={<Activity className="h-4 w-4 text-emerald-300" />}>
              {dexEmbed ? <iframe title={`${selectedCoin.symbol} live chart`} src={dexEmbed} className="h-[430px] w-full border-0 bg-[#07090b]" loading="lazy" /> : <div className="flex h-[430px] items-center justify-center text-xs text-slate-600">Няма pair chart.</div>}
            </Card>
            <div className="grid gap-4 lg:grid-cols-[1.1fr_.9fr]">
              <Card kicker="NEO" title="Цена по scan-ове" right={<Gauge className="h-4 w-4 text-emerald-300" />}>
                <div className="p-4"><div className="h-[190px]">
                  {chartData.length > 1 ? <ResponsiveContainer width="100%" height="100%"><AreaChart data={chartData}><defs><linearGradient id="priceFill" x1="0" y1="0" x2="0" y2="1"><stop offset="5%" stopColor="#34d399" stopOpacity={0.3} /><stop offset="95%" stopColor="#34d399" stopOpacity={0} /></linearGradient></defs><XAxis dataKey="label" hide /><YAxis domain={['dataMin', 'dataMax']} hide /><Tooltip contentStyle={{ background: '#0a0d10', border: '1px solid rgba(255,255,255,.1)', borderRadius: 12, fontSize: 11 }} formatter={(value: number | string) => [fmtPrice(Number(value)), 'Price']} labelFormatter={(label) => String(label)} /><Area type="monotone" dataKey="price" stroke="#34d399" fill="url(#priceFill)" strokeWidth={2} dot={false} /></AreaChart></ResponsiveContainer> : <div className="flex h-full items-center justify-center rounded-2xl border border-dashed border-white/[0.07] text-center text-[10px] leading-5 text-slate-600">Графиката се запълва след няколко scan-а.</div>}
                </div><div className="mt-3 grid grid-cols-4 gap-2 border-t border-white/[0.06] pt-3">{(['m5', 'h1', 'h6', 'h24'] as const).map(key => <div key={key}><div className="text-[8px] font-black text-slate-700">{key.toUpperCase()}</div><Change value={selectedCoin.priceChange[key]} compact /></div>)}</div></div>
              </Card>
              <Card kicker="NEO анализ" title={`Защо ${selectedCoin.posture}`} right={<ShieldCheck className="h-4 w-4 text-emerald-300" />}>
                <div className="space-y-2 p-4">{selectedCoin.signals.length ? selectedCoin.signals.map((item, i) => <div key={`${item.title}-${i}`} className={`rounded-xl border p-3 ${item.kind === 'positive' ? 'border-emerald-400/15 bg-emerald-400/[0.05]' : item.kind === 'risk' ? 'border-red-400/15 bg-red-400/[0.05]' : 'border-white/[0.07] bg-white/[0.02]'}`}><div className={`text-[10px] font-black ${item.kind === 'positive' ? 'text-emerald-300' : item.kind === 'risk' ? 'text-red-300' : 'text-slate-300'}`}>{item.title}</div><div className="mt-1 text-[9px] leading-4 text-slate-600">{item.detail}</div></div>) : <div className="text-xs text-slate-600">Няма сигнали.</div>}</div>
              </Card>
            </div>
          </> : <div className="flex min-h-[500px] items-center justify-center rounded-3xl border border-white/10 bg-[#0b0e11] text-xs text-slate-600">Чакам първия market scan…</div>}
        </div>
      </section>}

      <footer className="mt-5 flex flex-col justify-between gap-2 border-t border-white/[0.06] py-5 text-[9px] leading-4 text-slate-700 sm:flex-row"><div>NEO Meme Coins · PAPER симулация · изолиран engine за този акаунт</div><div className="max-w-2xl sm:text-right">Симулираните резултати не доказват бъдеща доходност. Meme coins са високорискови.</div></footer>
    </main>
  </div>;
}
