const UPSTREAM = 'https://neo-meme-api.169-58-211-177.sslip.io';

const ALLOWED_ORIGINS = new Set([
  'https://neo-meme-trade.vercel.app',
  'https://angelmalev9-creator.github.io',
]);

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

  try {
    const headers = { Accept: 'application/json' };
    if (req.headers.authorization) headers.Authorization = req.headers.authorization;
    if (req.method === 'POST') headers['Content-Type'] = 'application/json';

    const upstream = await fetch(`${UPSTREAM}${path}`, {
      method: req.method,
      headers,
      body: req.method === 'POST' ? JSON.stringify(req.body ?? {}) : undefined,
      cache: 'no-store',
    });

    const body = await upstream.text();
    res.status(upstream.status);
    res.setHeader('Content-Type', upstream.headers.get('content-type') || 'application/json; charset=utf-8');
    return res.send(body);
  } catch (error) {
    return res.status(502).json({
      message: 'Gateway proxy unavailable',
      detail: error instanceof Error ? error.message : String(error),
    });
  }
}
