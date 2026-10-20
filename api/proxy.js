const UPSTREAM = 'https://neo-meme-api.169-58-211-177.sslip.io';

const ALLOWED_ORIGINS = new Set([
  'https://neo-meme-trade.vercel.app',
  'https://angelmalev9-creator.github.io',
]);

const RETRYABLE_UPSTREAM_STATUSES = new Set([502, 503, 504]);
const GET_ATTEMPTS = 4;
const ATTEMPT_TIMEOUT_MS = 2800;
const RETRY_DELAYS_MS = [0, 150, 350, 700];

function setCors(req, res) {
  const origin = String(req.headers.origin || '');
  if (ALLOWED_ORIGINS.has(origin)) {
    res.setHeader('Access-Control-Allow-Origin', origin);
  } else {
    res.setHeader('Access-Control-Allow-Origin', 'https://neo-meme-trade.vercel.app');
  }
  res.setHeader('Vary', 'Origin');
  res.setHeader('Access-Control-Allow-Headers', 'Authorization, Content-Type');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  res.setHeader('Cache-Control', 'no-store');
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function fetchWithTimeout(url, options) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), ATTEMPT_TIMEOUT_MS);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

export default async function handler(req, res) {
  setCors(req, res);

  if (req.method === 'OPTIONS') {
    return res.status(204).end();
  }
  if (req.method !== 'GET' && req.method !== 'POST') {
    return res.status(405).json({ message: 'Method not allowed' });
  }

  const rawPath = Array.isArray(req.query.path) ? req.query.path[0] : req.query.path;
  const path = String(rawPath || '');
  if (!path.startsWith('/user/')) {
    return res.status(400).json({ message: 'Invalid proxy path' });
  }

  const headers = { Accept: 'application/json' };
  if (req.headers.authorization) headers.Authorization = req.headers.authorization;
  if (req.method === 'POST') headers['Content-Type'] = 'application/json';

  const options = {
    method: req.method,
    headers,
    body: req.method === 'POST' ? JSON.stringify(req.body ?? {}) : undefined,
    cache: 'no-store',
  };

  // GETs are read-only, so transient gateway/upstream failures can be retried
  // safely. POSTs are attempted once only to prevent duplicate side effects.
  const attempts = req.method === 'GET' ? GET_ATTEMPTS : 1;
  let upstream = null;
  let lastError = null;

  for (let attempt = 0; attempt < attempts; attempt += 1) {
    const delay = RETRY_DELAYS_MS[attempt] || 0;
    if (delay) await sleep(delay);

    try {
      const response = await fetchWithTimeout(`${UPSTREAM}${path}`, options);
      if (!RETRYABLE_UPSTREAM_STATUSES.has(response.status)) {
        upstream = response;
        break;
      }

      // Preserve the final response for diagnostics; discard intermediate
      // transient bodies so the connection can be released before retrying.
      if (attempt === attempts - 1) {
        upstream = response;
        break;
      }
      await response.text().catch(() => '');
    } catch (error) {
      lastError = error;
      if (attempt === attempts - 1) break;
    }
  }

  // Never expose HTTP 502 from the relay. A short backend restart is reported
  // as retryable 503 after automatic retries instead of killing the dashboard
  // request on the first gateway miss.
  if (!upstream || RETRYABLE_UPSTREAM_STATUSES.has(upstream.status)) {
    if (upstream) await upstream.text().catch(() => '');
    res.setHeader('Retry-After', '2');
    res.setHeader('Content-Type', 'application/json; charset=utf-8');
    return res.status(503).json({
      message: 'Backend temporarily recovering',
      retryable: true,
      attempts,
      detail: lastError instanceof Error ? lastError.message : undefined,
    });
  }

  const body = await upstream.text();
  res.status(upstream.status);
  res.setHeader('Content-Type', upstream.headers.get('content-type') || 'application/json; charset=utf-8');
  res.setHeader('X-NEO-Relay', 'healthy');
  return res.send(body);
}
