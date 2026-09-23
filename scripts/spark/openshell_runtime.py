"""Operate the single prepared OpenShell agent; never launch an unsandboxed agent."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = Path('/srv/market-shock/openshell')
CLI = RUNTIME / '0.0.116/openshell'
PREPARED = RUNTIME / 'prepared'
NAME = 'market-agent'
AGENT_COMMAND = ['/usr/local/bin/python3.12', '-m', 'uvicorn',
                 'market_agent.app:app', '--host', '127.0.0.1', '--port', '2024']
sys.path.insert(0, str(ROOT))
from scripts.spark.build_inputs import build_input_digests
from scripts.spark.openshell_receipt import create_receipt, validate_receipt
from scripts.spark import retention

RETENTION_ROOT = retention.ROOT
RETENTION_LOCKED_ACTIONS = frozenset({'launch', 'start', 'recreate', 'stop'})


def image_identity():
    info = json.loads(run(['docker', 'image', 'inspect', 'market-shock-agent:latest']))[0]
    digest = build_input_digests(ROOT)['agent']
    if info['Config']['Labels'].get('com.nvidia.market-shock.build-input-sha256') != digest:
        raise RuntimeError('Agent image source identity is stale')
    if any(k.startswith('com.docker.compose.') for k in info['Config']['Labels']):
        raise RuntimeError('Agent image must not claim Compose workload ownership')
    return info['Id'], digest


def prepare_receipt():
    for filename in ('openshell', 'openshell-gateway'):
        if run([RUNTIME / '0.0.116' / filename, '--version']).strip() != f'{filename} 0.0.116':
            raise RuntimeError('OpenShell version mismatch')
    image_id, digest = image_identity()
    files = dict(binary=CLI, gateway=CLI.with_name('openshell-gateway'),
                 supervisor=CLI.with_name('openshell-sandbox'),
                 gateway_config=ROOT / 'scripts/spark/openshell/gateway.toml',
                 base_policy=ROOT / 'scripts/spark/openshell/policy.yaml',
                 policy=PREPARED / 'policy.yaml', env=PREPARED / 'env.json',
                 provider_list=PREPARED / 'providers.json')
    receipt = create_receipt(files, image_id=image_id, source_build_input=digest)
    temporary = PREPARED / 'runtime.preparing.json'
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        json.dump(receipt, stream, indent=2)
    temporary.replace(PREPARED / 'runtime.json')
    print('OpenShell preparation receipt published')


def verify():
    image_id, digest = image_identity()
    path = PREPARED / 'runtime.json'
    if path.is_symlink() or path.stat().st_mode & 0o077:
        raise RuntimeError('Unsafe preparation receipt')
    receipt = json.loads(path.read_text())
    validate_receipt(receipt, image_id=image_id, source_build_input=digest)
    return receipt


def environment():
    result = os.environ.copy()
    for key, folder in [('XDG_CONFIG_HOME', 'config'), ('XDG_DATA_HOME', 'data'),
                        ('XDG_STATE_HOME', 'state')]:
        result[key] = str(RUNTIME / folder)
    return result


def run(args, *, capture=True, timeout=120, discard=False, input_text=None):
    streams = {'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL} if discard else {'capture_output': capture}
    result = subprocess.run([str(v) for v in args], cwd=ROOT, env=environment(),
                            **streams, input=input_text, text=True, timeout=timeout)
    if result.returncode:
        # Arguments or gateway errors can contain private configuration.
        raise RuntimeError(f'Operator command failed ({Path(str(args[0])).name}); no private output was displayed')
    return result.stdout if capture and not discard else ''


def shell(*args, **kwargs):
    return run([CLI, '-g', 'market-shock', *args], **kwargs)


def launch(name=NAME, *, probe=False):
    verify()
    wait_dependencies()
    env = json.loads((PREPARED / 'env.json').read_text())
    providers = json.loads((PREPARED / 'providers.json').read_text())
    image = image_identity()[0] if not probe else run(['docker', 'image', 'inspect',
             'market-shock-agent:latest', '--format', '{{.Id}}']).strip()
    mounts = []
    for folder in ('scenario', 'events', 'state', 'traces'):
        source = f'/srv/market-shock/{folder}'
        if probe and folder in ('state', 'traces'):
            source = str(RUNTIME / 'probe' / folder)
            Path(source).mkdir(parents=True, exist_ok=True)
        mounts.append(dict(type='bind', source=source,
                           target=f'/srv/market-shock/{folder}',
                           read_only=folder in ('scenario', 'events')))
    args = ['sandbox', 'create', '--name', name, '--from', image,
            '--policy', str(PREPARED / 'policy.yaml'), '--cpu', '4', '--memory', '4Gi',
            '--driver-config-json', json.dumps({'docker': {'mounts': mounts}}),
            '--detach', '--no-auto-providers']
    for key, value in env.items():
        if key != 'HOME':
            args.extend(['--env', f'{key}={value}'])
    for provider in providers:
        args.extend(['--provider', provider])
    args.extend(['--', *AGENT_COMMAND])
    shell(*args)
    print(f'OpenShell sandbox created: {name}; API health still requires verification')


def validate_policy(prepared, observed):
    if observed.get('status') != 'effective' or observed.get('version') != observed.get('active_version'):
        raise RuntimeError('Sandbox policy is not effective')
    live = observed.get('policy')
    if not isinstance(live, dict):
        raise RuntimeError('Sandbox policy is missing')
    # The pinned release inserts these read-only runtime paths. No other grant
    # or endpoint may differ from the prepared base policy.
    live = json.loads(json.dumps(live))
    expected = json.loads(json.dumps(prepared))
    defaults = {'/dev/urandom', '/var/log'}
    for document in (live, expected):
        filesystem = document.get('filesystem_policy', {})
        for field in ('read_only', 'read_write'):
            paths = filesystem.get(field)
            if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
                raise RuntimeError('Sandbox filesystem policy is invalid')
            filesystem[field] = sorted(set(paths) | (defaults if field == 'read_only' else set()))
    if live != expected:
        raise RuntimeError('Sandbox policy differs from preparation')


def wait_dependencies(timeout=600):
    deadline = time.monotonic() + timeout
    while True:
        healthy = True
        for service in ('tools', 'model'):
            ids = run(['docker', 'ps', '-q', '--filter', 'label=com.docker.compose.project=market-shock',
                       '--filter', f'label=com.docker.compose.service={service}']).split()
            if len(ids) > 1:
                raise RuntimeError('Dependency container identity is ambiguous')
            if not ids:
                healthy = False
                continue
            state = json.loads(run(['docker', 'inspect', ids[0], '--format', '{{json .State}}']))
            healthy = healthy and state.get('Running') is True and state.get('Health', {}).get('Status') == 'healthy'
        if healthy:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError('Tools or model did not become healthy; agent was not launched')
        time.sleep(2)


def validate_launch(container, prepared_env, prepared_providers, provider_output):
    """Compare actual launch inputs without exposing values in errors."""
    error = 'Sandbox launch configuration differs from preparation; explicitly recreate the sandbox from prepared inputs'
    try:
        pairs = [entry.split('=', 1) for entry in container['Config']['Env']]
        env = dict(pairs)
        expected_env = {key: value for key, value in prepared_env.items() if key != 'HOME'}
        if len(pairs) != len(env) or any(key in expected_env for key in ('NVIDIA_INFERENCE_API_KEY', 'LANGSMITH_API_KEY')):
            raise ValueError
        # Provider-injected credential placeholders are not compared or printed.
        if json.loads(env['OPENSHELL_USER_ENVIRONMENT']) != expected_env or any(env.get(key) != value for key, value in expected_env.items()):
            raise ValueError
        if json.loads(env['OPENSHELL_MAIN_PROCESS_SPEC']) != {'version': 1, 'command': AGENT_COMMAND, 'tty': False}:
            raise ValueError
        mounts = [row for row in container['Mounts'] if row['Destination'].startswith('/srv/market-shock')]
        expected_mounts = {(f'/srv/market-shock/{name}', name in ('state', 'traces')) for name in ('scenario', 'events', 'state', 'traces')}
        if len(mounts) != 4 or {(row['Destination'], row['RW']) for row in mounts} != expected_mounts:
            raise ValueError
        if any(row['Type'] != 'bind' or row['Source'] != row['Destination'] for row in mounts):
            raise ValueError
        # v0.0.116 exposes attachments as a fixed four-column table, not JSON.
        output = re.sub(r'\x1b\[[0-9;]*m', '', provider_output).strip()
        if not output:
            raise ValueError
        rows = [] if output == f'No providers attached to sandbox {NAME}.' else [line.split() for line in output.splitlines()]
        if rows:
            if rows.pop(0) != ['NAME', 'TYPE', 'CREDENTIAL_KEYS', 'CONFIG_KEYS']:
                raise ValueError
            if any(len(row) != 4 or row[0] != row[1] or not all(value.isdigit() for value in row[2:]) for row in rows):
                raise ValueError
        names = [row[0] for row in rows]
        if not isinstance(prepared_providers, list) or len(names) != len(set(names)) or sorted(names) != sorted(prepared_providers):
            raise ValueError
    except (KeyError, TypeError, ValueError, AttributeError):
        raise RuntimeError(error) from None


def managed_container(sandbox_id, *, include_stopped=False):
    ids = run(['docker', 'ps', '-aq' if include_stopped else '-q', '--filter',
               f'label=openshell.ai/sandbox-id={sandbox_id}']).split()
    if len(ids) != 1:
        raise RuntimeError('Managed workload identity is ambiguous')
    return ids[0], json.loads(run(['docker', 'inspect', ids[0]]))[0]


def status():
    receipt = verify()
    observed = json.loads(shell('sandbox', 'get', NAME, '--output', 'json'))
    if observed['phase'] != 'Ready':
        raise RuntimeError('Agent sandbox is not ready')
    validate_policy(json.loads((PREPARED / 'policy.yaml').read_text()),
                    json.loads(shell('policy', 'get', NAME, '--base', '--output', 'json')))
    container_id, container = managed_container(observed['id'])
    if container['Image'] != receipt['image_id']:
        raise RuntimeError('Managed agent image differs from preparation')
    supervisor = '/opt/openshell/bin/openshell-sandbox'
    if container['Config']['Entrypoint'] != [supervisor]:
        raise RuntimeError('Agent supervisor missing')
    mounts = [row for row in container.get('Mounts', []) if row.get('Destination') == supervisor]
    if len(mounts) != 1 or mounts[0].get('Source') != str(CLI.with_name('openshell-sandbox')) or mounts[0].get('RW') is not False:
        raise RuntimeError('Agent supervisor does not match the prepared binary')
    validate_launch(container, json.loads((PREPARED / 'env.json').read_text()),
                    json.loads((PREPARED / 'providers.json').read_text()),
                    shell('sandbox', 'provider', 'list', NAME))
    health = json.loads(shell('sandbox', 'exec', '-n', NAME, '--',
        '/usr/local/bin/python3.12', '-c',
        'import urllib.request; print(urllib.request.urlopen("http://127.0.0.1:2024/api/status", timeout=10).read().decode())'))
    if not health.get('ready'):
        raise RuntimeError('Agent dependencies not ready')
    container_id = container.get('Id') or container_id
    config_sha256 = hashlib.sha256(json.dumps(
        container.get('Config'), sort_keys=True, separators=(',', ':'),
    ).encode()).hexdigest()
    print(json.dumps({'name': NAME, 'phase': 'Ready', 'version': '0.0.116',
                      'image_id': receipt['image_id'], 'sandbox_id': observed['id'],
                      'container_id': container_id, 'container_config_sha256': config_sha256,
                      'tools': health['dependencies']['tools'], 'model': health['dependencies']['model']}))


def start():
    receipt = verify()
    wait_dependencies()
    run(['systemctl', '--user', 'start', 'market-shock-openshell'])
    for attempt in range(20):
        try:
            rows = json.loads(shell('sandbox', 'list', '--output', 'json'))
            break
        except RuntimeError:
            if attempt == 19:
                raise
            time.sleep(0.5)
    matching = [row for row in rows if row['name'] == NAME]
    if len(matching) > 1:
        raise RuntimeError('Agent sandbox identity is ambiguous')
    if not matching:
        launch()
    elif matching[0]['phase'] not in ('Ready', 'Stopped'):
        raise RuntimeError('Existing agent needs explicit repair; no fallback')
    else:
        _, container = managed_container(matching[0].get('id'), include_stopped=True)
        if container['Image'] != receipt['image_id']:
            raise RuntimeError('Prepared agent image changed; run ./demo start --recreate-agent during maintenance')
        if matching[0]['phase'] == 'Stopped':
            shell('sandbox', 'start', NAME)
    for attempt in range(30):
        try:
            status()
            break
        except RuntimeError:
            if attempt == 29:
                raise
            time.sleep(1)
    forward()


def forward(name=NAME):
    if name != NAME:
        raise RuntimeError('Only the prepared agent may be forwarded')
    stop_forward(name)
    run(['systemctl', '--user', 'restart', 'market-shock-openshell-forward.service'])
    print('Agent forwarding managed by the application systemd service')


def serve_forward():
    gateway = run(['docker', 'network', 'inspect', 'market-shock_edge',
                   '--format', '{{(index .IPAM.Config 0).Gateway}}']).strip()
    # Foreground child belongs to systemd, not a short-lived operator shell.
    shell('forward', 'start', f'{gateway}:2024', NAME, discard=True, timeout=None)


def stop_forward(name=NAME):
    run(['systemctl', '--user', 'stop', 'market-shock-openshell-forward.service'])
    # Let the pinned CLI validate its own tracked process before signalling it.
    # Never kill a process merely because it occupies the expected port.
    tracked = RUNTIME / 'config/openshell/forwards' / f'{name}-2024.pid'
    if tracked.is_symlink():
        raise RuntimeError('Unsafe forward tracking file')
    if tracked.exists():
        shell('forward', 'stop', '2024', name)


def stop():
    observed = json.loads(shell('sandbox', 'get', NAME, '--output', 'json'))
    if observed['phase'] != 'Stopped':
        shell('sandbox', 'stop', NAME)
    stop_forward()
    print('Agent sandbox stopped; persistent application state preserved')


def recreate():
    """Explicit image/config/data refresh; never remove host state or traces."""
    verify()
    wait_dependencies()
    rows = json.loads(shell('sandbox', 'list', '--output', 'json'))
    matching = [row for row in rows if row['name'] == NAME]
    if len(matching) > 1:
        raise RuntimeError('Agent sandbox identity is ambiguous')
    stop_forward()
    if matching:
        shell('sandbox', 'delete', NAME)
        print('Replaced agent sandbox; host investigation state and traces retained')
    start()


def acquire_action_lock(action):
    """Lock direct sandbox creation/start once at the supported CLI boundary."""
    if action not in RETENTION_LOCKED_ACTIONS:
        return None
    inherited = os.environ.get('SPARK_RETENTION_LOCK_FD')
    try:
        if inherited is not None:
            if not inherited.isdigit():
                raise retention.RetentionError('invalid inherited retention lock')
            retention.verify_retention_lock(int(inherited), RETENTION_ROOT)
            return None
        return retention.acquire_retention_lock(RETENTION_ROOT)
    except retention.RetentionError:
        raise RuntimeError('OpenShell start/recreation is blocked by the retention/start lock') from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['launch', 'probe', 'forward', 'status', 'stop',
                                         'start', 'verify', 'prepare-receipt', 'serve-forward', 'recreate'])
    args = parser.parse_args()
    lock_fd = acquire_action_lock(args.action)
    try:
        if args.action == 'launch':
            launch()
        elif args.action == 'probe':
            launch('market-agent-probe', probe=True)
        elif args.action == 'forward':
            forward()
        elif args.action == 'serve-forward':
            serve_forward()
        elif args.action == 'status':
            status()
        elif args.action == 'start':
            start()
        elif args.action == 'recreate':
            recreate()
        elif args.action == 'verify':
            verify()
            print('Prepared OpenShell artifacts verified')
        elif args.action == 'prepare-receipt':
            prepare_receipt()
        else:
            stop()
    finally:
        if lock_fd is not None:
            os.close(lock_fd)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        # RuntimeError messages above are deliberately fixed, safe operator advice.
        # Never expose raw JSON, subprocess argv/stderr, or OS exception details.
        reason = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        raise SystemExit(f'OpenShell operation failed: {reason}; no fallback was started') from None
