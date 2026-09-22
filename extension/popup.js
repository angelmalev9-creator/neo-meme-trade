const statusText = document.getElementById('statusText');
const terminalName = document.getElementById('terminalName');
const currentCoin = document.getElementById('currentCoin');
const rescanBtn = document.getElementById('rescanBtn');
const errorBox = document.getElementById('error');
const resultBox = document.getElementById('result');
const postureEl = document.getElementById('posture');
const tokenNameEl = document.getElementById('tokenName');
const riskEl = document.getElementById('risk');
const confidenceEl = document.getElementById('confidence');
const rpcInput = document.getElementById('rpcInput');
const saveRpcBtn = document.getElementById('saveRpcBtn');

let activeTabId = null;
let repairingConnection = false;
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function showError(message) { errorBox.textContent = message; errorBox.classList.remove('hidden'); }
function clearError() { errorBox.textContent = ''; errorBox.classList.add('hidden'); }

function terminalFromUrl(rawUrl) {
  try {
    const host = new URL(rawUrl).hostname.toLowerCase();
    if (host === 'fomo.family' || host.endsWith('.fomo.family')) return 'FOMO';
    if (host.includes('axiom.trade')) return 'AXIOM';
    if (host.includes('tinyastro.io')) return 'PHOTON';
  } catch { /* ignore */ }
  return null;
}

async function sendToSentinel(message, allowRepair = true) {
  if (!activeTabId) throw new Error('Няма активен terminal tab.');
  try {
    return await chrome.tabs.sendMessage(activeTabId, message);
  } catch (error) {
    if (!allowRepair || repairingConnection) throw error;
    repairingConnection = true;
    statusText.textContent = 'NEO активира realtime Sentinel v0.6 върху текущия terminal…';
    clearError();
    try {
      await chrome.scripting.executeScript({ target: { tabId: activeTabId }, files: ['fomo-discovery.js', 'sentinel-v2.js', 'realtime-layer.js'] });
      await sleep(700);
      return await chrome.tabs.sendMessage(activeTabId, message);
    } finally { repairingConnection = false; }
  }
}

function renderStatus(response) {
  if (!response?.ok) return;
  terminalName.textContent = response.source || '—';
  const top = Array.isArray(response.radarTop) ? response.radarTop[0] : null;
  currentCoin.textContent = top
    ? `${response.observedCount || 0} видими · $${top.symbol || '?'} ${top.status || ''}`
    : `${response.observedCount || 0} candidates`;

  const queue = Number(response.queue || 0);
  const v15 = response.validation?.m15;
  const validationText = v15?.total ? ` · 15m validation ${v15.correct}/${v15.total}` : '';
  if (response.deepScanning) statusText.textContent = `Deep holder/funding анализ върви. 3s market engine продължава паралелно${queue ? ` · queue ${queue}` : ''}${validationText}.`;
  else if (response.radarScanning) statusText.textContent = `NEO преоценява market flow, liquidity и candidates${validationText}.`;
  else if (response.observedCount > 0) statusText.textContent = `Realtime Sentinel следи market flow през ~3s, holder pulse и автоматични deep checks${queue ? ` · queue ${queue}` : ''}${validationText}.`;
  else statusText.textContent = 'Sentinel е активен. Търси CA директно или resolve-ва Fomo token name/symbol + market cap.';

  if (response.result) {
    resultBox.classList.remove('hidden');
    postureEl.textContent = response.result.posture || '—';
    tokenNameEl.textContent = `${response.result.name || 'Token'}${response.result.symbol ? ` · $${response.result.symbol}` : ''}`;
    riskEl.textContent = `${response.result.risk ?? '-'}/100`;
    confidenceEl.textContent = `${response.result.confidence ?? '-'}%`;
    postureEl.className = `posture ${String(response.result.posture || '').toLowerCase()}`;
  } else resultBox.classList.add('hidden');
}

async function readStatus() {
  clearError();
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  activeTabId = tab?.id ?? null;
  const terminal = terminalFromUrl(tab?.url || '');
  if (!terminal || !activeTabId) {
    terminalName.textContent = 'НЕПОДДЪРЖАН TAB'; currentCoin.textContent = '—';
    statusText.textContent = 'Отвори Fomo, Axiom или Photon. Sentinel работи там автоматично.';
    resultBox.classList.add('hidden'); return;
  }
  terminalName.textContent = terminal;
  try { renderStatus(await sendToSentinel({ type: 'NEO_STATUS' }, true)); }
  catch (error) { statusText.textContent = 'Не успях да стартирам Sentinel върху този tab.'; showError(error instanceof Error ? error.message : String(error)); }
}

rescanBtn.addEventListener('click', async () => {
  clearError(); rescanBtn.disabled = true; rescanBtn.textContent = 'ОБНОВЯВАМ…';
  try {
    if (!activeTabId) await readStatus();
    if (!activeTabId) return;
    await sendToSentinel({ type: 'NEO_RESCAN' }, true);
    statusText.textContent = 'Sentinel прави нов full-terminal scan…';
    setTimeout(readStatus, 900); setTimeout(readStatus, 2800);
  } catch (error) { showError(error instanceof Error ? error.message : String(error)); }
  finally { setTimeout(() => { rescanBtn.disabled = false; rescanBtn.textContent = 'ОБНОВИ SENTINEL'; }, 900); }
});

saveRpcBtn.addEventListener('click', async () => {
  clearError();
  const value = rpcInput.value.trim();
  if (!value) {
    await chrome.storage.local.remove('rpcUrl'); rpcInput.value = ''; saveRpcBtn.textContent = 'RESET TO PUBLIC RPC';
    setTimeout(() => { saveRpcBtn.textContent = 'ЗАПАЗИ ЛОКАЛНО'; }, 1300); return;
  }
  try {
    const url = new URL(value);
    if (url.protocol !== 'https:') throw new Error('RPC URL трябва да е HTTPS.');
    const granted = await chrome.permissions.request({ origins: [`${url.origin}/*`] });
    if (!granted) throw new Error('Chrome permission за този RPC домейн не беше дадено.');
    await chrome.storage.local.set({ rpcUrl: value }); saveRpcBtn.textContent = 'ЗАПАЗЕНО ✓';
    setTimeout(() => { saveRpcBtn.textContent = 'ЗАПАЗИ ЛОКАЛНО'; }, 1300);
  } catch (error) { showError(error instanceof Error ? error.message : String(error)); }
});

(async function init() {
  try { const stored = await chrome.storage.local.get(['rpcUrl']); rpcInput.value = stored.rpcUrl || ''; }
  catch { rpcInput.value = ''; }
  await readStatus(); setTimeout(readStatus, 1300);
})();