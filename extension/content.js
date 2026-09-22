(() => {
  const BASE58_RE = /\b[1-9A-HJ-NP-Za-km-z]{32,44}\b/g;
  const SOCIAL_HOSTS = ['x.com', 'twitter.com', 't.me', 'telegram.me', 'discord.gg', 'discord.com', 'youtube.com', 'instagram.com', 'tiktok.com'];
  let lastToken = '';
  let scanning = false;
  let collapsed = false;
  let scanTimer = null;

  function short(value, size = 5) {
    const text = String(value || '');
    return text.length > size * 2 + 2 ? `${text.slice(0, size)}…${text.slice(-size)}` : text;
  }

  function esc(value) {
    return String(value ?? '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#039;');
  }

  function collectCandidates() {
    const scored = new Map();
    const add = (candidate, points, reason = '') => {
      if (!candidate || candidate.length < 32 || candidate.length > 44) return;
      const current = scored.get(candidate) || { points: 0, reasons: [] };
      current.points += points;
      if (reason) current.reasons.push(reason);
      scored.set(candidate, current);
    };

    const urlMatches = location.href.match(BASE58_RE) || [];
    urlMatches.forEach((value) => add(value, 100, 'url'));

    const selectors = [
      'a[href*="solscan.io/token/"]',
      'a[href*="pump.fun/coin/"]',
      'a[href*="dexscreener.com/solana/"]',
      '[data-token-address]',
      '[data-mint]',
      '[data-address]',
    ];

    for (const node of document.querySelectorAll(selectors.join(','))) {
      const parts = [
        node.getAttribute?.('href'),
        node.getAttribute?.('data-token-address'),
        node.getAttribute?.('data-mint'),
        node.getAttribute?.('data-address'),
        node.textContent,
      ].filter(Boolean).join(' ');
      const matches = parts.match(BASE58_RE) || [];
      const bonus = parts.includes('solscan.io/token/') || parts.includes('pump.fun/coin/') ? 80 : 48;
      matches.forEach((value) => add(value, bonus, 'token-link'));
    }

    const visibleText = (document.body?.innerText || '').slice(0, 120_000);
    const keywordChunks = visibleText.match(/(?:CA|Contract|Mint|Token)[^\n]{0,120}/gi) || [];
    for (const chunk of keywordChunks.slice(0, 80)) {
      (chunk.match(BASE58_RE) || []).forEach((value) => add(value, 65, 'label'));
    }

    const sorted = [...scored.entries()].sort((a, b) => b[1].points - a[1].points);
    return sorted[0]?.[0] || '';
  }

  function collectPageContext() {
    const socialLinks = [];
    for (const anchor of document.querySelectorAll('a[href]')) {
      const href = anchor.href || '';
      try {
        const host = new URL(href).hostname.toLowerCase();
        if (SOCIAL_HOSTS.some((allowed) => host === allowed || host.endsWith(`.${allowed}`))) socialLinks.push(href);
      } catch {
        // Ignore malformed links.
      }
      if (socialLinks.length >= 20) break;
    }

    return {
      source: location.hostname.includes('axiom.trade') ? 'Axiom' : 'Photon',
      url: location.href,
      title: document.title,
      text: (document.body?.innerText || '').slice(0, 20_000),
      socialLinks: [...new Set(socialLinks)],
    };
  }

  const host = document.createElement('div');
  host.id = 'neo-meme-coins-root';
  host.style.all = 'initial';
  host.style.position = 'fixed';
  host.style.zIndex = '2147483647';
  host.style.right = '14px';
  host.style.top = '78px';
  document.documentElement.appendChild(host);
  const shadow = host.attachShadow({ mode: 'open' });

  shadow.innerHTML = `
    <style>
      *{box-sizing:border-box}button,a{font:inherit}button{cursor:pointer}
      .neo{width:360px;max-height:calc(100vh - 96px);overflow:hidden;border:1px solid rgba(255,255,255,.12);border-radius:18px;background:rgba(7,9,12,.97);color:#e8edf2;font-family:Inter,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;box-shadow:0 20px 60px rgba(0,0,0,.45);backdrop-filter:blur(18px)}
      .head{display:flex;align-items:center;gap:10px;padding:12px 13px;border-bottom:1px solid rgba(255,255,255,.08)}
      .mark{width:32px;height:32px;display:grid;place-items:center;border-radius:10px;background:#34d399;color:#06100c;font-weight:1000}
      .brand{font-size:12px;font-weight:950;color:#fff}.sub{font-size:9px;color:#657081;margin-top:2px}.source{margin-left:auto;font-size:8px;font-weight:900;letter-spacing:.1em;color:#6ee7b7;border:1px solid rgba(52,211,153,.22);padding:4px 6px;border-radius:7px}
      .collapse{border:0;background:transparent;color:#7d8796;font-size:16px;padding:2px 4px}.body{max-height:calc(100vh - 150px);overflow:auto;padding:12px}.body::-webkit-scrollbar{width:5px}.body::-webkit-scrollbar-thumb{background:#29303a;border-radius:4px}
      .hidden{display:none}.status{border:1px solid rgba(255,255,255,.08);background:rgba(255,255,255,.025);border-radius:14px;padding:12px;font-size:10px;line-height:1.55;color:#8f9bab}
      .spin{display:inline-block;width:10px;height:10px;margin-right:7px;border:2px solid rgba(52,211,153,.25);border-top-color:#34d399;border-radius:999px;animation:neoSpin .8s linear infinite}@keyframes neoSpin{to{transform:rotate(360deg)}}
      .decision{border:1px solid rgba(255,255,255,.09);border-radius:15px;padding:12px}.decision.skip{border-color:rgba(248,113,113,.28);background:rgba(248,113,113,.06)}.decision.wait{border-color:rgba(251,146,60,.28);background:rgba(251,146,60,.06)}.decision.watch{border-color:rgba(251,191,36,.24);background:rgba(251,191,36,.05)}.decision.setup{border-color:rgba(52,211,153,.25);background:rgba(52,211,153,.06)}
      .eyebrow{font-size:8px;font-weight:950;letter-spacing:.15em;color:#697486}.posture{font-size:27px;line-height:1;font-weight:1000;letter-spacing:-.04em;color:white;margin-top:5px}.token{font-size:10px;color:#909aaa;margin-top:5px}.row{display:flex;gap:7px;margin-top:8px}.metric{flex:1;border:1px solid rgba(255,255,255,.08);border-radius:12px;background:rgba(255,255,255,.02);padding:9px}.metric span{display:block;font-size:7px;font-weight:900;letter-spacing:.12em;color:#657081}.metric strong{display:block;margin-top:5px;font-size:13px;color:white}.forecast{margin-top:8px;border:1px solid rgba(96,165,250,.18);background:rgba(96,165,250,.05);border-radius:14px;padding:11px}.forecast-top{display:flex;justify-content:space-between;gap:8px}.forecast strong{font-size:12px;color:#bfdbfe}.forecast-score{font-size:10px;font-weight:900;color:#93c5fd}.forecast p{font-size:9px;line-height:1.45;color:#7f8da0;margin:6px 0 0}
      .label{margin:12px 2px 7px;font-size:8px;font-weight:950;letter-spacing:.15em;color:#667183}.signals{display:grid;gap:6px}.signal{border:1px solid rgba(255,255,255,.07);border-radius:11px;background:rgba(255,255,255,.02);padding:9px}.signal-top{display:flex;gap:7px;justify-content:space-between}.signal b{font-size:10px;color:white}.pts{font-size:9px;font-weight:950}.signal p{margin:4px 0 0;font-size:8.5px;line-height:1.45;color:#7b8797}.signal.critical{border-color:rgba(248,113,113,.18)}.signal.critical .pts{color:#fca5a5}.signal.warning{border-color:rgba(251,191,36,.15)}.signal.warning .pts{color:#fde68a}.signal.positive{border-color:rgba(52,211,153,.15)}.signal.positive .pts{color:#6ee7b7}
      .socials{display:flex;flex-wrap:wrap;gap:6px}.social{display:inline-flex;align-items:center;min-height:28px;border:1px solid rgba(255,255,255,.09);border-radius:9px;padding:0 8px;background:rgba(255,255,255,.03);color:#dce3ea;text-decoration:none;font-size:8.5px;font-weight:800}.social:hover{border-color:rgba(52,211,153,.25);color:#6ee7b7}
      .factors{display:grid;grid-template-columns:1fr 1fr;gap:7px}.factor{border:1px solid rgba(255,255,255,.07);border-radius:11px;padding:8px;font-size:8.5px;line-height:1.5;color:#8390a1}.factor b{display:block;font-size:8px;letter-spacing:.1em;margin-bottom:3px}.bull b{color:#6ee7b7}.bear b{color:#fca5a5}
      .actions{display:flex;gap:7px;margin-top:10px}.btn{height:34px;flex:1;border:1px solid rgba(255,255,255,.1);border-radius:10px;background:rgba(255,255,255,.035);color:#fff;font-size:9px;font-weight:900}.btn.primary{border:0;background:#34d399;color:#06100c}.warn{margin-top:8px;border:1px solid rgba(251,191,36,.16);background:rgba(251,191,36,.05);border-radius:11px;padding:8px;font-size:8.5px;line-height:1.45;color:#d8bd70}.foot{margin-top:9px;font-size:8px;line-height:1.45;color:#596474;text-align:center}
      .collapsed .body{display:none}.collapsed{width:210px}.collapsed .sub,.collapsed .source{display:none}
    </style>
    <section class="neo">
      <div class="head">
        <div class="mark">N</div>
        <div><div class="brand">NEO Meme Coins</div><div class="sub">Live coin intelligence</div></div>
        <div class="source">AUTO</div>
        <button class="collapse" title="Свий">−</button>
      </div>
      <div class="body">
        <div class="status">Отвори coin в Photon или Axiom. NEO ще намери contract address-а и ще анализира автоматично.</div>
      </div>
    </section>
  `;

  const panel = shadow.querySelector('.neo');
  const body = shadow.querySelector('.body');
  const sourceEl = shadow.querySelector('.source');
  const collapseBtn = shadow.querySelector('.collapse');

  sourceEl.textContent = location.hostname.includes('axiom.trade') ? 'AXIOM' : 'PHOTON';
  collapseBtn.addEventListener('click', () => {
    collapsed = !collapsed;
    panel.classList.toggle('collapsed', collapsed);
    collapseBtn.textContent = collapsed ? '+' : '−';
  });

  function renderLoading(token) {
    body.innerHTML = `<div class="status"><span class="spin"></span>Анализирам ${esc(short(token))}: market, holders, wallet funding, socials и momentum…</div>`;
  }

  function renderError(message) {
    body.innerHTML = `
      <div class="status" style="border-color:rgba(248,113,113,.2);color:#fca5a5">${esc(message)}</div>
      <div class="actions"><button class="btn primary" id="neoRetry">ОПИТАЙ ПАК</button></div>
    `;
    shadow.getElementById('neoRetry')?.addEventListener('click', () => scheduleScan(true));
  }

  function render(result) {
    const socials = result.social?.links || [];
    const bull = result.outlook?.bull || [];
    const bear = result.outlook?.bear || [];
    const fresh = result.holders?.sampledFreshWalletPct;
    const linked = result.holders?.sharedFunderClusters?.[0];
    const confidence = result.confidence ?? 0;

    body.innerHTML = `
      <div class="decision ${esc(String(result.posture || '').toLowerCase())}">
        <div class="eyebrow">NEO POSTURE · CONFIDENCE ${esc(confidence)}%</div>
        <div class="posture">${esc(result.posture)}</div>
        <div class="token">${esc(result.market?.name)} · $${esc(result.market?.symbol)} · ${esc(short(result.tokenAddress))}</div>
      </div>
      <div class="row">
        <div class="metric"><span>RISK</span><strong>${esc(result.risk)}/100</strong></div>
        <div class="metric"><span>LIQ/CAP</span><strong>${esc((result.liquidityRatio || 0).toFixed((result.liquidityRatio || 0) >= 10 ? 1 : 2))}%</strong></div>
        <div class="metric"><span>TOP 5</span><strong>${result.holders ? `${esc(result.holders.top5Pct.toFixed(1))}%` : 'N/A'}</strong></div>
      </div>
      <div class="row">
        <div class="metric"><span>FRESH SAMPLE</span><strong>${fresh === null || fresh === undefined ? 'N/A' : `${esc(fresh.toFixed(0))}%`}</strong></div>
        <div class="metric"><span>LINKED WALLETS</span><strong>${linked ? esc(linked.walletCount) : '0'}</strong></div>
        <div class="metric"><span>NARRATIVE</span><strong style="font-size:9px">${esc(result.narrative?.category || 'unknown')}</strong></div>
      </div>
      <div class="forecast">
        <div class="forecast-top"><strong>${esc(result.outlook?.label || 'MIXED')}</strong><span class="forecast-score">${esc(result.outlook?.score ?? 50)}/100</span></div>
        <p>${esc(result.outlook?.horizon || '15–60m evidence window')} · confidence ${esc(result.outlook?.confidence ?? 0)}%. ${esc(result.outlook?.note || '')}</p>
      </div>
      <div class="label">BULL / BEAR EVIDENCE</div>
      <div class="factors">
        <div class="factor bull"><b>BULL</b>${bull.length ? bull.map((x) => `• ${esc(x)}`).join('<br>') : '• няма силен bull сигнал'}</div>
        <div class="factor bear"><b>BEAR</b>${bear.length ? bear.map((x) => `• ${esc(x)}`).join('<br>') : '• няма силен bear сигнал'}</div>
      </div>
      <div class="label">СОЦИАЛКИ / PROJECT LINKS</div>
      <div class="socials">${socials.length ? socials.slice(0, 8).map((item) => `<a class="social" href="${esc(item.url)}" target="_blank" rel="noreferrer">${esc(item.type)}</a>`).join('') : '<span style="font-size:9px;color:#667183">Няма намерени линкове.</span>'}</div>
      <div class="label">КАКВО ВИЖДА NEO</div>
      <div class="signals">${(result.signals || []).slice(0, 8).map((signal) => `
        <div class="signal ${esc(signal.severity || '')}">
          <div class="signal-top"><b>${esc(signal.label)}</b><span class="pts">${signal.points > 0 ? '+' : ''}${esc(signal.points)}</span></div>
          <p>${esc(signal.detail)}</p>
        </div>`).join('')}</div>
      ${result.holderError ? `<div class="warn">Holder/RPC частта е непълна: ${esc(result.holderError)}. NEO няма да даде SETUP при недостатъчно evidence.</div>` : ''}
      <div class="actions">
        <button class="btn primary" id="neoRescan">REFRESH SCAN</button>
        <button class="btn" id="neoCopy">COPY CA</button>
      </div>
      <div class="foot">NEO е decision-support. Не чете seed/private keys и не изпълнява сделки.</div>
    `;

    shadow.getElementById('neoRescan')?.addEventListener('click', () => scheduleScan(true));
    shadow.getElementById('neoCopy')?.addEventListener('click', async () => {
      try { await navigator.clipboard.writeText(result.tokenAddress); } catch { /* noop */ }
    });
  }

  async function scan(force = false) {
    if (scanning) return;
    const token = collectCandidates();
    if (!token) {
      if (force) renderError('Не намерих contract address на текущия екран. Отвори конкретен coin или token details view.');
      return;
    }
    if (!force && token === lastToken) return;

    lastToken = token;
    scanning = true;
    renderLoading(token);
    try {
      const response = await chrome.runtime.sendMessage({
        type: 'NEO_ANALYZE',
        input: token,
        pageContext: collectPageContext(),
      });
      if (!response?.ok) throw new Error(response?.error || 'Unknown scan error');
      render(response.result);
    } catch (error) {
      renderError(error instanceof Error ? error.message : String(error));
    } finally {
      scanning = false;
    }
  }

  function scheduleScan(force = false) {
    clearTimeout(scanTimer);
    scanTimer = setTimeout(() => scan(force), force ? 100 : 900);
  }

  const observer = new MutationObserver(() => scheduleScan(false));
  observer.observe(document.documentElement, { childList: true, subtree: true, attributes: true, attributeFilter: ['href', 'data-token-address', 'data-mint', 'data-address'] });

  const originalPushState = history.pushState;
  const originalReplaceState = history.replaceState;
  history.pushState = function (...args) { const value = originalPushState.apply(this, args); scheduleScan(false); return value; };
  history.replaceState = function (...args) { const value = originalReplaceState.apply(this, args); scheduleScan(false); return value; };
  window.addEventListener('popstate', () => scheduleScan(false));
  window.addEventListener('hashchange', () => scheduleScan(false));

  scheduleScan(false);
})();
