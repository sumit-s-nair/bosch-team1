"""Read-only Docker health on the demo network; never reads config or env."""
import json
import subprocess
from agent import config


def _docker(*args):
    if not args or args[0] not in ('ps', 'inspect', 'stats'):
        raise ValueError('Docker operation is not allowlisted')
    return subprocess.run(['docker', *args], capture_output=True, text=True, timeout=config.http_timeout(), check=True).stdout


def list_containers():
    out = _docker('ps', '-a', '--filter', 'network='+config.setting('DOCKER_NETWORK', 'opentelemetry-demo'), '--format', '{{json .}}')
    rows = [json.loads(line) for line in out.splitlines() if line.strip()]
    return [{'name': row['Names'], 'id': row['ID'], 'state': row['State'], 'status': row['Status']} for row in rows]


def inspect(name):
    if not name or name.startswith('-'):
        raise ValueError('Invalid container name')
    out = _docker('inspect', '--format', '{"state":{{json .State}},"restart_count":{{json .RestartCount}}}', name)
    data = json.loads(out)
    state = data.get('state', {})
    return {'name': name, 'status': state.get('Status'), 'health': state.get('Health', {}).get('Status'), 'running': state.get('Running'), 'restart_count': data.get('restart_count'), 'exit_code': state.get('ExitCode'), 'oom_killed': state.get('OOMKilled'), 'started_at': state.get('StartedAt'), 'finished_at': state.get('FinishedAt')}


def stats():
    names = [row['name'] for row in list_containers()]
    if not names:
        return []
    return [json.loads(line) for line in _docker('stats', '--no-stream', '--format', '{{json .}}', *names).splitlines() if line.strip()]
