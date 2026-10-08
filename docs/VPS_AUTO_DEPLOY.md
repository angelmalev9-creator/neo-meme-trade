# Automatic deploy to the VPS

The VPS updates itself from `origin/main`. A systemd timer runs `neo-auto-deploy` every two minutes; when `main` has a new commit, that commit is tested, deployed and health-checked, and rolled back if anything fails. Nothing is pushed into the server: no SSH key or secret lives in GitHub, and the deploy does not depend on GitHub Actions.

PAPER only. The deploy never resets an account and never edits a ledger.

## What one run does

1. **Fetch** `origin/main`. If the checkout is already there, stop.
2. **Refuse unsafe states.** Tracked local changes in `/root/neo-meme-trade`, or a checkout that is not simply behind `origin/main`, block the deploy. Nothing is overwritten.
3. **Test the exact commit** in a temporary worktree, with the live tree untouched: `py_compile`, the strategy lock, and `scripts/run_python_checks.py`. A commit that fails is not deployed and is not retried; the next newer commit is.
4. **Stop the target engine and archive its ledger** (only when engine code changed) to `<account>/archives/pre-deploy-<time>-<commit>/` with checksums. The newest 10 such archives are kept; other archives are never touched.
5. **Fast-forward** the checkout.
6. **Restart only what changed**, engine first, then gateway.
7. **Verify**: `/health` answers; the engine reports `paper_only: true`; the PAPER session id and starting balance are unchanged; the history count did not fall.
8. **Roll back** to the previous commit and restart again if step 5, 6 or 7 fails.

The result of the last run is in `/var/lib/neo-market/deploy/status.json`.

## Which change restarts which service

| Changed files | Restarted |
| --- | --- |
| `backend/*.py` used by the engine (anything not listed below) | `neo-user-angel-paper.service` |
| `backend/user_gateway.py`, `backend/engine_runtime.py` | `neo-user-gateway.service` (after the engine is healthy) |
| `backend/live_tape.py`, `strategy_lab.py`, `lab_*.py`, `astra*.py`, `fomo_monitor.py`, `main_replay.py` | nothing — those services run from `/root/neomemecoins` |
| frontend, docs, tests, scripts | nothing — the files are only pulled |

A new backend module that is not on the exclusion list restarts the engine, so engine code can never be skipped by omission.

**Other accounts.** Engines spawned by the gateway keep running their old code until the gateway restarts; a gateway restart restarts all of them on the current code and the default strategy profile. To update them on every engine change, set `NEO_DEPLOY_RESTART_GATEWAY_ON_ENGINE_CHANGE=1` in `/etc/neo/auto-deploy.env`. To keep them on the scout strategy, set `NEO_STRATEGY_PROFILE=EARLY_SCOUT_V10` in `/etc/neo/neo-user-gateway.env` first (see `ORDER_FLOW_ADAPTIVE_OCT4.md`).

**Not managed:** the central engine, live tape and labs in `/root/neomemecoins`; systemd unit files; `requirements.txt` (a new Python dependency must be installed by hand — until then the checks fail and nothing deploys); Supabase and Vercel settings.

## Install (once, as root on the VPS)

```bash
cd /root/neo-meme-trade
git status --short            # must show no modified tracked files
git fetch origin main
git show origin/main:deploy/install-vps-auto-deploy.sh > /tmp/neo-install.sh
bash /tmp/neo-install.sh
```

The installer copies the script to `/usr/local/sbin/neo-auto-deploy`, installs and enables `neo-auto-deploy.timer`, and starts the first run. Because the live checkout is still on the old commit at that point, the first run performs a normal tested deploy of everything pending — including the engine restart onto the current strategy.

If the installer reports that the ledger cannot be located, put the path in `/etc/neo/auto-deploy.env`:

```bash
NEO_DEPLOY_ENGINE_STATE_PATH=/var/lib/neo-market/users/<account>/state.json
```

## Operate

```bash
cat /var/lib/neo-market/deploy/status.json          # last result
journalctl -u neo-auto-deploy.service -n 50         # what happened
systemctl list-timers neo-auto-deploy.timer         # next run
systemctl start neo-auto-deploy.service             # check now

touch /etc/neo/auto-deploy.disabled                 # pause
rm /etc/neo/auto-deploy.disabled                    # resume
```

`status.json` results: `deployed`, `verification_failed`, `rolled_back`, `rollback_unhealthy` (needs attention: the old commit is restored but a service is down), `blocked_dirty_checkout`, `blocked_diverged`, `blocked_no_ledger_path`, `blocked_archive_failed`.

To return to an older version, revert the commit on `main`; the server follows. Pause the timer before working on the server checkout by hand.

## What to keep in mind

- **`main` is now production for the VPS.** Whatever is merged there runs as root on the server within about two minutes, and its test suite runs as root before that. Protect the branch: require pull requests and keep write access narrow.
- **A deploy restarts the engine for a few seconds.** Open PAPER positions are not managed during that gap. Exits resume on restart with the policy each position was opened under.
- **The checks are a gate, not a guarantee.** They catch broken code and a broken strategy lock; they cannot tell whether a strategy change is a good idea.
