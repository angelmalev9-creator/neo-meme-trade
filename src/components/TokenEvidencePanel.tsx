import { AlertTriangle, CircleHelp, DatabaseZap, ShieldAlert } from 'lucide-react';

type CoinInput = {
  address: string;
  pairAddress: string;
  priceUsd: number;
  liquidityUsd: number;
  marketCap: number;
  fdv: number;
  score: number;
  riskScore: number;
  updatedAt: number;
  txns: { m5: { buys: number; sells: number } };
};
type HolderInput = {
  wallet: string | null;
  token_account: string;
  is_pool: boolean;
  share_pct: number | null;
};
type WalletInput = {
  at: number;
  holders: HolderInput[];
  holders_meta: { error: string | null; fetched_at: number | null; top_n: number };
  trade_sources: { rpc_live_rows: number; rpc_live_error: string | null; gecko_rows: number; tape_rows: number };
};
type FlowInput = { tape: { status?: string; covered_seconds: number; events: number } };
type EvidenceStatus = 'attention' | 'observed' | 'unknown';
type EvidenceItem = { title: string; status: EvidenceStatus; detail: string; source: string };

export function evaluateTokenEvidence(
  coin: CoinInput,
  wallets: WalletInput | null,
  flow: FlowInput | null,
  now = Date.now(),
): EvidenceItem[] {
  const age = wallets?.holders_meta.fetched_at ? now - wallets.holders_meta.fetched_at : null;
  const holdersFresh = age !== null && age >= 0 && age <= 120_000;
  const holderRows = wallets?.holders || [];
  const nonPoolHolders = holderRows.filter(row =>
    !row.is_pool && row.wallet && row.share_pct !== null &&
    Number.isFinite(row.share_pct) && row.share_pct >= 0 && row.share_pct <= 100
  );
  const maxShownShare = nonPoolHolders.length
    ? Math.max(...nonPoolHolders.map(row => row.share_pct!))
    : null;
  const sameOwner = new Map<string, number>();
  for (const holder of nonPoolHolders) {
    const key = holder.wallet!;
    sameOwner.set(key, (sameOwner.get(key) || 0) + holder.share_pct!);
  }
  const maxShownOwnerShare = sameOwner.size ? Math.max(...sameOwner.values()) : maxShownShare;
  const availableLiquidity = Number.isFinite(coin.liquidityUsd) && coin.liquidityUsd > 0;
  const priceFresh = coin.updatedAt > 0 && now >= coin.updatedAt && now - coin.updatedAt <= 120_000;
  const directRows = Math.max(0, wallets?.trade_sources.rpc_live_rows || 0);

  return [
    {
      title: 'Правомощия на токена',
      status: 'unknown',
      detail: 'В текущия фийд липсва независимо проверено доказателство за mint/freeze authority и Token-2022 разширения. Високият score не означава, че тези права са безопасни.',
      source: 'Нужни са потвърдени Solana account данни',
    },
    {
      title: 'Контрол над ликвидността',
      status: availableLiquidity ? 'observed' : 'attention',
      detail: availableLiquidity
        ? `Показана ликвидност: $${coin.liquidityUsd.toLocaleString('en-US', { maximumFractionDigits: 0 })}. Собствеността, заключването и възможността за изтегляне НЕ са потвърдени.`
        : 'Няма потвърдена положителна ликвидност в текущия пазарен snapshot.',
      source: 'Пазарен фийд; LP контролът остава непроверен',
    },
    {
      title: 'Концентрация на притежателите',
      status: wallets?.holders_meta.error || !holdersFresh || maxShownOwnerShare === null
        ? 'unknown' : maxShownOwnerShare > 20 ? 'attention' : 'observed',
      detail: wallets?.holders_meta.error || !holdersFresh || maxShownOwnerShare === null
        ? 'Няма достатъчно свежи on-chain данни за концентрацията. Липсващи холдъри НЕ означават равномерно разпределение.'
        : `Най-голям видим собственик извън обозначените пулове: ${maxShownOwnerShare.toFixed(2)}% от supply. Това е само сред показаните акаунти, не пълен одит на свързани портфейли.`,
      source: 'Solana RPC · показаните token account-и, групирани по видим собственик',
    },
    {
      title: 'Независимост на търговската активност',
      status: directRows > 0 ? 'observed' : 'unknown',
      detail: directRows > 0
        ? `${directRows} директно видими on-chain сделки в събрания прозорец. Различни адреси НЕ доказват независими участници и не изключват wash trading.`
        : 'Няма достатъчно директно потвърдени on-chain сделки. Броят покупки/продажби от агрегатор не е доказателство за органичен интерес.',
      source: 'RPC live · без доказателство за независим контрол над адресите',
    },
    {
      title: 'Покритие на wallet-flow',
      status: flow?.tape.status === 'online' && flow.tape.covered_seconds > 0 ? 'observed' : 'unknown',
      detail: flow?.tape.status === 'online' && flow.tape.covered_seconds > 0
        ? `Наблюдавани ${Math.round(flow.tape.covered_seconds)} секунди в tape. Покритието не доказва пълна история или липса на скрита активност.`
        : 'Покритието е неизвестно или недостъпно. Липсата на запис не означава нулева активност.',
      source: 'On-chain tape · частично наблюдение',
    },
    {
      title: 'Актуалност на пазарната цена',
      status: Number.isFinite(coin.priceUsd) && coin.priceUsd > 0 && priceFresh ? 'observed' : 'unknown',
      detail: Number.isFinite(coin.priceUsd) && coin.priceUsd > 0 && priceFresh
        ? 'Има актуална показана цена, но това не е гарантирана изпълнима котировка за покупка или продажба.'
        : 'Липсва достатъчно свежа положителна пазарна цена. Не може да се потвърди изпълнимостта на сделка.',
      source: 'Пазарен фийд · не е swap quote',
    },
    {
      title: 'Създател и свързани портфейли',
      status: 'unknown',
      detail: 'Няма доказателства за цялата история на създателя, свързаните портфейли и финансиращите връзки. Не се приема автоматично нисък риск.',
      source: 'Нужен е отделен on-chain граф на собствеността',
    },
  ];
}

