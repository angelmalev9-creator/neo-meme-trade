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

function short(value, size = 5) {
  const text = String(value || '');
  return text.length > size * 2 + 2 ? `${text.slice(0, size)}…${text.slice(-size)}` : text;
}

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
  currentCoin.textContent = response.token ? short(response.token) : 'Чакам coin…';

  if (response.scanning) {
    statusText.textContent = 'NEO засече coin и в момента го анализира автоматично.';
  } else if (response.result) {
    statusText.textContent = 'AUTO WATCH е активен. При смяна на coin ще стартира нов анализ.';
  } else {
    statusText.textContent = 'AUTO WATCH е активен. Отвори coin в терминала — не е нужно да копираш CA.';
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
    statusText.textContent = 'Отвори Fomo, Axiom или Photon. Там NEO работи автоматично.';
    resultBox.classList.add('hidden');
    return;
  }

  terminalName.textContent = terminal;

  try {
    const response = await chrome.tabs.sendMessage(activeTabId, { type: 'NEO_STATUS' });
    renderStatus(response);
  } catch {
    statusText.textContent = 'Терминалът е отворен, но тази страница още няма зареден NEO content script.';
    showError('Refresh-ни Fomo/Axiom/Photon веднъж след обновяването на extension-а.');
  }
}

rescanBtn.addEventListener('click', async () => {
  clearError();
  rescanBtn.disabled = true;
  rescanBtn.textContent = 'ПРОВЕРЯВАМ…';
  try {
    if (!activeTabId) await readStatus();
    if (!activeTabId) return;
    await chrome.tabs.sendMessage(activeTabId, { type: 'NEO_RESCAN' });
    statusText.textContent = 'Преглеждам текущия екран за coin…';
    setTimeout(readStatus, 900);
    setTimeout(readStatus, 2600);
  } catch (error) {
    showError(error instanceof Error ? error.message : String(error));
  } finally {
    setTimeout(() => {
      rescanBtn.disabled = false;
      rescanBtn.textContent = 'ПРОВЕРИ ТЕКУЩИЯ ЕКРАН';
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
