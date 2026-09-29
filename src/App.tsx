import React, { useEffect, useMemo, useState } from 'react';
import {
  Activity,
  AlertTriangle,
  BrainCircuit,
  CheckCircle2,
  ChevronRight,
  CircleGauge,
  Clock3,
  Copy,
  Database,
  ExternalLink,
  Eye,
  Loader2,
  Radar,
  RefreshCw,
  Settings2,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  WalletCards,
  XCircle,
  Zap,
} from 'lucide-react';
import { analyzeToken } from './core/analyzeToken';
import {
  DEFAULT_DEVICE_SETTINGS,
  type AnalysisSignal,
  type DeviceSettings,
  type RiskAssessment,
  type SignalSeverity,
} from './core/types';

const SETTINGS_KEY = 'neo-meme-coins-settings-v1';
const FOMO_MONITOR_URL = 'https://neo-meme-api.169-58-211-177.sslip.io';
const FOMO_VNC_URL = `${FOMO_MONITOR_URL}/vnc/vnc.html?autoconnect=1&resize=scale&path=vnc/websockify`;

type BackendCandidate = {
  url: string;
  text: string;
  symbol: string;
  score: number;
  seen_at: number;
};

type BackendPosition = {
  id: string;
  token_url: string;
  symbol: string;
  entry_price: number;
  current_price: number;
  peak_price: number;
  score: number;
  opened_at: number;
  updated_at: number;
  pnl_pct?: number;
  exit_reason?: string;
};

type FomoMonitorState = {
  running: boolean;
  status: string;
  message: string;
  login_required: boolean;
  last_scan_at: number;
  candidates: BackendCandidate[];
  positions: BackendPosition[];
  history: BackendPosition[];
  events: Array<{ ts: number; text: string }>;
  config: { max_positions: number; entry_score: number; scan_seconds: number; stop_loss_pct?: number; take_profit_pct?: number; trailing_pct?: number; max_hold_minutes?: number };
};

function formatMoney(value: number): string {
  if (!Number.isFinite(value)) return '$0';
  if (value >= 1_000_000_000) return `$${(value / 1_000_000_000).toFixed(2)}B`;
  if (value >= 1_000_000) return `$${(value / 1_000_000).toFixed(2)}M`;
  if (value >= 1_000) return `$${(value / 1_000).toFixed(1)}K`;
  return `$${value.toFixed(0)}`;
}

function formatAge(timestamp?: number): string {
  if (!timestamp) return 'Unknown';
  const minutes = Math.max(0, (Date.now() - timestamp) / 60_000);
  if (minutes < 60) return `${minutes.toFixed(0)}m`;
  if (minutes < 1440) return `${(minutes / 60).toFixed(1)}h`;
  return `${(minutes / 1440).toFixed(1)}d`;
}

function shortAddress(address: string): string {
  if (address.length <= 12) return address;
  return `${address.slice(0, 5)}…${address.slice(-5)}`;
}

function severityStyles(severity: SignalSeverity) {
  switch (severity) {
    case 'critical':
      return {
        wrap: 'border-red-500/25 bg-red-500/[0.06]',
        icon: <XCircle className="h-4 w-4 text-red-400" />,
        label: 'CRITICAL',
        labelClass: 'text-red-300',
      };
    case 'warning':
      return {
        wrap: 'border-amber-400/20 bg-amber-400/[0.05]',
        icon: <AlertTriangle className="h-4 w-4 text-amber-300" />,
        label: 'CAUTION',
        labelClass: 'text-amber-200',
      };
    case 'positive':
      return {
        wrap: 'border-emerald-400/20 bg-emerald-400/[0.05]',
        icon: <CheckCircle2 className="h-4 w-4 text-emerald-300" />,
        label: 'POSITIVE',
        labelClass: 'text-emerald-200',
      };
    default:
      return {
        wrap: 'border-white/10 bg-white/[0.025]',
        icon: <Eye className="h-4 w-4 text-slate-400" />,
        label: 'INFO',
        labelClass: 'text-slate-400',
      };
  }
}

function postureStyles(posture: RiskAssessment['posture']) {
  switch (posture) {
    case 'SKIP':
      return 'border-red-500/35 bg-red-500/10 text-red-200';
    case 'WAIT':
      return 'border-orange-400/30 bg-orange-400/10 text-orange-100';
    case 'WATCH':
      return 'border-amber-300/25 bg-amber-300/10 text-amber-100';
    case 'SETUP':
      return 'border-emerald-400/30 bg-emerald-400/10 text-emerald-100';
  }
}

