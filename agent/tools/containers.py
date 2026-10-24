"""Read-only Docker health on the demo network; never reads config or env."""
import json
import subprocess
import time
from agent import config


class DockerError(RuntimeError):
    pass


def _docker(*args):
    """Read-only docker call with 2 retries: Docker Desktop occasionally fails a call under load."""
    if not args or args[0] not in ('ps', 'inspect', 'stats'):
        raise ValueError('Docker operation is not allowlisted')
    err = ''
    for attempt in range(3):
        try:
            r = subprocess.run(['docker', *args], capture_output=True, text=True, timeout=config.http_timeout())
        except subprocess.TimeoutExpired:
            err = 'timed out'
        else:
            if r.returncode == 0:
                return r.stdout
            err = (r.stderr or r.stdout).strip()[:200]
        time.sleep(1 + attempt)
    raise DockerError(f'docker {args[0]} failed after 3 tries: {err}')


def list_containers():
    out = _docker('ps', '-a', '--filter', 'network='+config.setting('DOCKER_NETWORK', 'opentelemetry-demo'), '--format', '{{json .}}')
    rows = [json.loads(line) for line in out.splitlines() if line.strip()]
    return [{'name': row['Names'], 'id': row['ID'], 'state': row['State'], 'status': row['Status']} for row in rows]


def inspect(name):
    if not name or name.startswith('-'):
        raise ValueError('Invalid container name')
    out = _docker('inspect', '--format', '{"state":{{json .State}},"restart_count":{{json .RestartCount}},'
                  '"nano_cpus":{{json .HostConfig.NanoCpus}},"cpu_quota":{{json .HostConfig.CpuQuota}},'
                  '"cpu_period":{{json .HostConfig.CpuPeriod}},"mem_limit":{{json .HostConfig.Memory}}}', name)
    data = json.loads(out)
    state = data.get('state') or {}
    return {'name': name, 'status': state.get('Status'), 'health': (state.get('Health') or {}).get('Status'), 'running': state.get('Running'), 'restart_count': data.get('restart_count'), 'exit_code': state.get('ExitCode'), 'oom_killed': state.get('OOMKilled'), 'started_at': state.get('StartedAt'), 'finished_at': state.get('FinishedAt'),
            'cpu_limit_cores': cpu_limit_cores(data), 'mem_limit_bytes': data.get('mem_limit') or None}


def cpu_limit_cores(data):
    """CPU limit in cores from docker HostConfig, or None when unlimited."""
    nano = data.get('nano_cpus') or 0
    if nano > 0:
        return nano / 1e9                     # docker --cpus 0.5 -> 500000000
    quota, period = data.get('cpu_quota') or 0, data.get('cpu_period') or 0
    if quota > 0 and period > 0:
        return quota / period
    return None                               # unlimited


def stats():
    names = [row['name'] for row in list_containers()]
    if not names:
        return []
    return [json.loads(line) for line in _docker('stats', '--no-stream', '--format', '{{json .}}', *names).splitlines() if line.strip()]
