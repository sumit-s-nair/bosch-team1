"""Discovery and summaries used by the two existing observation tools."""
import json
import math
import re
import time
from agent import config
from agent.tools import metrics, http

_NAMES = {}
RUNTIME_PREFIXES = ('jvm_', 'process_runtime_dotnet_', 'process_runtime_cpython_', 'process_runtime_go_', 'go_', 'nodejs_', 'process_cpu_', 'process_memory_')
SERVICE_LABELS = ('service_name', 'service', 'exported_service_name', 'job')
ROUTE_LABELS = ('http_route', 'http_target', 'url_path')
CLIENT_LABELS = ('client_address', 'client_ip', 'http_client_ip', 'user_agent_original', 'http_user_agent')


def names():
    key = config.prometheus_url()
    cached = _NAMES.get(key)
    if not cached or time.monotonic()-cached[0] >= 60:
        cached = (time.monotonic(), metrics.metric_names())
        _NAMES[key] = cached
    return cached[1]


def window_ok(window):
    if not isinstance(window, str) or not re.fullmatch(r'[1-9][0-9]*[smhd]', window):
        raise ValueError('window must be a positive duration, e.g. 5m')
    return window


def series(name):
    # Names originate from Prometheus discovery, but validate before PromQL interpolation.
    if not re.fullmatch(r'[a-zA-Z_:][a-zA-Z0-9_:]*', name):
        raise ValueError('Invalid discovered metric name')
    return http.get(f'{config.prometheus_url()}/api/v1/series', {'match[]': name, 'start': time.time()-3600}).get('data', [])


def selector(labels):
    return ','.join(f'{key}={json.dumps(value)}' for key, value in labels.items())


def scalar(query):
    rows = metrics.promql(query)
    value = rows[0]['value'] if rows else None
    return value if isinstance(value, (int, float)) and math.isfinite(value) else None


def service_selector(rows, service):
    for label in SERVICE_LABELS:
        if any(row.get(label) == service for row in rows):
            return {label: service}
    return None


def runtime(service, window, growth_threshold):
    window_ok(window)
    if not service:
        return {'runtime_kind': 'unknown', 'notes': ['Pass service to inspect runtime metrics']}
    candidates = [name for name in names() if name.startswith(RUNTIME_PREFIXES)]
    found, samples = {}, []
    if candidates:
        pattern = '|'.join(re.escape(prefix)+'.*' for prefix in RUNTIME_PREFIXES)
        matches = ['{__name__=~'+json.dumps(pattern)+','+label+'='+json.dumps(service)+'}' for label in SERVICE_LABELS]
        samples = http.get(f'{config.prometheus_url()}/api/v1/series', {'match[]': matches, 'start': time.time()-3600}).get('data', [])
        for name in candidates:
            labels = service_selector([row for row in samples if row.get('__name__') == name], service)
            if labels:
                found[name] = labels
    if not found:
        return {'runtime_kind': 'unknown', 'notes': [f'no runtime metrics found for {service}']}
    kinds = [('jvm', ('jvm_',)), ('dotnet', ('process_runtime_dotnet_',)), ('python', ('process_runtime_cpython_',)), ('go', ('go_', 'process_runtime_go_')), ('nodejs', ('nodejs_',))]
    kind = next((kind for kind, prefixes in kinds if any(name.startswith(prefixes) for name in found)), 'unknown')
    result = {'runtime_kind': kind, 'available_runtime_metrics': sorted(found)[:15], 'notes': []}

    def pick(predicate):
        return next((name for name in sorted(found) if predicate(name)), None)

    gc = pick(lambda n: ('gc' in n or 'garbage_collection' in n) and n.endswith(('_seconds_sum', '_milliseconds_sum', '_duration_sum', '_seconds_total', '_milliseconds_total', '_duration_total', '_collections_total', '_count_total', '_count')))
    heap = pick(lambda n: ('heap' in n or n == 'jvm_memory_used_bytes') and 'bytes' in n and ('used' in n or 'alloc_bytes' in n or 'heap_size_bytes' in n) and not n.endswith(('_total', '_count', '_sum', '_bucket')))
    if not heap:
        heap = pick(lambda n: ('memory' in n) and 'bytes' in n and ('usage' in n or 'used' in n or 'working_set' in n) and not n.endswith(('_total', '_count', '_sum', '_bucket')))
    cpu = pick(lambda n: 'cpu' in n and ('utilization' in n or n.endswith(('_seconds_total', '_time_seconds_total'))))
    for category, name in [('gc', gc), ('heap', heap), ('process_cpu', cpu)]:
        if not name:
            result['notes'].append(f'no supported {category} metric found for {service}')
            continue
        labels = dict(found[name])
        if category == 'heap' and name.startswith('jvm_'):
            areas = [row for row in samples if row.get('__name__') == name]
            heap_label = next(((key, row[key]) for row in areas if all(row.get(k) == v for k,v in labels.items()) for key in ('jvm_memory_type', 'type', 'area') if str(row.get(key, '')).lower() == 'heap'), None)
            if heap_label:
                labels[heap_label[0]] = heap_label[1]
            elif 'heap' not in name:
                result['notes'].append('JVM memory type label unavailable; heap usage cannot be isolated')
                continue
        expression = f'{name}{{{selector(labels)}}}'
        if category == 'heap':
            now = scalar(f'sum({expression})')
            start = scalar(f'sum({expression} offset {window})')
            growth = now-start if now is not None and start is not None else None
            pct = growth/start*100 if growth is not None and start > 0 else None
            result[category] = {'metric': name, 'unit': 'bytes', 'now': now, 'start': start, 'growth': growth, 'growth_pct': pct}
            if pct is not None:
                result['memory_growing'] = pct > growth_threshold
            elif start == 0:
                result['notes'].append('heap start value is zero; growth percentage and memory_growing are undefined')
        else:
            gauge = category == 'process_cpu' and 'utilization' in name
            aggregate = 'avg' if gauge else 'sum'
            function = 'avg_over_time' if gauge else 'rate'
            now = scalar(f'{aggregate}({function}({expression}[{window}]))')
            baseline = scalar(f'{aggregate}({function}({expression}[1h] offset {window}))')
            unit = 'ratio' if gauge else 'cpu_seconds_per_second'
            if category == 'gc':
                unit = 'events_per_second' if ('count' in name or 'collections' in name) else 'milliseconds_per_second' if 'milliseconds' in name else 'seconds_per_second' if 'seconds' in name else 'metric_units_per_second'
            result[category] = {'metric': name, 'unit': unit, 'now': now, 'baseline': baseline}
        if any(v is None for k,v in result[category].items() if k in ('now', 'start', 'baseline')):
            result['notes'].append(f'{category}: insufficient samples for current or baseline window')
    return result