function MetricCard({
  label,
  value,
  detail,
  icon,
}: {
  label: string;
  value: React.ReactNode;
  detail: string;
  icon: React.ReactNode;
}) {
  return (
    <div className="rounded-2xl border border-white/10 bg-white/[0.025] p-4">
      <div className="mb-4 flex items-center justify-between">
        <span className="text-[10px] font-bold uppercase tracking-[0.18em] text-slate-500">{label}</span>
        <span className="text-slate-500">{icon}</span>
      </div>
      <div className="text-2xl font-black tracking-tight text-white">{value}</div>
      <div className="mt-1 text-xs leading-5 text-slate-500">{detail}</div>
    </div>
  );
}

function SignalRow({ signal }: { signal: AnalysisSignal }) {
  const style = severityStyles(signal.severity);
  return (
    <div className={`rounded-xl border p-4 ${style.wrap}`}>
      <div className="flex items-start gap-3">
        <div className="mt-0.5">{style.icon}</div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h4 className="text-sm font-bold text-white">{signal.title}</h4>
            <span className={`text-[9px] font-black tracking-[0.16em] ${style.labelClass}`}>{style.label}</span>
          </div>
          <p className="mt-1 text-xs leading-5 text-slate-400">{signal.detail}</p>
        </div>
        {signal.riskPoints !== 0 && (
          <div className={`text-xs font-black ${signal.riskPoints > 0 ? 'text-red-300' : 'text-emerald-300'}`}>
            {signal.riskPoints > 0 ? '+' : ''}{signal.riskPoints}
          </div>
        )}
      </div>
    </div>
  );
}