export default function TokenEvidencePanel({
  coin, wallets, flow,
}: { coin: CoinInput; wallets: WalletInput | null; flow: FlowInput | null }) {
  const checks = evaluateTokenEvidence(coin, wallets, flow);
  const unknown = checks.filter(check => check.status === 'unknown').length;
  const attention = checks.filter(check => check.status === 'attention').length;
  const observed = checks.filter(check => check.status === 'observed').length;
  return <section data-testid="token-evidence-panel" className="overflow-hidden rounded-3xl border border-white/10 bg-[#0b0e11]">
    <div className="flex flex-wrap items-center justify-between gap-2 border-b border-white/[0.07] px-4 py-3">
      <div>
        <div className="text-[9px] font-black uppercase tracking-[0.16em] text-slate-600">Проверка на токена · само информация</div>
        <h2 className="mt-0.5 text-sm font-black text-white">Качество на доказателствата</h2>
      </div>
      <span className="rounded-lg border border-amber-400/20 bg-amber-400/[0.07] px-2.5 py-1 text-[10px] font-bold text-amber-100">
        {unknown} непотвърдени · {attention} предупреждения
      </span>
    </div>
    <div className="border-b border-white/[0.06] px-4 py-2.5 text-[10px] leading-5 text-slate-400">
      {observed} наблюдавани характеристики от 7 проверки. „Наблюдавано“ не означава „безопасно“.
      Пазарният score {Number.isFinite(coin.score) ? coin.score.toFixed(0) : '—'} не удостоверява
      собственост, ликвидност или бъдеща доходност.
    </div>
    <div className="grid gap-2 p-3 sm:grid-cols-2">
      {checks.map(check => <div key={check.title} className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-3">
        <div className="flex items-start gap-2">
          {check.status === 'attention' ? <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-red-300" />
            : check.status === 'unknown' ? <CircleHelp className="mt-0.5 h-4 w-4 shrink-0 text-amber-200" />
              : <DatabaseZap className="mt-0.5 h-4 w-4 shrink-0 text-cyan-300" />}
          <div className="min-w-0">
            <div className="text-[11px] font-black text-white">{check.title}</div>
            <div className="mt-1 text-[10px] leading-5 text-slate-400">{check.detail}</div>
            <div className="mt-2 text-[9px] text-slate-600">{check.source}</div>
          </div>
        </div>
      </div>)}
    </div>
    <div className="flex gap-2 border-t border-white/[0.06] px-4 py-3 text-[10px] leading-5 text-amber-200/75">
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
      Нито едно поле не е сигнал за покупка. Непотвърдените проверки не се считат за преминати; не се променят стратегии, стопове или поръчки.
    </div>
  </section>;
}