def traffic(service, group_by, window, spike_threshold):
    window_ok(window)
    if group_by not in ('route', 'client'):
        raise ValueError('group_by must be route or client')
    discovered = names()
    candidates = [name for name in discovered if ('span' in name and 'calls' in name and not name.endswith(('_bucket', '_sum', '_count')))]
    result = {'window': window, 'baseline_window': f'1h offset {window}', 'group_by': group_by, 'items': [], 'notes': []}
    calls, rows, label = None, [], None
    for candidate in candidates:
        samples = series(candidate)
        if service:
            samples = [row for row in samples if service_selector([row], service)]
        options = ROUTE_LABELS if group_by == 'route' else CLIENT_LABELS
        field = next((key for key in options if any(row.get(key) for row in samples)), None)
        if field:
            calls, rows, label = candidate, samples, field
            break
    if not calls:
        result['notes'].append(f'no {group_by} label found in span call metrics'+(f' for {service}' if service else ''))
    else:
        rows = [row for row in rows if row.get(label)]
        service_label = next((key for key in SERVICE_LABELS if any(key in row for row in rows)), None)
        labels = {service_label: service} if service and service_label else {}
        labels_text = selector(labels)
        kinds = {row.get('span_kind') for row in rows}
        kind = next((value for value in ('SPAN_KIND_SERVER','server') if value in kinds), None)
        if kind:
            labels_text += (',' if labels_text else '')+'span_kind='+json.dumps(kind)
        labels_text += (',' if labels_text else '')+label+'!=""'
        groups = f'{service_label}, {label}' if service_label else label
        base_q = lambda span, offset='', extra='': f'sum by ({groups})(rate({calls}{{{labels_text}{extra}}}[{span}]{offset}))'
        current = metrics.promql(f'topk(10, {base_q(window)})')
        baseline = metrics.promql(base_q('1h', f' offset {window}'))
        error_values = {str(row.get('status_code')) for row in rows if 'status_code' in row}
        error_code = next((code for code in ('STATUS_CODE_ERROR', 'ERROR', 'Error', '2') if code in error_values), None)
        errors = metrics.promql(base_q(window, extra=',status_code='+json.dumps(error_code))) if error_code else []
        key = lambda row: (row['metric'].get(service_label), row['metric'].get(label))
        bases = {key(row): row['value'] for row in baseline}
        errs = {key(row): row['value'] for row in errors}
        for row in sorted(current, key=lambda r: r['value'] if r['value'] is not None else -1, reverse=True)[:10]:
            now, base = row['value'], bases.get(key(row))
            ratio = now/base if now is not None and base is not None and base > 0 else None
            item = {'service': row['metric'].get(service_label), group_by: row['metric'].get(label), 'rate_per_s': now, 'baseline_rate_per_s': base, 'ratio': ratio, 'error_rate_per_s': errs.get(key(row)), 'spike': ratio > spike_threshold if ratio is not None else None}
            result['items'].append(item)
        result['metric'] = calls
        result['label'] = label
        if not error_code:
            result['notes'].append('No observed error status value; error rates unavailable')
        if any(item['ratio'] is None for item in result['items']):
            result['notes'].append('Missing/zero baseline: ratio and spike unknown')
    gateways = [name for name in discovered if name.startswith(('nginx_', 'envoy_', 'proxy_')) and ('request' in name or 'requests' in name) and name.endswith(('_total', '_count')) and not any(part in name for part in ('duration', 'latency', 'bytes', 'bucket'))]
    if gateways:
        name = gateways[0]
        samples = series(name)
        labels = service_selector(samples, service) if service else {}
        if labels is not None:
            expression = name+'{'+selector(labels)+'}'
            result['gateway'] = {'metric': name, 'rate_per_s': scalar(f'sum(rate({expression}[{window}]))'), 'baseline_rate_per_s': scalar(f'sum(rate({expression}[1h] offset {window}))')}
    return result


def compact(value, budget=2900):
    """Preserve valid JSON and required structures; trim lists before strings."""
    while len(json.dumps(value, ensure_ascii=False)) > budget:
        lists = []
        def walk(node):
            if isinstance(node, dict):
                for item in node.values(): walk(item)
            elif isinstance(node, list):
                if len(node) > 1: lists.append(node)
                for item in node: walk(item)
        walk(value)
        if lists:
            max(lists, key=lambda x: len(json.dumps(x))).pop()
        elif len(value.get('summary', '')) > 100:
            value['summary'] = value['summary'][:len(value['summary'])//2]
        else:
            return {'error': 'Result too large; narrow service/window or grouping'}
    return value