export default function App() {
  const [tokenAddress, setTokenAddress] = useState('');
  const [assessment, setAssessment] = useState<RiskAssessment | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settings, setSettings] = useState<DeviceSettings>(DEFAULT_DEVICE_SETTINGS);
  const [fomoMonitor, setFomoMonitor] = useState<FomoMonitorState | null>(null);
  const [fomoMonitorError, setFomoMonitorError] = useState('');
  const [fomoLoginOpen, setFomoLoginOpen] = useState(false);

  useEffect(() => {
    try {
      const saved = localStorage.getItem(SETTINGS_KEY);
      if (saved) setSettings({ ...DEFAULT_DEVICE_SETTINGS, ...JSON.parse(saved) });
    } catch {
      // Keep defaults if local storage is blocked or malformed.
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    const loadFomoMonitor = async () => {
      try {
        const response = await fetch(`${FOMO_MONITOR_URL}/state`, { cache: 'no-store' });
        if (!response.ok) throw new Error(`Monitor HTTP ${response.status}`);
        const data = await response.json() as FomoMonitorState;
        if (!cancelled) {
          setFomoMonitor(data);
          setFomoMonitorError('');
        }
      } catch (monitorError) {
        if (!cancelled) setFomoMonitorError(monitorError instanceof Error ? monitorError.message : 'Backend monitor unavailable');
      }
    };
    loadFomoMonitor();
    const interval = window.setInterval(loadFomoMonitor, 4_000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, []);

  const saveSettings = (next: DeviceSettings) => {
    setSettings(next);
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(next));
  };

  const runAnalysis = async (address = tokenAddress) => {
    const clean = address.trim();
    if (!clean) {
      setError('Постави contract address на Solana token.');
      return;
    }

    setTokenAddress(clean);
    setLoading(true);
    setError('');
    try {
      const result = await analyzeToken(clean, settings, '');
      setAssessment(result);
    } catch (scanError) {
      setAssessment(null);
      setError(scanError instanceof Error ? scanError.message : 'Анализът не успя.');
    } finally {
      setLoading(false);
    }
  };

  const liquidityRatio = useMemo(() => {
    if (!assessment) return 0;
    const cap = assessment.market.marketCapUsd || assessment.market.fdvUsd;
    return cap > 0 ? (assessment.market.liquidityUsd / cap) * 100 : 0;
  }, [assessment]);

  const criticalCount = assessment?.signals.filter((signal) => signal.severity === 'critical').length || 0;
  const warningCount = assessment?.signals.filter((signal) => signal.severity === 'warning').length || 0;

  const fomoHistoryWins = fomoMonitor?.history.filter((trade) => (trade.pnl_pct || 0) > 0).length || 0;
  const fomoWinRate = fomoMonitor?.history.length
    ? (fomoHistoryWins / fomoMonitor.history.length) * 100
    : 0;

  const controlFomoMonitor = async (action: 'start' | 'stop' | 'reset' | 'rescan') => {
    try {
      const response = await fetch(`${FOMO_MONITOR_URL}/control/${action}`, { method: 'POST' });
      if (!response.ok) throw new Error(`Monitor HTTP ${response.status}`);
      if (action !== 'rescan') {
        const data = await response.json() as FomoMonitorState;
        setFomoMonitor(data);
      }
      setFomoMonitorError('');
    } catch (monitorError) {
      setFomoMonitorError(monitorError instanceof Error ? monitorError.message : 'Backend monitor unavailable');
    }
  };

  const copyAddress = async () => {
    if (!assessment) return;
    try { await navigator.clipboard.writeText(assessment.tokenAddress); } catch { /* noop */ }
  };

  return (
    <div className="min-h-screen bg-[#08090c] text-slate-200">
      <div className="pointer-events-none fixed inset-0 overflow-hidden">
        <div className="absolute left-1/2 top-[-280px] h-[600px] w-[760px] -translate-x-1/2 rounded-full bg-emerald-400/[0.055] blur-[120px]" />
        <div className="absolute bottom-[-260px] right-[-180px] h-[540px] w-[540px] rounded-full bg-cyan-400/[0.035] blur-[130px]" />
      </div>

      <header className="sticky top-0 z-40 border-b border-white/[0.07] bg-[#08090c]/90 backdrop-blur-xl">
        <div className="mx-auto flex h-16 max-w-[1500px] items-center justify-between px-4 sm:px-6 lg:px-8">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-xl border border-emerald-400/20 bg-emerald-400/10">
              <Radar className="h-5 w-5 text-emerald-300" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <span className="font-black tracking-tight text-white">NEO Meme Coins</span>
                <span className="rounded-md border border-emerald-400/15 bg-emerald-400/[0.07] px-1.5 py-0.5 text-[8px] font-black tracking-[0.18em] text-emerald-300">ALPHA</span>
              </div>
              <div className="text-[10px] text-slate-500">Backend Fomo browser intelligence</div>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <div className="hidden items-center gap-2 rounded-lg border border-white/10 bg-white/[0.025] px-3 py-1.5 text-[10px] font-bold text-slate-400 sm:flex">
              <Database className="h-3.5 w-3.5 text-emerald-300" />
              BROWSER MONITOR
            </div>
            <button
              onClick={() => setSettingsOpen((value) => !value)}
              className="flex h-9 items-center gap-2 rounded-xl border border-white/10 bg-white/[0.035] px-3 text-xs font-bold text-white transition hover:border-white/20 hover:bg-white/[0.06]"
            >
              <Settings2 className="h-4 w-4" />
              <span className="hidden sm:inline">Настройки</span>
            </button>
          </div>
        </div>
      </header>

      <main className="relative z-10 mx-auto max-w-[1500px] px-4 py-6 sm:px-6 lg:px-8 lg:py-8">
        <section className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_340px]">
          <div className="space-y-6">
            <div className="overflow-hidden rounded-3xl border border-white/10 bg-[#0d0f13]/90 p-5 shadow-2xl shadow-black/20 sm:p-7">
              <div className="mb-6 flex flex-col justify-between gap-4 lg:flex-row lg:items-end">
                <div className="max-w-3xl">
                  <div className="mb-2 flex items-center gap-2 text-[10px] font-black uppercase tracking-[0.22em] text-emerald-300">
                    <Zap className="h-3.5 w-3.5" /> 24/7 backend monitor
                  </div>
                  <h1 className="text-2xl font-black tracking-tight text-white sm:text-3xl lg:text-4xl">
                    NEO следи Fomo и избира сам.
                  </h1>
                  <p className="mt-3 max-w-2xl text-sm leading-6 text-slate-400">
                    Python monitor на VPS наблюдава Fomo през browser, отваря coin страниците сам и записва симулирани позиции. Не е нужен extension.
                  </p>
                </div>
                <div className="flex items-center gap-2 text-[10px] text-slate-500">
                  <ShieldCheck className="h-4 w-4 text-emerald-300" />
                  Backend monitor работи 24/7 на VPS.
                </div>
              </div>

              <div className="grid gap-3 sm:grid-cols-3">
                <div className="rounded-2xl border border-white/10 bg-black/20 p-4"><div className="text-[9px] font-black uppercase tracking-[0.16em] text-slate-500">Backend</div><div className="mt-1 text-sm font-black text-white">{fomoMonitorError ? 'OFFLINE' : fomoMonitor?.status?.toUpperCase() || 'CONNECTING'}</div></div>
                <div className="rounded-2xl border border-white/10 bg-black/20 p-4"><div className="text-[9px] font-black uppercase tracking-[0.16em] text-slate-500">Fomo queue</div><div className="mt-1 text-sm font-black text-white">{fomoMonitor?.candidates.length ?? 0} coins</div></div>
                <div className="rounded-2xl border border-white/10 bg-black/20 p-4"><div className="text-[9px] font-black uppercase tracking-[0.16em] text-slate-500">Mode</div><div className="mt-1 text-sm font-black text-emerald-300">BACKGROUND MONITOR</div></div>
              </div>

              {error && (
                <div className="mt-4 flex items-start gap-3 rounded-xl border border-red-500/25 bg-red-500/[0.07] p-3 text-sm text-red-200">
                  <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
                  {error}
                </div>
              )}
            </div>

            {!assessment && !loading && (
              <div className="grid gap-4 md:grid-cols-3">
                <div className="rounded-2xl border border-white/10 bg-white/[0.025] p-5">
                  <ShieldAlert className="h-5 w-5 text-red-300" />
                  <h3 className="mt-4 font-bold text-white">Browser monitoring</h3>
                  <p className="mt-2 text-xs leading-5 text-slate-500">Python + Chromium следят Fomo директно от VPS, без extension и без ръчно избиране на coin.</p>
                </div>
                <div className="rounded-2xl border border-white/10 bg-white/[0.025] p-5">
                  <Activity className="h-5 w-5 text-amber-300" />
                  <h3 className="mt-4 font-bold text-white">Automatic selection</h3>
                  <p className="mt-2 text-xs leading-5 text-slate-500">NEO събира видимите coin карти, оценява ги и отваря най-силните кандидати сам.</p>
                </div>
                <div className="rounded-2xl border border-white/10 bg-white/[0.025] p-5">
                  <BrainCircuit className="h-5 w-5 text-emerald-300" />
                  <h3 className="mt-4 font-bold text-white">Paper tracking</h3>
                  <p className="mt-2 text-xs leading-5 text-slate-500">Входът и изходът са симулирани. Пазим стартова цена, текуща цена, PnL, причина и история.</p>
                </div>
              </div>
            )}

            {assessment && (
              <>
                <div className={`rounded-3xl border p-5 sm:p-6 ${postureStyles(assessment.posture)}`}>
                  <div className="flex flex-col justify-between gap-5 md:flex-row md:items-center">
                    <div>
                      <div className="text-[10px] font-black uppercase tracking-[0.2em] opacity-70">NEO DECISION POSTURE</div>
                      <div className="mt-1 text-4xl font-black tracking-[-0.05em]">{assessment.posture}</div>
                      <p className="mt-2 max-w-3xl text-sm leading-6 opacity-80">{assessment.summary}</p>
                    </div>
                    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
                      <div className="rounded-xl border border-current/10 bg-black/15 px-4 py-3 text-center">
                        <div className="text-[9px] font-black uppercase tracking-[0.15em] opacity-60">Critical</div>
                        <div className="mt-1 text-xl font-black">{criticalCount}</div>
                      </div>
                      <div className="rounded-xl border border-current/10 bg-black/15 px-4 py-3 text-center">
                        <div className="text-[9px] font-black uppercase tracking-[0.15em] opacity-60">Caution</div>
                        <div className="mt-1 text-xl font-black">{warningCount}</div>
                      </div>
                      <div className="col-span-2 rounded-xl border border-current/10 bg-black/15 px-4 py-3 text-center sm:col-span-1">
                        <div className="text-[9px] font-black uppercase tracking-[0.15em] opacity-60">Confidence</div>
                        <div className="mt-1 text-xl font-black">{assessment.confidenceScore}%</div>
                      </div>
                    </div>
                  </div>
                </div>

                <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                  <MetricCard
                    label="Risk Score"
                    value={<span className={assessment.riskScore >= 65 ? 'text-red-300' : assessment.riskScore >= 40 ? 'text-amber-200' : 'text-emerald-300'}>{assessment.riskScore}/100</span>}
                    detail="Higher = more risk flags"
                    icon={<ShieldAlert className="h-4 w-4" />}
                  />
                  <MetricCard
                    label="Liquidity / Cap"
                    value={`${liquidityRatio.toFixed(liquidityRatio >= 10 ? 1 : 2)}%`}
                    detail={`${formatMoney(assessment.market.liquidityUsd)} liquidity`}
                    icon={<CircleGauge className="h-4 w-4" />}
                  />
                  <MetricCard
                    label="Top 5 Holders"
                    value={assessment.holders ? `${assessment.holders.top5Pct.toFixed(1)}%` : 'N/A'}
                    detail={assessment.holders ? 'Raw top token accounts' : 'RPC holder scan unavailable'}
                    icon={<WalletCards className="h-4 w-4" />}
                  />
                  <MetricCard
                    label="Pair Age"
                    value={formatAge(assessment.market.pairCreatedAt)}
                    detail={`${assessment.market.buys1h + assessment.market.sells1h} txns in 1h`}
                    icon={<Clock3 className="h-4 w-4" />}
                  />
                </div>

                <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_360px]">
                  <div className="rounded-3xl border border-white/10 bg-[#0d0f13]/90 p-5 sm:p-6">
                    <div className="mb-5 flex items-center justify-between gap-3">
                      <div>
                        <div className="text-[10px] font-black uppercase tracking-[0.2em] text-slate-500">Signal engine</div>
                        <h2 className="mt-1 text-xl font-black text-white">Какво вижда NEO</h2>
                      </div>
                      <span className="rounded-lg border border-white/10 bg-white/[0.03] px-2.5 py-1 text-[10px] font-bold text-slate-400">
                        {assessment.signals.length} сигнала
                      </span>
                    </div>
                    <div className="space-y-2.5">
                      {assessment.signals.map((signal) => <SignalRow key={signal.id} signal={signal} />)}
                    </div>
                  </div>

                  <div className="space-y-4">
                    <div className="rounded-3xl border border-white/10 bg-[#0d0f13]/90 p-5">
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <div className="text-[10px] font-black uppercase tracking-[0.18em] text-slate-500">Token</div>
                          <h3 className="mt-1 truncate text-xl font-black text-white">{assessment.market.name}</h3>
                          <div className="mt-0.5 font-mono text-xs text-emerald-300">${assessment.market.symbol}</div>
                        </div>
                        <div className="rounded-xl border border-white/10 bg-white/[0.03] p-2.5">
                          <Sparkles className="h-4 w-4 text-emerald-300" />
                        </div>
                      </div>

                      <div className="mt-5 space-y-3 border-t border-white/10 pt-4 text-xs">
                        <div className="flex items-center justify-between gap-3">
                          <span className="text-slate-500">Market cap</span>
                          <span className="font-bold text-white">{formatMoney(assessment.market.marketCapUsd || assessment.market.fdvUsd)}</span>
                        </div>
                        <div className="flex items-center justify-between gap-3">
                          <span className="text-slate-500">1h volume</span>
                          <span className="font-bold text-white">{formatMoney(assessment.market.volume1hUsd)}</span>
                        </div>
                        <div className="flex items-center justify-between gap-3">
                          <span className="text-slate-500">1h buys / sells</span>
                          <span className="font-bold text-white">{assessment.market.buys1h} / {assessment.market.sells1h}</span>
                        </div>
                        <div className="flex items-center justify-between gap-3">
                          <span className="text-slate-500">Narrative</span>
                          <span className="font-bold capitalize text-white">{assessment.narrative.category.replace('-', ' ')}</span>
                        </div>
                        <div className="flex items-center justify-between gap-3">
                          <span className="text-slate-500">Fresh wallets</span>
                          <span className="font-bold text-white">
                            {assessment.holders?.sampledFreshWalletPct !== undefined
                              ? `${assessment.holders.sampledFreshWalletPct.toFixed(0)}% sampled`
                              : 'N/A'}
                          </span>
                        </div>
                      </div>

                      <div className="mt-5 flex gap-2">
                        <button
                          onClick={copyAddress}
                          className="flex h-9 flex-1 items-center justify-center gap-2 rounded-xl border border-white/10 bg-white/[0.03] text-[10px] font-black text-white transition hover:bg-white/[0.06]"
                        >
                          <Copy className="h-3.5 w-3.5" /> {shortAddress(assessment.tokenAddress)}
                        </button>
                        <a
                          href={`https://dexscreener.com/solana/${assessment.market.pairAddress || assessment.tokenAddress}`}
                          target="_blank"
                          rel="noreferrer"
                          className="flex h-9 w-10 items-center justify-center rounded-xl border border-white/10 bg-white/[0.03] text-white transition hover:bg-white/[0.06]"
                        >
                          <ExternalLink className="h-3.5 w-3.5" />
                        </a>
                      </div>
                    </div>

                    {assessment.holders && (
                      <div className="rounded-3xl border border-white/10 bg-[#0d0f13]/90 p-5">
                        <div className="mb-4 flex items-center gap-2">
                          <WalletCards className="h-4 w-4 text-emerald-300" />
                          <h3 className="text-sm font-black text-white">Top holder sample</h3>
                        </div>
                        <div className="space-y-2">
                          {assessment.holders.holders.slice(0, 5).map((holder, index) => (
                            <div key={holder.tokenAccount} className="flex items-center justify-between gap-3 rounded-xl border border-white/[0.07] bg-white/[0.02] px-3 py-2.5">
                              <div className="min-w-0">
                                <div className="text-[9px] font-bold uppercase tracking-[0.14em] text-slate-600">#{index + 1}</div>
                                <div className="truncate font-mono text-[10px] text-slate-400">{holder.owner ? shortAddress(holder.owner) : shortAddress(holder.tokenAccount)}</div>
                              </div>
                              <div className="text-right">
                                <div className="text-xs font-black text-white">{holder.percentage.toFixed(2)}%</div>
                                {holder.likelyFresh !== undefined && (
                                  <div className={`text-[9px] font-bold ${holder.likelyFresh ? 'text-red-300' : 'text-emerald-300'}`}>
                                    {holder.likelyFresh ? 'LIKELY FRESH' : 'ESTABLISHED'}
                                  </div>
                                )}
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                </div>

                {assessment.dataWarnings.length > 0 && (
                  <div className="rounded-2xl border border-amber-400/20 bg-amber-400/[0.05] p-4">
                    <div className="flex items-center gap-2 text-xs font-black text-amber-200">
                      <AlertTriangle className="h-4 w-4" /> DATA LIMITATIONS
                    </div>
                    <div className="mt-2 space-y-1 text-xs leading-5 text-amber-100/60">
                      {assessment.dataWarnings.map((warning) => <div key={warning}>• {warning}</div>)}
                    </div>
                  </div>
                )}
              </>
            )}
          </div>

          <aside className="space-y-4">
            {settingsOpen && (
              <div className="rounded-3xl border border-emerald-400/20 bg-[#0d1110] p-5 shadow-xl shadow-black/20">
                <div className="mb-5 flex items-start justify-between gap-3">
                  <div>
                    <div className="text-[10px] font-black uppercase tracking-[0.18em] text-emerald-300">Backend settings</div>
                    <h3 className="mt-1 text-lg font-black text-white">Fomo browser monitor</h3>
                  </div>
                  <Settings2 className="h-4 w-4 text-emerald-300" />
                </div>
                <div className="space-y-2 text-xs">
                  <div className="flex items-center justify-between rounded-xl border border-white/[0.07] bg-white/[0.025] p-3"><span className="text-slate-500">Scan</span><span className="font-black text-white">{fomoMonitor?.config.scan_seconds ?? 20}s</span></div>
                  <div className="flex items-center justify-between rounded-xl border border-white/[0.07] bg-white/[0.025] p-3"><span className="text-slate-500">Selection score</span><span className="font-black text-white">{fomoMonitor?.config.entry_score ?? 66}+</span></div>
                  <div className="flex items-center justify-between rounded-xl border border-white/[0.07] bg-white/[0.025] p-3"><span className="text-slate-500">Max simulations</span><span className="font-black text-white">{fomoMonitor?.config.max_positions ?? 2}</span></div>
                  <div className="flex items-center justify-between rounded-xl border border-white/[0.07] bg-white/[0.025] p-3"><span className="text-slate-500">Risk exits</span><span className="font-black text-white">-{fomoMonitor?.config.stop_loss_pct ?? 8}% / +{fomoMonitor?.config.take_profit_pct ?? 16}%</span></div>
                </div>
                <p className="mt-4 text-[10px] leading-4 text-slate-500">Fomo се наблюдава през Chromium на VPS. Няма extension и автоматичният monitor не използва външен market API.</p>
              </div>
            )}

            <div className="rounded-3xl border border-emerald-400/20 bg-[#0d1110] p-5 shadow-xl shadow-black/20">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="text-[10px] font-black uppercase tracking-[0.18em] text-emerald-300">Fomo backend monitor</div>
                  <h3 className="mt-1 text-base font-black text-white">NEO Fomo Monitor</h3>
                </div>
                <div className={`rounded-lg border px-2 py-1 text-[9px] font-black ${fomoMonitor?.status === 'monitoring' ? 'border-emerald-400/25 bg-emerald-400/10 text-emerald-200' : fomoMonitor?.login_required ? 'border-amber-400/25 bg-amber-400/10 text-amber-200' : 'border-white/10 bg-white/[0.03] text-slate-500'}`}>
                  {fomoMonitorError ? 'OFFLINE' : fomoMonitor?.login_required ? 'LOGIN' : fomoMonitor?.status?.toUpperCase() || 'CONNECTING'}
                </div>
              </div>
              <div className="mt-4 grid grid-cols-2 gap-2">
                <div className="rounded-xl border border-white/[0.07] bg-black/20 p-3"><div className="text-[9px] font-black uppercase tracking-[0.12em] text-slate-600">Queue</div><div className="mt-1 text-lg font-black text-white">{fomoMonitor?.candidates.length ?? 0}</div></div>
                <div className="rounded-xl border border-white/[0.07] bg-black/20 p-3"><div className="text-[9px] font-black uppercase tracking-[0.12em] text-slate-600">Open sims</div><div className="mt-1 text-lg font-black text-white">{fomoMonitor?.positions.length ?? 0}/{fomoMonitor?.config.max_positions ?? 2}</div></div>
                <div className="rounded-xl border border-white/[0.07] bg-black/20 p-3"><div className="text-[9px] font-black uppercase tracking-[0.12em] text-slate-600">Completed</div><div className="mt-1 text-lg font-black text-white">{fomoMonitor?.history.length ?? 0}</div></div>
                <div className="rounded-xl border border-white/[0.07] bg-black/20 p-3"><div className="text-[9px] font-black uppercase tracking-[0.12em] text-slate-600">Win rate</div><div className="mt-1 text-lg font-black text-white">{fomoMonitor?.history.length ? `${fomoWinRate.toFixed(0)}%` : '—'}</div></div>
              </div>
              <div className="mt-3 rounded-xl border border-white/[0.07] bg-white/[0.02] p-3 text-[10px] leading-4 text-slate-500">Python + Chromium на VPS · browser DOM monitoring · без extension · без външен market API · само симулация</div>
              {fomoMonitor?.login_required && <div className="mt-3 rounded-xl border border-amber-400/20 bg-amber-400/[0.06] p-3 text-[10px] leading-4 text-amber-100/80">Влез директно във Fomo от бутона отдолу. Login-ът се пази в persistent browser-а на VPS и после monitor-ът работи 24/7.</div>}
              {fomoMonitorError && <div className="mt-3 rounded-xl border border-red-500/20 bg-red-500/[0.06] p-3 text-[10px] leading-4 text-red-200">{fomoMonitorError}</div>}
              <button onClick={() => setFomoLoginOpen(true)} className="mt-3 flex h-11 w-full items-center justify-center gap-2 rounded-xl bg-emerald-400 text-xs font-black text-[#06100c] transition hover:bg-emerald-300"><ExternalLink className="h-4 w-4" /> {fomoMonitor?.login_required ? 'ВХОД ВЪВ FOMO' : 'ОТВОРИ FOMO LIVE'}</button>
              <button onClick={() => controlFomoMonitor(fomoMonitor?.running ? 'stop' : 'start')} disabled={!fomoMonitor || fomoMonitor.login_required} className={`mt-2 flex h-10 w-full items-center justify-center gap-2 rounded-xl text-xs font-black transition disabled:cursor-not-allowed disabled:opacity-45 ${fomoMonitor?.running ? 'border border-red-400/20 bg-red-500/10 text-red-200' : 'bg-emerald-400 text-[#06100c]'}`}><Zap className="h-4 w-4" /> {fomoMonitor?.running ? 'STOP MONITOR' : 'START MONITOR'}</button>
              <button onClick={() => controlFomoMonitor('rescan')} disabled={!fomoMonitor || fomoMonitor.login_required} className="mt-2 flex h-9 w-full items-center justify-center gap-2 rounded-xl border border-white/10 bg-white/[0.03] text-[10px] font-black text-white disabled:opacity-40"><RefreshCw className="h-3.5 w-3.5" /> FORCE SCAN</button>
              <div className="mt-3 text-[10px] leading-4 text-slate-500">{fomoMonitor?.message || 'Connecting to backend monitor…'}</div>
              {(fomoMonitor?.positions.length || 0) > 0 && <div className="mt-4 space-y-2 border-t border-white/[0.07] pt-4">{fomoMonitor?.positions.map((position) => { const pnlPct = position.pnl_pct ?? (position.entry_price > 0 ? ((position.current_price - position.entry_price) / position.entry_price) * 100 : 0); return <div key={position.id} className="rounded-xl border border-white/[0.07] bg-black/20 p-3"><div className="flex items-center justify-between gap-2"><div><div className="text-xs font-black text-white">${position.symbol}</div><div className="mt-0.5 text-[9px] text-slate-600">start ${position.entry_price.toPrecision(6)} · score {position.score.toFixed(0)}</div></div><div className={`text-xs font-black ${pnlPct >= 0 ? 'text-emerald-300' : 'text-red-300'}`}>{pnlPct >= 0 ? '+' : ''}{pnlPct.toFixed(2)}%</div></div></div>; })}</div>}
              <button onClick={() => controlFomoMonitor('reset')} className="mt-3 h-8 w-full rounded-lg border border-white/[0.07] bg-transparent text-[9px] font-black text-slate-500">RESET SIMULATION HISTORY</button>
            </div>

            <div className="rounded-3xl border border-white/10 bg-[#0d0f13]/90 p-5">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <div className="text-[10px] font-black uppercase tracking-[0.18em] text-slate-500">Fomo coin queue</div>
                  <h3 className="mt-1 text-base font-black text-white">Bot-а избира сам</h3>
                </div>
                {fomoMonitor?.status === 'monitoring' ? <Activity className="h-4 w-4 text-emerald-300" /> : <Loader2 className="h-4 w-4 text-slate-500" />}
              </div>
              <div className="mt-4 space-y-2">
                {(fomoMonitor?.candidates.length || 0) === 0 && <div className="rounded-xl border border-white/[0.07] bg-white/[0.02] p-3 text-xs text-slate-500">{fomoMonitor?.login_required ? 'Чака еднократен Fomo login за да вижда coin feed-а.' : 'Чака следващия scan на Fomo.'}</div>}
                {fomoMonitor?.candidates.slice(0, 9).map((coin, index) => (
                  <div key={coin.url} className="flex w-full items-center justify-between gap-3 rounded-xl border border-white/[0.07] bg-white/[0.02] px-3 py-3">
                    <div className="min-w-0">
                      <div className="text-[9px] font-black uppercase tracking-[0.12em] text-slate-600">AUTO #{index + 1}</div>
                      <div className="mt-0.5 truncate text-xs font-black text-white">${coin.symbol}</div>
                      <div className="mt-0.5 truncate text-[9px] text-slate-600">{coin.text}</div>
                    </div>
                    <div className="shrink-0 rounded-lg border border-emerald-400/15 bg-emerald-400/[0.06] px-2 py-1 text-[10px] font-black text-emerald-300">{coin.score.toFixed(0)}</div>
                  </div>
                ))}
              </div>
            </div>

            <div className="rounded-3xl border border-white/10 bg-gradient-to-b from-white/[0.035] to-transparent p-5">
              <div className="flex h-9 w-9 items-center justify-center rounded-xl border border-emerald-400/20 bg-emerald-400/10">
                <BrainCircuit className="h-4 w-4 text-emerald-300" />
              </div>
              <h3 className="mt-4 text-base font-black text-white">NEO не трябва да е “magic AI”.</h3>
              <p className="mt-2 text-xs leading-5 text-slate-500">
                Първо строим измерим engine: реални данни → ясни сигнали → confidence → решение. AI слой ще има само там, където реално добавя стойност.
              </p>
            </div>
          </aside>
        </section>

        <footer className="mt-8 flex flex-col justify-between gap-3 border-t border-white/[0.07] py-6 text-[10px] leading-5 text-slate-600 sm:flex-row">
          <div>NEO Meme Coins · analysis & risk intelligence · alpha</div>
          <div className="max-w-2xl sm:text-right">
            Meme coins са високорискови. NEO тук записва само симулирани позиции; реални BUY/SELL действия не се изпълняват.
          </div>
        </footer>
      </main>

      {fomoLoginOpen && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/80 p-2 backdrop-blur-sm sm:p-5">
          <div className="flex h-[94vh] w-full max-w-[1500px] flex-col overflow-hidden rounded-2xl border border-emerald-400/25 bg-[#07090b] shadow-2xl shadow-black">
            <div className="flex items-center justify-between gap-3 border-b border-white/10 px-4 py-3">
              <div>
                <div className="text-[10px] font-black uppercase tracking-[0.18em] text-emerald-300">Persistent VPS browser</div>
                <div className="mt-0.5 text-sm font-black text-white">Fomo Live Login & Monitoring</div>
              </div>
              <div className="flex items-center gap-2">
                <button onClick={() => { controlFomoMonitor('rescan'); setFomoLoginOpen(false); }} className="rounded-xl border border-emerald-400/20 bg-emerald-400/10 px-3 py-2 text-[10px] font-black text-emerald-200">ГОТОВО — ПРОВЕРИ LOGIN</button>
                <button onClick={() => setFomoLoginOpen(false)} className="flex h-9 w-9 items-center justify-center rounded-xl border border-white/10 bg-white/[0.04] text-white"><XCircle className="h-4 w-4" /></button>
              </div>
            </div>
            <div className="border-b border-white/[0.07] bg-amber-400/[0.05] px-4 py-2 text-[10px] leading-4 text-amber-100/70">Първо отключи remote browser-а с monitor access кода, после се логни във Fomo нормално. Fomo паролата не се въвежда в NEO — пишеш я директно в отдалечения Chromium.</div>
            <iframe title="Fomo persistent browser" src={FOMO_VNC_URL} className="min-h-0 flex-1 border-0 bg-black" allow="clipboard-read; clipboard-write" />
          </div>
        </div>
      )}
    </div>
  );
}
