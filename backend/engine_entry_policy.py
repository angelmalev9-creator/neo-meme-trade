"""Pure entry checks for the existing paper engine (never submits swaps).

The policy exposes rejection reasons; it does not promise trade frequency or
profitability. Exit rules and the Strategy Lab are deliberately outside it.
"""
from collections import Counter
import math
import re
from typing import Any

POLICY_VERSION = 'ORDER_FLOW_FAST_3_10_V5'
MAX_FEED_AGE_MS = 30_000
MAX_ENTRY_QUOTE_AGE_MS = 10_000
MAX_QUOTED_CANDIDATES = 2
_ADDRESS = re.compile(r'^[1-9A-HJ-NP-Za-km-z]{32,44}$')
LABELS = {
    'entry_error': 'проверката на входа не е завършена',
    'token_blocklisted': 'токен в забранителния списък',
    'blocklist_unavailable': 'забранителният списък не може да се провери',
    'unsupported_token_program': 'неподдържана token програма',
    'invalid_mint': 'невалиден mint',
    'mint_decimals': 'непотвърдени token единици',
    'token_extensions_unknown': 'непотвърдени token разширения',
    'rug_report_invalid_or_rugged': 'невалиден отчет или отбелязан rug',
    'rug_report_incomplete': 'непълен отчет за риска',
    'pool_not_verified': 'непотвърден точен pool',
    'holders_unverified': 'непотвърдени притежатели',
    'risk_schema_unverified': 'неразпозната структура на данните за риск',
    'risk_check_pending': 'проверка за rug риск',
    'risk_data_unavailable': 'липсват проверими данни за rug риск',
    'mint_authority': 'активно право за нови токени',
    'freeze_authority': 'активно право за замразяване',
    'unsupported_token_extension': 'неподдържано token разширение',
    'rugcheck_critical': 'критичен риск в RugCheck',
    'holder_concentration': 'концентрация на притежателите',
    'lp_control_risk': 'недостатъчно потвърдено заключване на ликвидността',
    'reported_linked_insiders': 'докладвани свързани вътрешни портфейли',
    'network_price_unknown': 'липсва оценка на мрежовия разход',
    'invalid_pair': 'невалиден token или pool',
    'invalid_price': 'липсва цена',
    'stale_feed': 'остарели пазарни данни',
    'score': 'ниска оценка',
    'liquidity': 'ниска ликвидност',
    'momentum': 'неподходящо движение за 5 минути',
    'hour_trend': 'силен спад или скок за час',
    'market_buyers': 'слаби пазарни покупки',
    'liquidity_ratio': 'недостатъчна ликвидност спрямо капитализацията',
    'flow_count': 'малко on-chain сделки',
    'flow_ratio': 'продажбите надделяват',
    'buy_volume': 'недостатъчен обем покупки',
    'wallet_count': 'малко различни портфейли',
    'buyer_count': 'малко различни купувачи',
    'wallet_ratio': 'повече продавачи от купувачи',
    'large_sells': 'големи продажби',
    'conviction': 'слаб потвърден сигнал',
    'cooldown': 'изчакване след предишна сделка',
    'entry_quote': 'няма валидна входна котировка през следения pool',
    'exit_quote': 'няма валидна котировка за продажба',
    'quote_age': 'входната котировка е остаряла',
    'invalid_quote': 'невалидни данни в котировката',
    'impact': 'твърде голямо ценово въздействие',
    'roundtrip_cost': 'твърде скъпи вход и изход',
    'worst_case_cost': 'недостатъчен запас до стопа',
    'quote_budget': 'изчакване на следващата проверка на котировките',
    'daily_limit': 'достигнат дневен лимит',
    'position_open': 'вече има отворена позиция',
    'balance': 'недостатъчен свободен баланс',
}


def number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError, OverflowError):
        return default


