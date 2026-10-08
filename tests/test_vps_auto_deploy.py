"""Offline tests for deploy/vps-auto-deploy.sh.

Each test builds throwaway git repositories, a stub `systemctl` and a local
HTTP server standing in for the engine and gateway. Nothing touches systemd,
the network or a real ledger.
"""
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / 'deploy' / 'vps-auto-deploy.sh'
ENGINE, GATEWAY = 'neo-user-angel-paper.service', 'neo-user-gateway.service'
STATE = {'history': [{'id': 1}, {'id': 2}],
         'stats': {'demo_session_id': 'S1', 'demo_starting_balance_usd': 1000.0},
         'config': {'paper_only': True, 'signal_strategy': 'ORDER_FLOW_ADAPTIVE'}}

STUB_SYSTEMCTL = r'''#!/usr/bin/env bash
# Records calls. A "bad" commit makes the services unhealthy or corrupts state.
echo "$*" >> "$STUB_DIR/calls"
case "$1" in
  show) [ "$3" = MainPID ] && echo 0; exit 0 ;;
  start|restart)
    head="$(git -C "$NEO_DEPLOY_REPO" rev-parse HEAD)"
    if [ -f "$STUB_DIR/unhealthy_commit" ] && [ "$(cat "$STUB_DIR/unhealthy_commit")" = "$head" ]; then
      echo down > "$STUB_DIR/health"
    else
      echo ok > "$STUB_DIR/health"
    fi
    if [ -f "$STUB_DIR/corrupt_commit" ] && [ "$(cat "$STUB_DIR/corrupt_commit")" = "$head" ]; then
      cp "$STUB_DIR/state_corrupt.json" "$STUB_DIR/state.json"
    else
      cp "$STUB_DIR/state_good.json" "$STUB_DIR/state.json"
    fi ;;
esac
exit 0
'''


@unittest.skipUnless(os.name == 'posix' and all(shutil.which(t) for t in ('bash', 'git', 'flock', 'sha256sum')),
                     'needs a POSIX shell with git, flock and sha256sum')
