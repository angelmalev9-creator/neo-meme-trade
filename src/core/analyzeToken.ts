import { fetchSolanaTokenMarket } from './providers/dexscreener';
import { getHolderSnapshot } from './providers/solanaRpc';
import { classifyNarrative } from './narrative';
import { getPriceHistory, recordPrice } from './localHistory';
import { scoreToken } from './scoring';
import type { DeviceSettings, RiskAssessment } from './types';

export async function analyzeToken(
  tokenAddress: string,
  settings: DeviceSettings,
  description = '',
): Promise<RiskAssessment> {
  const cleanAddress = tokenAddress.trim();
  if (!cleanAddress) throw new Error('Enter a Solana token address.');

  const market = await fetchSolanaTokenMarket(cleanAddress);
  const warnings: string[] = [];

  const historyBefore = getPriceHistory(cleanAddress);
  const history = market.priceUsd > 0
    ? recordPrice(cleanAddress, market.priceUsd)
    : historyBefore;

  let holders;
  try {
    holders = await getHolderSnapshot(cleanAddress, settings);
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Unknown RPC error';
    warnings.push(`Solana holder scan failed: ${message}`);
  }

  const narrative = classifyNarrative(market, description);
  return scoreToken({
    market,
    holders,
    narrative,
    priceHistory: history,
    dataWarnings: warnings,
  });
}