def signal_rejections(coin, flow, context, *, min_score, min_liquidity,
                      min_conviction, now):
    changes = coin.get('priceChange') or {}
    tx = (coin.get('txns') or {}).get('m5') or {}
    liq = number(coin.get('liquidityUsd'))
    mc = number(coin.get('marketCap') or coin.get('fdv'))
    observed = number(coin.get('updatedAt'))
    market_ratio = number(tx.get('buys')) / max(number(tx.get('sells')), 1)
    checks = {
        'invalid_pair': bool(_ADDRESS.fullmatch(str(coin.get('address') or '')))
                        and bool(_ADDRESS.fullmatch(str(coin.get('pairAddress') or ''))),
        'invalid_price': number(coin.get('priceUsd')) > 0,
        'stale_feed': observed > 0 and 0 <= now - observed <= MAX_FEED_AGE_MS,
        'score': number(coin.get('score')) >= min_score,
        'liquidity': liq >= min_liquidity,
        'momentum': -3.0 <= number(changes.get('m5'), -999) <= 40.0,
        'hour_trend': -40.0 <= number(changes.get('h1'), -999) <= 250.0,
        'market_buyers': market_ratio >= 1.0,
        'liquidity_ratio': mc > 0 and liq / mc >= 0.02,
        'flow_count': number(flow.get('trades')) >= 3,
        'flow_ratio': number(flow.get('buy_sell_usd_ratio')) >= 1.20,
        'buy_volume': number(flow.get('buy_usd')) >= 75.0,
        'wallet_count': number(flow.get('unique_wallets')) >= 3,
        'buyer_count': number(flow.get('buyer_wallets')) >= 2,
        'wallet_ratio': number(flow.get('wallet_buy_sell_ratio')) >= 1.0,
        'large_sells': number(flow.get('max_sell_usd')) < max(250.0, number(flow.get('buy_usd')) * 0.8),
        'conviction': number(context.get('conviction')) >= min_conviction,
    }
    return [key for key, passed in checks.items() if not passed]


def quote_rejections(entry, expected_roundtrip_pct, conservative_roundtrip_pct,
                     *, max_impact, max_cost, max_conservative_cost, now):
    impact = number(entry.get('price_impact_pct'), math.inf)
    quoted_at = number(entry.get('quoted_at'))
    reasons = []
    if number(entry.get('token_raw_expected')) <= 0 or not all(math.isfinite(number(x, math.nan)) for x in (expected_roundtrip_pct, conservative_roundtrip_pct)):
        reasons.append('invalid_quote')
    if quoted_at <= 0 or not 0 <= now - quoted_at <= MAX_ENTRY_QUOTE_AGE_MS:
        reasons.append('quote_age')
    if not math.isfinite(impact) or impact > max_impact:
        reasons.append('impact')
    if number(expected_roundtrip_pct, -math.inf) < -max_cost:
        reasons.append('roundtrip_cost')
    if number(conservative_roundtrip_pct, -math.inf) < -max_conservative_cost:
        reasons.append('worst_case_cost')
    return reasons


def record(report, reasons, coin=None, metrics=None):
    for reason in reasons:
        report['rejections'][reason] = report['rejections'].get(reason, 0) + 1
    if coin and len(report['examples']) < 8:
        report['examples'].append({'symbol': str(coin.get('symbol') or '')[:40],
                                   'reasons': list(reasons), 'metrics': metrics or {}})


def finish(report):
    counts = Counter(report['rejections'])
    if report['opened']:
        report['status'] = 'opened'
        report['message'] = 'Отворена е нова тестова позиция след проверка на котировките и разходите.'
    elif 'position_open' in counts:
        report['status'] = 'position_open'
        report['message'] = 'Следи отворената позиция. Не отваря втора едновременно.'
    elif 'daily_limit' in counts:
        report['status'] = 'daily_limit'
        report['message'] = 'Дневният лимит е достигнат. Новите входове са спрени.'
    else:
        report['status'] = 'waiting'
        top = counts.most_common(2)
        why = '; '.join(f'{LABELS.get(k, k)}: {v}' for k, v in top) or 'няма подходящ сигнал'
        report['message'] = (f"Проверени {report['evaluated']} от {report['candidates']} token-а; "
                             f"{report['signal_passed']} минаха входните филтри; "
                             f"{report['quoted']} проверки на котировки. Откази: {why}.")
    report['reason_labels'] = {k: LABELS.get(k, k) for k in counts}
    return report
