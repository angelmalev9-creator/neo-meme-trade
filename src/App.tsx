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
import { fetchLatestSolanaProfiles } from './core/providers/dexscreener';
import {
  DEFAULT_DEVICE_SETTINGS,
  type AnalysisSignal,
  type DeviceSettings,
  type RiskAssessment,
  type SignalSeverity,
} from './core/types';

const SETTINGS_KEY = 'neo-meme-coins-settings-v1';

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
  const [latestProfiles, setLatestProfiles] = useState<Array<{ tokenAddress: string; description: string }>>([]);
  const [latestLoading, setLatestLoading] = useState(true);
  const [descriptionByAddress, setDescriptionByAddress] = useState<Record<string, string>>({});

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
    (async () => {
      setLatestLoading(true);
      try {
        const profiles = await fetchLatestSolanaProfiles(14);
        if (!cancelled) {
          setLatestProfiles(profiles);
          setDescriptionByAddress(Object.fromEntries(profiles.map((p) => [p.tokenAddress, p.description])));
        }
      } catch {
        if (!cancelled) setLatestProfiles([]);
      } finally {
        if (!cancelled) setLatestLoading(false);
      }
    })();
    return () => { cancelled = true; };
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
      const result = await analyzeToken(clean, settings, descriptionByAddress[clean] || '');
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
              <div className="text-[10px] text-slate-500">Device-first Solana intelligence</div>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <div className="hidden items-center gap-2 rounded-lg border border-white/10 bg-white/[0.025] px-3 py-1.5 text-[10px] font-bold text-slate-400 sm:flex">
              <Database className="h-3.5 w-3.5 text-emerald-300" />
              NO OWNER API KEY
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
                    <Zap className="h-3.5 w-3.5" /> Live deterministic scan
                  </div>
                  <h1 className="text-2xl font-black tracking-tight text-white sm:text-3xl lg:text-4xl">
                    Не гадай. Провери риска преди вход.
                  </h1>
                  <p className="mt-3 max-w-2xl text-sm leading-6 text-slate-400">
                    NEO комбинира DEX market data, on-chain holder distribution, fresh-wallet sampling и локални chart сигнали директно през твоето устройство.
                  </p>
                </div>
                <div className="flex items-center gap-2 text-[10px] text-slate-500">
                  <ShieldCheck className="h-4 w-4 text-emerald-300" />
                  Ключове и RPC настройки се пазят локално.
                </div>
              </div>

              <div className="flex flex-col gap-3 sm:flex-row">
                <div className="relative flex-1">
                  <WalletCards className="absolute left-4 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
                  <input
                    value={tokenAddress}
                    onChange={(event) => setTokenAddress(event.target.value)}
                    onKeyDown={(event) => { if (event.key === 'Enter') runAnalysis(); }}
                    placeholder="Solana token contract address…"
                    className="h-13 w-full rounded-2xl border border-white/10 bg-black/30 pl-11 pr-4 font-mono text-sm text-white outline-none transition placeholder:text-slate-600 focus:border-emerald-400/40 focus:ring-4 focus:ring-emerald-400/[0.05]"
                  />
                </div>
                <button
                  onClick={() => runAnalysis()}
                  disabled={loading}
                  className="flex h-13 min-w-[170px] items-center justify-center gap-2 rounded-2xl bg-emerald-400 px-5 text-sm font-black text-[#06100c] transition hover:bg-emerald-300 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Radar className="h-4 w-4" />}
                  {loading ? 'АНАЛИЗИРАМ…' : 'АНАЛИЗИРАЙ'}
                </button>
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
                  <h3 className="mt-4 font-bold text-white">Rug / bundle risk</h3>
                  <p className="mt-2 text-xs leading-5 text-slate-500">Holder concentration, fresh-wallet sampling, liquidity depth и подозрителни market ratios.</p>
                </div>
                <div className="rounded-2xl border border-white/10 bg-white/[0.025] p-5">
                  <Activity className="h-5 w-5 text-amber-300" />
                  <h3 className="mt-4 font-bold text-white">Fake-volume heuristics</h3>
                  <p className="mt-2 text-xs leading-5 text-slate-500">Следим transaction imbalance, volume/liquidity extremes и локално научен staircase pattern.</p>
                </div>
                <div className="rounded-2xl border border-white/10 bg-white/[0.025] p-5">
                  <BrainCircuit className="h-5 w-5 text-emerald-300" />
                  <h3 className="mt-4 font-bold text-white">Trader discipline</h3>
                  <p className="mt-2 text-xs leading-5 text-slate-500">Резултатът е SKIP / WAIT / WATCH / SETUP, а не обещание за печалба или импулсивен BUY сигнал.</p>
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
                    <div className="text-[10px] font-black uppercase tracking-[0.18em] text-emerald-300">Device settings</div>
                    <h3 className="mt-1 text-lg font-black text-white">Локален data access</h3>
                  </div>
                  <Settings2 className="h-4 w-4 text-emerald-300" />
                </div>

                <label className="block text-[10px] font-black uppercase tracking-[0.14em] text-slate-500">Solana RPC URL</label>
                <input
                  value={settings.rpcUrl}
                  onChange={(event) => saveSettings({ ...settings, rpcUrl: event.target.value })}
                  className="mt-2 h-10 w-full rounded-xl border border-white/10 bg-black/30 px-3 font-mono text-[10px] text-white outline-none focus:border-emerald-400/40"
                />
                <p className="mt-2 text-[10px] leading-4 text-slate-500">
                  Default public RPC е безплатен, но е rate-limited. За по-сериозна употреба можеш да поставиш собствен RPC URL; пази се само в този browser.
                </p>

                <label className="mt-5 flex cursor-pointer items-center justify-between gap-4 rounded-xl border border-white/[0.07] bg-white/[0.025] p-3">
                  <div>
                    <div className="text-xs font-bold text-white">Deep wallet sample</div>
                    <div className="mt-1 text-[10px] leading-4 text-slate-500">Проверява кратка история на част от top-holder owners.</div>
                  </div>
                  <input
                    type="checkbox"
                    checked={settings.deepWalletScan}
                    onChange={(event) => saveSettings({ ...settings, deepWalletScan: event.target.checked })}
                    className="h-4 w-4 accent-emerald-400"
                  />
                </label>

                <button
                  onClick={() => saveSettings(DEFAULT_DEVICE_SETTINGS)}
                  className="mt-4 flex h-9 w-full items-center justify-center gap-2 rounded-xl border border-white/10 bg-white/[0.03] text-[10px] font-black text-white transition hover:bg-white/[0.06]"
                >
                  <RefreshCw className="h-3.5 w-3.5" /> RESET DEFAULTS
                </button>
              </div>
            )}

            <div className="rounded-3xl border border-white/10 bg-[#0d0f13]/90 p-5">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <div className="text-[10px] font-black uppercase tracking-[0.18em] text-slate-500">Latest Solana profiles</div>
                  <h3 className="mt-1 text-base font-black text-white">Quick scan queue</h3>
                </div>
                {latestLoading ? <Loader2 className="h-4 w-4 animate-spin text-slate-500" /> : <Activity className="h-4 w-4 text-emerald-300" />}
              </div>

              <div className="mt-4 space-y-2">
                {latestProfiles.length === 0 && !latestLoading && (
                  <div className="rounded-xl border border-white/[0.07] bg-white/[0.02] p-3 text-xs text-slate-500">Latest feed is temporarily unavailable.</div>
                )}
                {latestProfiles.slice(0, 9).map((profile, index) => (
                  <button
                    key={profile.tokenAddress}
                    onClick={() => runAnalysis(profile.tokenAddress)}
                    disabled={loading}
                    className="group flex w-full items-center justify-between gap-3 rounded-xl border border-white/[0.07] bg-white/[0.02] px-3 py-3 text-left transition hover:border-emerald-400/20 hover:bg-emerald-400/[0.04] disabled:opacity-50"
                  >
                    <div className="min-w-0">
                      <div className="text-[9px] font-black uppercase tracking-[0.12em] text-slate-600">NEW #{index + 1}</div>
                      <div className="mt-0.5 truncate font-mono text-[10px] text-slate-400">{shortAddress(profile.tokenAddress)}</div>
                    </div>
                    <ChevronRight className="h-4 w-4 text-slate-600 transition group-hover:translate-x-0.5 group-hover:text-emerald-300" />
                  </button>
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
            Meme coins са високорискови. NEO показва измерими сигнали и несигурност; не гарантира печалба и не трябва да изпълнява сделка без изрично действие от потребителя.
          </div>
        </footer>
      </main>
    </div>
  );
}
