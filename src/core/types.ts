export type SignalSeverity = 'critical' | 'warning' | 'positive' | 'info';

export type TradePosture = 'SKIP' | 'WAIT' | 'WATCH' | 'SETUP';

export interface AnalysisSignal {
  id: string;
  title: string;
  detail: string;
  severity: SignalSeverity;
  riskPoints: number;
  confidence: number;
}

export interface MarketSnapshot {
  tokenAddress: string;
  pairAddress?: string;
  name: string;
  symbol: string;
  priceUsd: number;
  marketCapUsd: number;
  fdvUsd: number;
  liquidityUsd: number;
  volume5mUsd: number;
  volume1hUsd: number;
  volume24hUsd: number;
  buys5m: number;
  sells5m: number;
  buys1h: number;
  sells1h: number;
  priceChange5m: number;
  priceChange1h: number;
  priceChange24h: number;
  pairCreatedAt?: number;
  dexId?: string;
  websiteCount: number;
  socials: string[];
  boostsActive: number;
}

export interface HolderAccount {
  tokenAccount: string;
  owner?: string;
  amountRaw: string;
  uiAmount: number;
  percentage: number;
  likelyFresh?: boolean;
  sampledSignatureCount?: number;
  oldestSampledSignatureAt?: number;
}

export interface HolderSnapshot {
  supply: number;
  decimals: number;
  holders: HolderAccount[];
  top1Pct: number;
  top5Pct: number;
  top10Pct: number;
  sampledFreshWalletPct?: number;
  dataComplete: boolean;
}

export type NarrativeCategory =
  | 'pure-meme'
  | 'culture'
  | 'community'
  | 'news'
  | 'celebrity'
  | 'tech'
  | 'art'
  | 'unknown';

export interface NarrativeSnapshot {
  category: NarrativeCategory;
  confidence: number;
  matchedKeywords: string[];
  explanation: string;
}

export interface LocalPricePoint {
  ts: number;
  priceUsd: number;
}

export interface RiskAssessment {
  tokenAddress: string;
  generatedAt: number;
  market: MarketSnapshot;
  holders?: HolderSnapshot;
  narrative: NarrativeSnapshot;
  riskScore: number;
  qualityScore: number;
  confidenceScore: number;
  posture: TradePosture;
  signals: AnalysisSignal[];
  summary: string;
  dataWarnings: string[];
}

export interface DeviceSettings {
  rpcUrl: string;
  deepWalletScan: boolean;
  freshWalletMaxAgeHours: number;
  freshWalletMaxSampledSignatures: number;
}

export const DEFAULT_DEVICE_SETTINGS: DeviceSettings = {
  rpcUrl: 'https://api.mainnet.solana.com',
  deepWalletScan: true,
  freshWalletMaxAgeHours: 24,
  freshWalletMaxSampledSignatures: 40,
};