class VpsAutoDeploy(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='neo-auto-deploy-')
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.origin, self.work, self.live = root / 'origin.git', root / 'work', root / 'live'
        self.stub, self.deploy_state = root / 'stub', root / 'deploy-state'
        self.account = root / 'account'
        for directory in (self.stub, self.account):
            directory.mkdir()
        (self.account / 'state.json').write_text('{"ledger": "precious"}')
        (self.account / 'audit.jsonl').write_text('{"event": "ENTRY"}\n')

        self.git(root, 'init', '-q', '--bare', '-b', 'main', str(self.origin))
        self.git(root, 'clone', '-q', str(self.origin), str(self.work))
        for name in ('backend/market_monitor.py', 'backend/user_gateway.py', 'backend/live_tape.py', 'docs/a.md'):
            (self.work / name).parent.mkdir(exist_ok=True)
            (self.work / name).write_text('v1\n')
        self.commit('initial')
        self.git(root, 'clone', '-q', str(self.origin), str(self.live))
        self.start = self.head(self.live)

        (self.stub / 'systemctl').write_text(STUB_SYSTEMCTL)
        (self.stub / 'systemctl').chmod(0o755)
        (self.stub / 'health').write_text('ok')
        (self.stub / 'state_good.json').write_text(json.dumps(STATE))
        (self.stub / 'state.json').write_text(json.dumps(STATE))

        stub = self.stub

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                healthy = (stub / 'health').read_text().strip() == 'ok'
                body = (stub / 'state.json').read_bytes() if self.path == '/state' else b'{"ok": true}'
                self.send_response(200 if healthy else 503)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        url = f'http://127.0.0.1:{self.server.server_port}'
        # Start from a clean slate: on the VPS these checks run inside a real
        # deploy, whose own NEO_DEPLOY_* settings must not leak into the fixtures.
        inherited = {k: v for k, v in os.environ.items() if not k.startswith('NEO_DEPLOY_')}
        self.env = {**inherited, 'STUB_DIR': str(self.stub), 'NEO_DEPLOY_REPO': str(self.live),
                    'NEO_DEPLOY_STATE_DIR': str(self.deploy_state),
                    'NEO_DEPLOY_DISABLE_FLAG': str(root / 'disabled'),
                    'NEO_DEPLOY_SYSTEMCTL': str(self.stub / 'systemctl'),
                    'NEO_DEPLOY_ENGINE_URL': url, 'NEO_DEPLOY_GATEWAY_URL': url,
                    'NEO_DEPLOY_ENGINE_STATE_PATH': str(self.account / 'state.json'),
                    'NEO_DEPLOY_VERIFY_CMD': f'echo run >> "{self.stub}/verify_runs"; test ! -f BROKEN',
                    'NEO_DEPLOY_HEALTH_TIMEOUT_SECONDS': '2', 'NEO_DEPLOY_INSTALLED_COPY': '',
                    'NEO_DEPLOY_RESTART_GATEWAY_ON_ENGINE_CHANGE': '0', 'TMPDIR': str(root)}

    # ---- helpers ---------------------------------------------------------
    def git(self, cwd, *args):
        return subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@example.test', *args],
                              cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()

    def head(self, repo):
        return self.git(repo, 'rev-parse', 'HEAD')

    def commit(self, message):
        self.git(self.work, 'add', '-A')
        self.git(self.work, 'commit', '-q', '-m', message)
        self.git(self.work, 'push', '-q', 'origin', 'HEAD:main')
        return self.head(self.work)

    def push_change(self, *names, content='v2\n'):
        for name in names:
            (self.work / name).parent.mkdir(exist_ok=True)
            (self.work / name).write_text(content)
        return self.commit('change ' + ' '.join(names))

    def run_deploy(self, **env):
        return subprocess.run(['bash', str(SCRIPT)], env={**self.env, **env}, capture_output=True, text=True, timeout=120)

    def calls(self):
        path = self.stub / 'calls'
        return [line for line in path.read_text().splitlines() if not line.startswith('show')] if path.exists() else []

    def status(self):
        return json.loads((self.deploy_state / 'status.json').read_text())

    def archives(self):
        return sorted((self.account / 'archives').glob('pre-deploy-*')) if (self.account / 'archives').exists() else []

    # ---- tests -----------------------------------------------------------
    def test_nothing_to_do_when_already_current(self):
        result = self.run_deploy()
        self.assertEqual((result.returncode, self.calls(), self.head(self.live)), (0, [], self.start))
        self.assertFalse((self.stub / 'verify_runs').exists())

    def test_docs_only_change_is_pulled_without_restarting_anything(self):
        target = self.push_change('docs/a.md', 'src/App.tsx')
        result = self.run_deploy()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.head(self.live), self.calls(), self.archives()), (target, [], []))
        self.assertEqual((self.status()['result'], self.status()['restarted_services']), ('deployed', []))

    def test_engine_change_archives_the_ledger_then_restarts_only_the_engine(self):
        target = self.push_change('backend/market_monitor.py')
        result = self.run_deploy()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.head(self.live), target)
        self.assertEqual(self.calls(), [f'stop {ENGINE}', f'start {ENGINE}'])
        archive, = self.archives()
        self.assertEqual((archive / 'state.json').read_text(), '{"ledger": "precious"}')
        self.assertTrue((archive / 'audit.jsonl').exists())
        self.assertIn('state.json', (archive / 'SHA256SUMS').read_text())
        self.assertEqual((self.account / 'state.json').read_text(), '{"ledger": "precious"}')
        self.assertEqual(self.status()['restarted_services'], [ENGINE])
        self.assertEqual(self.git(self.live, 'worktree', 'list').count('\n'), 0)

    def test_gateway_change_restarts_only_the_gateway(self):
        self.push_change('backend/user_gateway.py')
        self.assertEqual(self.run_deploy().returncode, 0)
        self.assertEqual((self.calls(), self.archives()), ([f'restart {GATEWAY}'], []))

    def test_engine_is_healthy_before_the_gateway_restarts(self):
        self.push_change('backend/market_monitor.py', 'backend/engine_runtime.py')
        self.assertEqual(self.run_deploy().returncode, 0)
        self.assertEqual(self.calls(), [f'stop {ENGINE}', f'start {ENGINE}', f'restart {GATEWAY}'])

    def test_other_process_modules_do_not_restart_the_engine_but_unknown_ones_do(self):
        self.push_change('backend/live_tape.py', 'backend/strategy_lab.py', 'backend/astra6_brain.py',
                         'backend/lab_paired_runner.py', 'backend/tests/test_x.py')
        self.assertEqual(self.run_deploy().returncode, 0)
        self.assertEqual(self.calls(), [])
        self.push_change('backend/brand_new_engine_module.py')
        self.assertEqual(self.run_deploy().returncode, 0)
        self.assertEqual(self.calls(), [f'stop {ENGINE}', f'start {ENGINE}'])

    def test_optional_gateway_restart_on_engine_change(self):
        self.push_change('backend/market_monitor.py')
        self.assertEqual(self.run_deploy(NEO_DEPLOY_RESTART_GATEWAY_ON_ENGINE_CHANGE='1').returncode, 0)
        self.assertEqual(self.calls(), [f'stop {ENGINE}', f'start {ENGINE}', f'restart {GATEWAY}'])

    def test_failing_checks_deploy_nothing_and_are_not_retried(self):
        bad = self.push_change('backend/market_monitor.py', 'BROKEN')
        result = self.run_deploy()
        self.assertEqual(result.returncode, 1)
        self.assertEqual((self.head(self.live), self.calls(), self.archives()), (self.start, [], []))
        self.assertEqual(self.status()['result'], 'verification_failed')
        self.assertEqual((self.deploy_state / 'failed_commit').read_text().strip(), bad)
        again = self.run_deploy()
        self.assertEqual(again.returncode, 0)
        self.assertIn('already failed', again.stdout)
        self.assertEqual((self.stub / 'verify_runs').read_text().count('run'), 1)
        # A newer, fixed commit is picked up normally.
        (self.work / 'BROKEN').unlink()
        fixed = self.commit('fix')
        self.assertEqual(self.run_deploy().returncode, 0)
        self.assertEqual(self.head(self.live), fixed)
        self.assertFalse((self.deploy_state / 'failed_commit').exists())

    def test_unhealthy_engine_rolls_back_to_the_previous_commit(self):
        bad = self.push_change('backend/market_monitor.py')
        (self.stub / 'unhealthy_commit').write_text(bad)
        result = self.run_deploy()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.head(self.live), self.start)
        self.assertEqual((self.live / 'backend/market_monitor.py').read_text(), 'v1\n')
        self.assertEqual(self.calls(), [f'stop {ENGINE}', f'start {ENGINE}', f'start {ENGINE}'])
        self.assertEqual(self.status()['result'], 'rolled_back')
        self.assertEqual((self.stub / 'health').read_text().strip(), 'ok')
        self.assertEqual((self.account / 'state.json').read_text(), '{"ledger": "precious"}')
        self.assertEqual(len(self.archives()), 1)
        self.assertEqual(self.run_deploy().returncode, 0)        # the bad commit is not retried
        self.assertEqual(self.head(self.live), self.start)

    def test_ledger_identity_change_rolls_back(self):
        cases = {'history shrank': {**STATE, 'history': [{'id': 1}]},
                 'session changed': {**STATE, 'stats': {**STATE['stats'], 'demo_session_id': 'RESET'}},
                 'starting balance changed': {**STATE, 'stats': {**STATE['stats'], 'demo_starting_balance_usd': 5.0}},
                 'paper_only': {**STATE, 'config': {**STATE['config'], 'paper_only': False}}}
        for reason, corrupt in cases.items():
            with self.subTest(reason=reason):
                previous = self.head(self.live)
                bad = self.push_change('backend/market_monitor.py', content=reason)
                (self.stub / 'corrupt_commit').write_text(bad)
                (self.stub / 'state_corrupt.json').write_text(json.dumps(corrupt))
                result = self.run_deploy()
                self.assertEqual(result.returncode, 1)
                self.assertIn(reason.split()[0], result.stdout)
                self.assertEqual(self.head(self.live), previous)
                self.assertEqual(self.status()['result'], 'rolled_back')

    def test_history_may_grow_across_a_deploy(self):
        target = self.push_change('backend/market_monitor.py')
        (self.stub / 'state_good.json').write_text(json.dumps({**STATE, 'history': [{'id': i} for i in range(5)]}))
        self.assertEqual(self.run_deploy().returncode, 0)
        self.assertEqual(self.head(self.live), target)

    def test_local_changes_block_the_deploy_and_are_kept(self):
        self.push_change('backend/market_monitor.py')
        (self.live / 'backend/user_gateway.py').write_text('hotfix on the server\n')
        result = self.run_deploy()
        self.assertEqual(result.returncode, 1)
        self.assertEqual((self.head(self.live), self.calls()), (self.start, []))
        self.assertEqual((self.live / 'backend/user_gateway.py').read_text(), 'hotfix on the server\n')
        self.assertEqual(self.status()['result'], 'blocked_dirty_checkout')

    def test_diverged_checkout_blocks_the_deploy(self):
        self.push_change('backend/market_monitor.py')
        (self.live / 'local.txt').write_text('x')
        self.git(self.live, 'add', '-A')
        self.git(self.live, 'commit', '-q', '-m', 'local only')
        local = self.head(self.live)
        result = self.run_deploy()
        self.assertEqual((result.returncode, self.head(self.live), self.calls()), (1, local, []))
        self.assertEqual(self.status()['result'], 'blocked_diverged')

    def test_engine_is_not_restarted_without_a_ledger_to_archive(self):
        self.push_change('backend/market_monitor.py')
        result = self.run_deploy(NEO_DEPLOY_ENGINE_STATE_PATH=str(self.account / 'missing.json'))
        self.assertEqual((result.returncode, self.head(self.live), self.calls()), (1, self.start, []))
        self.assertEqual(self.status()['result'], 'blocked_no_ledger_path')

    def test_disable_flag_pauses_everything(self):
        self.push_change('backend/market_monitor.py')
        Path(self.env['NEO_DEPLOY_DISABLE_FLAG']).write_text('')
        result = self.run_deploy()
        self.assertEqual((result.returncode, self.head(self.live), self.calls()), (0, self.start, []))

    def test_old_pre_deploy_archives_are_pruned_but_other_archives_are_kept(self):
        (self.account / 'archives' / 'manual-keep-me').mkdir(parents=True)
        for index in range(4):
            self.push_change('backend/market_monitor.py', content=f'v{index + 10}\n')
            self.assertEqual(self.run_deploy(NEO_DEPLOY_KEEP_ARCHIVES='2').returncode, 0)
            (self.stub / 'calls').unlink()
            # Archive names carry a one-second timestamp plus the commit, so they stay distinct.
        self.assertEqual(len(self.archives()), 2)
        self.assertTrue((self.account / 'archives' / 'manual-keep-me').is_dir())


if __name__ == '__main__':
    unittest.main(verbosity=2)
