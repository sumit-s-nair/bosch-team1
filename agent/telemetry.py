"""Bounded, failure-safe model tools built on the shared read-only clients."""
import functools
import re
import time
from agent import config
from agent.results import bounded
from agent.tools import metrics, traces, logs, containers


def safe(fn):
    @functools.wraps(fn)
    def call(*args, **kwargs):
        try:
            return bounded(fn(*args, **kwargs))
        except Exception as exc:
            return {'error': f'{type(exc).__name__}: {str(exc)[:450]}'}
    return call


def duration(value):
    if not isinstance(value, str) or not re.fullmatch(r'[1-9][0-9]*[smhd]', value):
        raise ValueError('window must be a positive duration such as 5m')
    return int(value[:-1]) * {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}[value[-1]]


@safe
def query_metrics(service, window='5m'):
    duration(window)
    settings = metrics.settings()
    if not service or service not in settings['services_seen']:
        return {'service': service, 'services_seen': settings['services_seen'], 'hint': 'Choose an exact service name from services_seen; verify METRIC_SERVICE_LABEL if no services appear.'}
    selector = metrics.selectors(settings, service)
    result = {'service': service, 'window': window, 'timestamp': time.time(), 'baseline_window': '1h preceding current window', 'metric_source': settings, 'baseline': {}}
    error = settings['error_selector']
    for target, span, offset in [(result, window, ''), (result['baseline'], '1h', f' offset {window}')]:
        calls, bucket = settings['calls'], settings['bucket']
        expressions = {
            'request_rate': f'sum(rate({calls}{{{selector}}}[{span}]{offset}))',
            'error_rate': f'sum(rate({calls}{{{selector},{error}}}[{span}]{offset}))',
            'p95_ms': f'histogram_quantile(0.95,sum by(le)(rate({bucket}{{{selector}}}[{span}]{offset})))'}
        for key, expression in expressions.items():
            if key == 'error_rate' and not error:
                target[key] = None
                continue
            rows = metrics.promql(expression)
            value = rows[0]['value'] if rows else None
            if key == 'p95_ms' and value is not None and settings['unit'] == 's':
                value *= 1000
            target[key] = value
        # A missing error series means zero only when a real request series exists.
        if error and settings['status_codes_seen'] and target['error_rate'] is None and target['request_rate'] is not None and target['request_rate'] > 0:
            target['error_rate'] = 0.0
    return result


@safe
def query_traces(service, lookback='15m', errors_only=True, limit=10):
    duration(lookback)
    limit = max(1,min(int(limit),20))
    data = traces.find_traces(service, lookback, limit, errors_only)
    output = []
    for trace in data:
        for span in trace.get('spans', []):
            process = trace.get('processes', {}).get(span.get('processID'), {})
            tags = {t['key']: t.get('value') for t in span.get('tags', []) if t['key'] in ('error', 'http.status_code', 'http.response.status_code', 'rpc.grpc.status_code', 'grpc.status_code', 'otel.status_code', 'status.code')}
            messages = []
            for log in span.get('logs', []):
                for field in log.get('fields', []):
                    if field.get('key') in ('message', 'event', 'error.message', 'exception.message', 'exception.type'):
                        messages.append(str(field.get('value', ''))[:300])
            # Include downstream spans, not just the initially queried victim.
            output.append({'trace_id': trace['traceID'], 'span_id': span.get('spanID'), 'service': process.get('serviceName', 'unknown'), 'operation': span.get('operationName'), 'duration_ms': span.get('duration', 0)/1000, 'timestamp': span.get('startTime'), 'error_tags': tags, 'messages': messages[:2], 'parent_span_ids': [ref.get('spanID') for ref in span.get('references', []) if ref.get('refType') == 'CHILD_OF']})
    if not output:
        return {'traces': [], 'services_seen': traces.services()[:30], 'hint': 'Try errors_only=false for slow or untagged failures.'}
    def failed(item):
        tags = item['error_tags']
        return any(str(v) not in ('0', 'OK', 'None', '') for k,v in tags.items() if k in ('rpc.grpc.status_code', 'grpc.status_code')) or tags.get('error') in (True, 'true') or any(str(v) in ('ERROR', 'STATUS_CODE_ERROR', '2') for k,v in tags.items() if k in ('otel.status_code', 'status.code')) or any(str(v).startswith('5') for k,v in tags.items() if k in ('http.status_code','http.response.status_code'))
    return sorted(output, key=lambda item: (failed(item), item['duration_ms']), reverse=True)[:limit]


@safe
def query_logs(service, level='ERROR', contains='', minutes=15, limit=15):
    hits, field = logs.search_documents(service, level, contains, minutes, limit)
    output, seen = [], set()
    for hit in hits:
        source = hit['_source']
        body = str(logs.value_at(source, field) or '')
        if body in seen:
            continue
        seen.add(body)
        output.append(f"{source.get('@timestamp', '')} [{hit.get('_id', '')}] {body[:300]}")
    return output or {'logs': [], 'hint': 'Try level="" for all severities; verify mappings with python -m scripts.discover.'}


@safe
def container_health():
    rows = containers.list_containers()
    usage = {row.get('Name'): row for row in containers.stats()} if rows else {}
    output = []
    for row in rows:
        stat = usage.get(row['name'], {})
        try:
            state = containers.inspect(row['name'])
        except Exception as exc:
            state = {'name': row['name'], 'status': row['state'], 'error': str(exc)[:150]}
        output.append({**state, 'cpu': stat.get('CPUPerc'), 'memory': stat.get('MemUsage')})
    output.sort(key=lambda row: (row.get('status') == 'running' and row.get('health') != 'unhealthy', -int(row.get('restart_count') or 0)))
    return {'containers': output, 'total_containers': len(rows), 'note': 'No containers found on configured demo network' if not rows else 'Unhealthy/stopped/restarting containers first'}


@safe
def deploy_history():
    return {'events': [], 'note': 'no deploy history source'}
