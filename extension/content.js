(() => {
  const BASE58_RE = /\b[1-9A-HJ-NP-Za-km-z]{32,44}\b/g;
  const SOCIAL_HOSTS = [
    'x.com', 'twitter.com', 't.me', 'telegram.me', 'discord.gg', 'discord.com',
    'youtube.com', 'youtu.be', 'instagram.com', 'tiktok.com',
  ];

  let lastToken = '';
  let lastResult = null;
  let scanning = false;
  let collapsed = false;
  let scanTimer = null;
  let lastSeenUrl = location.href;

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

  function sourceName() {
    const host = location.hostname.toLowerCase();
    if (host === 'fomo.family' || host.endsWith('.fomo.family')) return 'FOMO';
    if (host.includes('axiom.trade')) return 'AXIOM';
    if (host.includes('tinyastro.io')) return 'PHOTON';
    return 'TERMINAL';
  }

  function addCandidate(map, raw, points, reason, context = '') {
    const values = String(raw || '').match(BASE58_RE) || [];
    for (const candidate of values) {
      const current = map.get(candidate) || { token: candidate, score: 0, reasons: [], context: [] };
      current.score += points;
      if (reason && !current.reasons.includes(reason)) current.reasons.push(reason);
      if (context && current.context.length < 5) current.context.push(context.slice(0, 180));
      map.set(candidate, current);
    }
  }

  function collectCandidates() {
    const scored = new Map();

    addCandidate(scored, location.href, 180, 'url', location.pathname);

    const selector = [
      'a[href]',
      '[data-token-address]',
      '[data-mint]',
      '[data-address]',
      '[data-ca]',
      '[data-contract]',
      '[data-token]',
    ].join(',');

    const nodes = [...document.querySelectorAll(selector)].slice(0, 3500);
    for (const node of nodes) {
      const href = node.getAttribute?.('href') || '';
      const attrs = [
        node.getAttribute?.('data-token-address'),
        node.getAttribute?.('data-mint'),
        node.getAttribute?.('data-address'),
        node.getAttribute?.('data-ca'),
        node.getAttribute?.('data-contract'),
        node.getAttribute?.('data-token'),
      ].filter(Boolean).join(' ');
      const text = String(node.textContent || '').trim().slice(0, 220);
      const combined = `${href} ${attrs} ${text}`;

      const lower = combined.toLowerCase();
      let points = 24;
      let reason = 'page-link';

      if (lower.includes('solscan.io/token/') || lower.includes('pump.fun/coin/') || lower.includes('dexscreener.com/solana/')) {
        points = 145;
        reason = 'verified-token-link';
      } else if (attrs) {
        points = 125;
        reason = 'token-attribute';
      } else if (/\/(token|coin|trade|swap|pair)\//i.test(href)) {
        points = 95;
        reason = 'token-route';
      } else if (/contract|\bca\b|mint/i.test(text)) {
        points = 88;
        reason = 'token-label';
      }

      addCandidate(scored, combined, points, reason, text || href);
    }

    const visibleText = String(document.body?.innerText || '').slice(0, 180_000);
    const lines = visibleText.split('\n').filter(Boolean);
    for (const line of lines.slice(0, 2500)) {
      if (/\b(CA|Contract|Mint|Token address|Address)\b/i.test(line)) {
        addCandidate(scored, line, 96, 'visible-label', line);
      }
    }

    return [...scored.values()]
      .filter((item) => item.score >= 70)
      .sort((a, b) => b.score - a.score);
  }

  function collectPageContext() {
    const socialLinks = [];
    const projectLinks = [];

    for (const anchor of [...document.querySelectorAll('a[href]')].slice(0, 3500)) {
      const href = anchor.href || '';
      try {
        const url = new URL(href);
        const host = url.hostname.toLowerCase();
        if (SOCIAL_HOSTS.some((allowed) => host === allowed || host.endsWith(`.${allowed}`))) {
          socialLinks.push(href);
        } else if (href.startsWith('http') && !host.includes(location.hostname)) {
          projectLinks.push(href);
        }
      } catch {
        // Ignore malformed links.
      }
      if (socialLinks.length >= 30 && projectLinks.length >= 20) break;
    }

    return {
      source: sourceName(),
      url: location.href,
      title: document.title,
      text: String(document.body?.innerText || '').slice(0, 35_000),
      socialLinks: [...new Set(socialLinks)].slice(0, 20),
      projectLinks: [...new Set(projectLinks)].slice(0, 12),
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
      .neo{width:366px;max-height:calc(100vh - 96px);overflow:hidden;border:1px solid rgba(255,255,255,.12);border-radius:18px;background:rgba(7,9,12,.97);color:#e8edf2;font-family:Inter,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;box-shadow:0 20px 60px rgba(0,0,0,.48);backdrop-filter:blur(18px)}
      .head{display:flex;align-items:center;gap:10px;padding:12px 13px;border-bottom:1px solid rgba(255,255,255,.08)}
      .mark{width:32px;height:32px;display:grid;place-items:center;border-radius:10px;background:#34d399;color:#06100c;font-weight:1000}
      .brand{font-size:12px;font-weight:950;color:#fff}.sub{font-size:9px;color:#657081;margin-top:2px}.source{margin-left:auto;font-size:8px;font-weight:900;letter-spacing:.1em;color:#6ee7b7;border:1px solid rgba(52,211,153,.22);padding:4px 6px;border-radius:7px}
      .collapse{border:0;background:transparent;color:#7d8796;font-size:16px;padding:2px 4px}.body{max-height:calc(100vh - 150px);overflow:auto;padding:12px}.body::-webkit-scrollbar{width:5px}.body::-webkit-scrollbar-thumb{background:#29303a;border-radius:4px}
      .status{border:1px solid rgba(255,255,255,.08);background:rgba(255,255,255,.025);border-radius:14px;padding:12px;font-size:10px;line-height:1.55;color:#8f9bab}.status b{color:#fff}.live{display:inline-flex;align-items:center;gap:6px;margin-bottom:8px;color:#6ee7b7;font-size:8px;font-weight:950;letter-spacing:.12em}.dot{width:6px;height:6px;border-radius:50%;background:#34d399;box-shadow:0 0 12px rgba(52,211,153,.8)}
      .spin{display:inline-block;width:10px;height:10px;margin-right:7px;border:2px solid rgba(52,211,153,.25);border-top-color:#34d399;border-radius:999px;animation:neoSpin .8s linear infinite}@keyframes neoSpin{to{transform:rotate(360deg)}}
      .decision{border:1px solid rgba(255,255,255,.09);border-radius:15px;padding:12px}.decision.skip{border-color:rgba(248,113,113,.28);background:rgba(248,113,113,.06)}.decision.wait{border-color:rgba(251,146,60,.28);background:rgba(251,146,60,.06)}.decision.watch{border-color:rgba(251,191,36,.24);background:rgba(251,191,36,.05)}.decision.setup{border-color:rgba(52,211,153,.25);background:rgba(52,211,153,.06)}
      .eyebrow{font-size:8px;font-weight:950;letter-spacing:.15em;color:#697486}.posture{font-size:27px;line-height:1;font-weight:1000;letter-spacing:-.04em;color:white;margin-top:5px}.token{font-size:10px;color:#909aaa;margin-top:5px}.row{display:flex;gap:7px;margin-top:8px}.metric{flex:1;border:1px solid rgba(255,255,255,.08);border-radius:12px;background:rgba(255,255,255,.02);padding:9px}.metric span{display:block;font-size:7px;font-weight:900;letter-spacing:.12em;color:#657081}.metric strong{display:block;margin-top:5px;font-size:13px;color:white}
      .forecast{margin-top:8px;border:1px solid rgba(96,165,250,.18);background:rgba(96,165,250,.05);border-radius:14px;padding:11px}.forecast-top{display:flex;justify-content:space-between;gap:8px}.forecast strong{font-size:12px;color:#bfdbfe}.forecast-score{font-size:10px;font-weight:900;color:#93c5fd}.forecast p{font-size:9px;line-height:1.45;color:#7f8da0;margin:6px 0 0}
      .label{margin:12px 2px 7px;font-size:8px;font-weight:950;letter-spacing:.15em;color:#667183}.signals{display:grid;gap:6px}.signal{border:1px solid rgba(255,255,255,.07);border-radius:11px;background:rgba(255,255,255,.02);padding:9px}.signal-top{display:flex;gap:7px;justify-content:space-between}.signal b{font-size:10px;color:white}.pts{font-size:9px;font-weight:950}.signal p{margin:4px 0 0;font-size:8.5px;line-height:1.45;color:#7b8797}.signal.critical{border-color:rgba(248,113,113,.18)}.signal.critical .pts{color:#fca5a5}.signal.warning{border-color:rgba(251,191,36,.15)}.signal.warning .pts{color:#fde68a}.signal.positive{border-color:rgba(52,211,153,.15)}.signal.positive .pts{color:#6ee7b7}
      .socials{display:flex;flex-wrap:wrap;gap:6px}.social{display:inline-flex;align-items:center;min-height:28px;border:1px solid rgba(255,255,255,.09);border-radius:9px;padding:0 8px;background:rgba(255,255,255,.03);color:#dce3ea;text-decoration:none;font-size:8.5px;font-weight:800}.social:hover{border-color:rgba(52,211,153,.25);color:#6ee7b7}
      .factors{display:grid;grid-template-columns:1fr 1fr;gap:7px}.factor{border:1px solid rgba(255,255,255,.07);border-radius:11px;padding:8px;font-size:8.5px;line-height:1.5;color:#8390a1}.factor b{display:block;font-size:8px;letter-spacing:.1em;margin-bottom:3px}.bull b{color:#6ee7b7}.bear b{color:#fca5a5}
      .actions{display:flex;gap:7px;margin-top:10px}.btn{height:34px;flex:1;border:1px solid rgba(255,255,255,.1);border-radius:10px;background:rgba(255,255,255,.035);color:#fff;font-size:9px;font-weight:900}.btn.primary{border:0;background:#34d399;color:#06100c}.warn{margin-top:8px;border:1px solid rgba(251,191,36,.16);background:rgba(251,191,36,.05);border-radius:11px;padding:8px;font-size:8.5px;line-height:1.45;color:#d8bd70}.foot{margin-top:9px;font-size:8px;line-height:1.45;color:#596474;text-align:center}
      .collapsed .body{display:none}.collapsed{width:210px}.collapsed .sub,.collapsed .source{display:none}
    </style>
    <section class="neo">
      <div class="head">
        <div class="mark">N</div>
        <div><div class="brand">NEO Meme Coins</div><div class="sub">Automatic terminal intelligence</div></div>
        <div class="source">${esc(sourceName())}</div>
        <button class="collapse" title="Свий">−</button>
      </div>
      <div class="body"></div>
    </section>
  `;

  const panel = shadow.querySelector('.neo');
  const body = shadow.querySelector('.body');
  const sourceEl = shadow.querySelector('.source');
  const collapseBtn = shadow.querySelector('.collapse');

  collapseBtn.addEventListener('click', () => {
    collapsed = !collapsed;
    panel.classList.toggle('collapsed', collapsed);
    collapseBtn.textContent = collapsed ? '+' : '−';
  });

  function renderIdle(detail = '') {
    body.innerHTML = `
      <div class="status">
        <div class="live"><span class="dot"></span>AUTO WATCH ACTIVE</div>
        <b>Не поставяй contract address.</b><br>
        NEO наблюдава ${esc(sourceName())} и сам ще стартира анализ, когато отвориш coin.
        ${detail ? `<div style="margin-top:7px;color:#647184">${esc(detail)}</div>` : ''}
      </div>
      <div class="foot">При SPA навигация NEO следи URL + DOM промени и се прехвърля към новия token автоматично.</div>
    `;
  }

  function renderLoading(token) {
    body.innerHTML = `
      <div class="status">
        <div class="live"><span class="dot"></span>AUTO WATCH ACTIVE</div>
        <span class="spin"></span>Засечен coin <b>${esc(short(token))}</b>.<br>
        Анализирам market, holders, wallet funding, social links и momentum…
      </div>
    `;
  }

  function renderError(message) {
    body.innerHTML = `
      <div class="status" style="border-color:rgba(248,113,113,.2);color:#fca5a5">${esc(message)}</div>
      <div class="actions"><button class="btn primary" id="neoRetry">ПРОВЕРИ ПАК</button></div>
    `;
    shadow.getElementById('neoRetry')?.addEventListener('click', () => scheduleScan(true));
  }

  function render(result) {
    lastResult = result;
    const socials = result.social?.links || [];
    const bull = result.outlook?.bull || [];
    const bear = result.outlook?.bear || [];
    const fresh = result.holders?.sampledFreshWalletPct;
    const linked = result.holders?.sharedFunderClusters?.[0];
    const confidence = result.confidence ?? 0;
    const signals = Array.isArray(result.signals) ? result.signals.slice(0, 8) : [];

    body.innerHTML = `
      <div class="decision ${esc(String(result.posture || '').toLowerCase())}">
        <div class="eyebrow">AUTO ANALYSIS · CONFIDENCE ${esc(confidence)}%</div>
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
        <div class="metric"><span>LINKED</span><strong>${linked ? esc(linked.walletCount) : '0'}</strong></div>
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
      <div class="socials">${socials.length ? socials.slice(0, 10).map((item) => `<a class="social" href="${esc(item.url)}" target="_blank" rel="noreferrer">${esc(item.platform || 'LINK')}</a>`).join('') : '<span style="font-size:9px;color:#657081">Няма намерени публични project links.</span>'}</div>
      <div class="label">КЛЮЧОВИ СИГНАЛИ</div>
      <div class="signals">${signals.map((signal) => {
        const severity = signal.severity === 'warning' ? 'warning' : signal.severity || '';
        return `<div class="signal ${esc(severity)}"><div class="signal-top"><b>${esc(signal.label || signal.title)}</b><span class="pts">${Number(signal.points ?? signal.riskPoints ?? 0) > 0 ? '+' : ''}${esc(signal.points ?? signal.riskPoints ?? 0)}</span></div><p>${esc(signal.detail || '')}</p></div>`;
      }).join('')}</div>
      ${result.holderError ? `<div class="warn">Holder/RPC evidence е непълно: ${esc(result.holderError)}</div>` : ''}
      <div class="actions"><button class="btn primary" id="neoRescan">ОБНОВИ АНАЛИЗА</button></div>
      <div class="foot">NEO не изпълнява сделки. Outlook е вероятностен сигнал от текущите данни, не гаранция за бъдеща цена.</div>
    `;

    shadow.getElementById('neoRescan')?.addEventListener('click', () => scheduleScan(true));
  }

  async function runScan(token, force = false) {
    if (!token || scanning) return;
    if (!force && token === lastToken && lastResult) return;

    scanning = true;
    lastToken = token;
    sourceEl.textContent = sourceName();
    renderLoading(token);

    try {
      const response = await chrome.runtime.sendMessage({
        type: 'NEO_ANALYZE',
        input: token,
        pageContext: collectPageContext(),
      });
      if (!response?.ok) throw new Error(response?.error || 'Неуспешен анализ.');
      render(response.result);
    } catch (error) {
      lastResult = null;
      renderError(error instanceof Error ? error.message : String(error));
    } finally {
      scanning = false;
    }
  }

  function findFocusedToken() {
    const candidates = collectCandidates();
    if (!candidates.length) return null;

    const first = candidates[0];
    // A token in the URL or a token-specific route/link is strong enough to scan automatically.
    if (first.score >= 90) return first;
    return null;
  }

  function scheduleScan(force = false, delay = 350) {
    clearTimeout(scanTimer);
    scanTimer = setTimeout(() => {
      const focused = findFocusedToken();
      if (!focused) {
        if (!scanning && (!lastResult || force)) renderIdle('В момента няма ясно разпознат Solana coin на екрана.');
        return;
      }
      runScan(focused.token, force);
    }, delay);
  }

  function onNavigation() {
    if (location.href !== lastSeenUrl) {
      lastSeenUrl = location.href;
      lastToken = '';
      lastResult = null;
      renderIdle('Засечена е навигация. Търся новия coin…');
    }
    scheduleScan(false, 250);
  }

  const originalPushState = history.pushState.bind(history);
  history.pushState = (...args) => {
    const result = originalPushState(...args);
    setTimeout(onNavigation, 0);
    return result;
  };

  const originalReplaceState = history.replaceState.bind(history);
  history.replaceState = (...args) => {
    const result = originalReplaceState(...args);
    setTimeout(onNavigation, 0);
    return result;
  };

  window.addEventListener('popstate', onNavigation);
  window.addEventListener('hashchange', onNavigation);
  document.addEventListener('click', () => scheduleScan(false, 700), true);

  const observer = new MutationObserver(() => {
    if (location.href !== lastSeenUrl) onNavigation();
    else scheduleScan(false, 650);
  });
  observer.observe(document.documentElement, { subtree: true, childList: true, attributes: true, attributeFilter: ['href', 'data-token-address', 'data-mint', 'data-address', 'data-ca', 'data-contract'] });

  setInterval(() => scheduleScan(false, 0), 5000);

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type === 'NEO_RESCAN') {
      scheduleScan(true, 0);
      sendResponse({ ok: true, source: sourceName(), token: lastToken, scanning });
      return false;
    }
    if (message?.type === 'NEO_STATUS') {
      sendResponse({
        ok: true,
        source: sourceName(),
        token: lastToken,
        scanning,
        result: lastResult ? {
          posture: lastResult.posture,
          risk: lastResult.risk,
          confidence: lastResult.confidence,
          symbol: lastResult.market?.symbol,
          name: lastResult.market?.name,
        } : null,
      });
      return false;
    }
    return false;
  });

  renderIdle();
  scheduleScan(false, 450);
})();
