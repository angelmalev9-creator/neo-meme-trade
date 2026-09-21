const tokenInput = document.getElementById('tokenInput');
const scanBtn = document.getElementById('scanBtn');
const errorBox = document.getElementById('error');
const resultBox = document.getElementById('result');
const decisionBox = document.getElementById('decision');
const postureEl = document.getElementById('posture');
const tokenNameEl = document.getElementById('tokenName');
const riskEl = document.getElementById('risk');
const liqRatioEl = document.getElementById('liqRatio');
const top5El = document.getElementById('top5');
const signalsEl = document.getElementById('signals');
const holderWarningEl = document.getElementById('holderWarning');
const rpcInput = document.getElementById('rpcInput');
const saveRpcBtn = document.getElementById('saveRpcBtn');

function showError(message) {
  errorBox.textContent = message;
  errorBox.classList.remove('hidden');
}

function clearError() {
  errorBox.textContent = '';
  errorBox.classList.add('hidden');
}

function setLoading(loading) {
  scanBtn.disabled = loading;
  scanBtn.textContent = loading ? 'АНАЛИЗИРАМ…' : 'АНАЛИЗИРАЙ';
}

function renderResult(result) {
  resultBox.classList.remove('hidden');
  postureEl.textContent = result.posture;
  tokenNameEl.textContent = `${result.market.name} · $${result.market.symbol}`;
  riskEl.textContent = `${result.risk}/100`;
  liqRatioEl.textContent = `${result.liquidityRatio.toFixed(result.liquidityRatio >= 10 ? 1 : 2)}%`;
  top5El.textContent = result.holders ? `${result.holders.top5Pct.toFixed(1)}%` : 'N/A';

  decisionBox.className = `decision ${result.posture.toLowerCase()}`;
  signalsEl.innerHTML = '';

  for (const signal of result.signals) {
    const row = document.createElement('div');
    const className = signal.severity === 'warning' ? 'warning-signal' : signal.severity;
    row.className = `signal ${className}`;

    const top = document.createElement('div');
    top.className = 'signal-top';

    const title = document.createElement('strong');
    title.textContent = signal.label;
    const points = document.createElement('span');
    points.className = 'points';
    points.textContent = `${signal.points > 0 ? '+' : ''}${signal.points}`;
    top.append(title, points);

    const detail = document.createElement('p');
    detail.textContent = signal.detail;
    row.append(top, detail);
    signalsEl.appendChild(row);
  }

  if (result.holderError) {
    holderWarningEl.textContent = `Holder scan unavailable: ${result.holderError}`;
    holderWarningEl.classList.remove('hidden');
  } else {
    holderWarningEl.classList.add('hidden');
  }
}

async function scan() {
  const input = tokenInput.value.trim();
  if (!input) {
    showError('Постави Solana token address или DexScreener URL.');
    return;
  }

  clearError();
  setLoading(true);
  try {
    const response = await chrome.runtime.sendMessage({ type: 'NEO_ANALYZE', input });
    if (!response?.ok) throw new Error(response?.error || 'Unknown scan error');
    renderResult(response.result);
  } catch (error) {
    showError(error instanceof Error ? error.message : String(error));
  } finally {
    setLoading(false);
  }
}

scanBtn.addEventListener('click', scan);
tokenInput.addEventListener('keydown', (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') scan();
});

saveRpcBtn.addEventListener('click', async () => {
  clearError();
  const value = rpcInput.value.trim();
  if (!value) {
    await chrome.storage.local.remove('rpcUrl');
    rpcInput.value = '';
    saveRpcBtn.textContent = 'RESET TO PUBLIC RPC';
    setTimeout(() => { saveRpcBtn.textContent = 'ЗАПАЗИ ЛОКАЛНО'; }, 1300);
    return;
  }

  try {
    const url = new URL(value);
    if (url.protocol !== 'https:') throw new Error('RPC URL трябва да е HTTPS.');
    const originPermission = `${url.origin}/*`;
    const granted = await chrome.permissions.request({ origins: [originPermission] });
    if (!granted) throw new Error('Chrome permission за този RPC домейн не беше дадено.');
    await chrome.storage.local.set({ rpcUrl: value });
    saveRpcBtn.textContent = 'ЗАПАЗЕНО ✓';
    setTimeout(() => { saveRpcBtn.textContent = 'ЗАПАЗИ ЛОКАЛНО'; }, 1300);
  } catch (error) {
    showError(error instanceof Error ? error.message : String(error));
  }
});

(async function init() {
  try {
    const stored = await chrome.storage.local.get(['rpcUrl']);
    rpcInput.value = stored.rpcUrl || '';
  } catch {
    rpcInput.value = '';
  }

  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (tab?.url?.includes('dexscreener.com/solana/')) {
      tokenInput.value = tab.url;
    }
  } catch {
    // activeTab URL is optional; manual paste still works.
  }
})();
