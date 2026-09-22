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

function showError(message) {
  errorBox.textContent = message;
  errorBox.classList.remove('hidden');
}

function clearError() {
  errorBox.textContent = '';
  errorBox.classList.add('hidden');
}

function terminalFromUrl(rawUrl) {
  try {
    const host = new URL(rawUrl).hostname.toLowerCase();
    if (host === 'fomo.family' || host.endsWith('.fomo.family')) return 'FOMO';
    if (host.includes('axiom.trade')) return 'AXIOM';
    if (host.includes('tinyastro.io')) return 'PHOTON';
  } catch {
    // Ignore invalid URL.
  }
  return null;
}

function renderStatus(response) {
  if (!response?.ok) return;

  terminalName.textContent = response.source || '—';
  const top = Array.isArray(response.radarTop) ? response.radarTop[0] : null;
  currentCoin.textContent = top
    ? `${response.observedCount || 0} видими · $${top.symbol || '?'} ${top.status || ''}`
    : `${response.observedCount || 0} candidates`;

  if (response.deepScanning) {
    statusText.textContent = 'Sentinel продължава да гледа целия екран, докато върви deep holder/funding анализ.';
  } else if (response.radarScanning) {
    statusText.textContent = 'NEO в момента преоценява видимите token-и и market flow.';
  } else if (response.observedCount > 0) {
    statusText.textContent = 'Sentinel е активен: следи feed/cards/swaps и сам избира кои token-и заслужават deep check.';
  } else {
    statusText.textContent = 'Sentinel е активен и чака terminal-ът да покаже разпознаваеми Solana token addresses.';
  }

  if (response.result) {
    resultBox.classList.remove('hidden');
    postureEl.textContent = response.result.posture || '—';
    tokenNameEl.textContent = `${response.result.name || 'Token'}${response.result.symbol ? ` · $${response.result.symbol}` : ''}`;
    riskEl.textContent = `${response.result.risk ?? '-'}/100`;
    confidenceEl.textContent = `${response.result.confidence ?? '-'}%`;
    postureEl.className = `posture ${String(response.result.posture || '').toLowerCase()}`;
  } else {
    resultBox.classList.add('hidden');
  }
}

async function readStatus() {
  clearError();
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  activeTabId = tab?.id ?? null;
  const terminal = terminalFromUrl(tab?.url || '');

  if (!terminal || !activeTabId) {
    terminalName.textContent = 'НЕПОДДЪРЖАН TAB';
    currentCoin.textContent = '—';
    statusText.textContent = 'Отвори Fomo, Axiom или Photon. Там Sentinel наблюдава страницата автоматично.';
    resultBox.classList.add('hidden');
    return;
  }

  terminalName.textContent = terminal;

  try {
    const response = await chrome.tabs.sendMessage(activeTabId, { type: 'NEO_STATUS' });
    renderStatus(response);
  } catch {
    statusText.textContent = 'Терминалът е отворен, но content script-ът още не е зареден.';
    showError('Refresh-ни Fomo/Axiom/Photon веднъж след обновяването на extension-а.');
  }
}

rescanBtn.addEventListener('click', async () => {
  clearError();
  rescanBtn.disabled = true;
  rescanBtn.textContent = 'ОБНОВЯВАМ…';
  try {
    if (!activeTabId) await readStatus();
    if (!activeTabId) return;
    await chrome.tabs.sendMessage(activeTabId, { type: 'NEO_RESCAN' });
    statusText.textContent = 'Sentinel прави нов page-wide scan…';
    setTimeout(readStatus, 900);
    setTimeout(readStatus, 2600);
  } catch (error) {
    showError(error instanceof Error ? error.message : String(error));
  } finally {
    setTimeout(() => {
      rescanBtn.disabled = false;
      rescanBtn.textContent = 'ОБНОВИ SENTINEL';
    }, 900);
  }
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
  await readStatus();
  setTimeout(readStatus, 1200);
})();
