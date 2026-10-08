#!/usr/bin/env bash
# One-time install of the pull-based deploy on the VPS. Run as root.
#
# It reads the deploy files from origin/main, so it works even while the live
# checkout is still on an older commit; the first timer run then performs a
# normal tested deploy of everything that is pending.
set -euo pipefail

REPO="${NEO_DEPLOY_REPO:-/root/neo-meme-trade}"
REF="${NEO_DEPLOY_INSTALL_REF:-origin/main}"
[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 1; }
for tool in git python3 flock sha256sum systemctl; do
  command -v "$tool" >/dev/null || { echo "missing required tool: $tool" >&2; exit 1; }
done
python3 -c 'import requests' || { echo "python3 cannot import requests; the checks cannot run" >&2; exit 1; }

git -C "$REPO" fetch --quiet origin main
tmp="$(mktemp -d)"; trap 'rm -rf -- "$tmp"' EXIT
for name in vps-auto-deploy.sh neo-auto-deploy.service neo-auto-deploy.timer; do
  git -C "$REPO" show "$REF:deploy/$name" > "$tmp/$name"
done
bash -n "$tmp/vps-auto-deploy.sh"

install -m 0755 "$tmp/vps-auto-deploy.sh" /usr/local/sbin/neo-auto-deploy
install -m 0644 "$tmp/neo-auto-deploy.service" /etc/systemd/system/neo-auto-deploy.service
install -m 0644 "$tmp/neo-auto-deploy.timer" /etc/systemd/system/neo-auto-deploy.timer
mkdir -p /etc/neo /var/lib/neo-market/deploy
[ -f /etc/neo/auto-deploy.env ] || cat > /etc/neo/auto-deploy.env <<'ENV'
# Optional settings for neo-auto-deploy. See docs/VPS_AUTO_DEPLOY.md.
# NEO_DEPLOY_ENGINE_STATE_PATH=/var/lib/neo-market/users/<account>/state.json
# NEO_DEPLOY_RESTART_GATEWAY_ON_ENGINE_CHANGE=0
ENV

# The deploy refuses to restart the engine unless it can archive the ledger first.
engine_unit="${NEO_DEPLOY_ENGINE_UNIT:-neo-user-angel-paper.service}"
pid="$(systemctl show "$engine_unit" -p MainPID --value 2>/dev/null || true)"
ledger=""
if [ -n "$pid" ] && [ "$pid" != 0 ] && [ -r "/proc/$pid/environ" ]; then
  ledger="$(tr '\0' '\n' < "/proc/$pid/environ" | sed -n 's/^NEO_MARKET_STATE_PATH=//p' | head -n 1)"
fi
if [ -n "$ledger" ] && [ -f "$ledger" ]; then
  echo "Engine ledger found: $ledger"
elif ! grep -q '^NEO_DEPLOY_ENGINE_STATE_PATH=' /etc/neo/auto-deploy.env; then
  echo "WARNING: could not locate the ledger of $engine_unit." >&2
  echo "         Set NEO_DEPLOY_ENGINE_STATE_PATH in /etc/neo/auto-deploy.env; until then engine changes will not deploy." >&2
fi

systemctl daemon-reload
systemctl enable --now neo-auto-deploy.timer

echo "Installed. Checkout is at $(git -C "$REPO" rev-parse --short HEAD), origin/main is at $(git -C "$REPO" rev-parse --short origin/main)."
echo "Running the first check now (this tests the pending commit before deploying it)..."
systemctl start neo-auto-deploy.service || true
journalctl -u neo-auto-deploy.service -n 30 --no-pager || true
echo
echo "Status:  cat /var/lib/neo-market/deploy/status.json"
echo "Logs:    journalctl -u neo-auto-deploy.service -f"
echo "Pause:   touch /etc/neo/auto-deploy.disabled     Resume: rm /etc/neo/auto-deploy.disabled"
