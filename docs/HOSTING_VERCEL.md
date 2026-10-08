# Frontend hosting on Vercel

The dashboard is hosted on Vercel at **https://neo-meme-trade.vercel.app**. Vercel builds the repository itself, so the frontend no longer depends on GitHub Actions. Only the static frontend moves: the PAPER backend stays on the VPS behind `https://neo-meme-api.169-58-211-177.sslip.io`.

## How it deploys

`vercel.json` pins the build: `npm ci`, then `npm run lint && npm run build`, output `dist/`. The build still verifies the strategy lock and the extension sources, and packages the extension zip into `dist/`. A push to `main` becomes the production deployment; other branches get preview deployments.

The Python regression suite does not run on Vercel. Run `python3 scripts/run_python_checks.py` before merging backend changes.

## One-time steps outside this repository

1. **Vercel** — the repository is imported in two Vercel teams. Keep the project that owns `neo-meme-trade.vercel.app`, confirm its production branch is `main`, and disconnect the other one so each push builds once.
2. **VPS gateway** — the browser may call `/user/*` only from allowed origins. The new origin is allowed in `backend/user_gateway.py`, so the gateway must run the new code:
   ```bash
   cd /root/neo-meme-trade && git merge --ff-only origin/main
   systemctl restart neo-user-gateway.service
   curl -s http://127.0.0.1:8789/user/health
   curl -si -H 'Origin: https://neo-meme-trade.vercel.app' https://neo-meme-api.169-58-211-177.sslip.io/user/health | grep -i access-control-allow-origin
   ```
   The last command must print the Vercel origin. Until then the Vercel page loads and logs in, but cannot read the account. The gateway reuses the running account engines on their existing ports; it does not restart them.
3. **Supabase** — Authentication → URL Configuration: set Site URL to `https://neo-meme-trade.vercel.app` and add it to the redirect URLs, so sign-up confirmation emails return to the new address.

## Allowed origins

`https://neo-meme-trade.vercel.app`, `https://angelmalev9-creator.github.io` (the old address keeps working), and local development on port 5173. `NEO_ALLOWED_ORIGINS` adds exact origins, comma separated, for a custom domain; wildcards and paths are ignored. Preview deployment URLs are not allowed, so previews can show the page but not account data.

## GitHub Pages

`.github/workflows/deploy-pages.yml` now runs only when started manually. The existing `gh-pages` site stays online with its last build and is not updated by pushes.
