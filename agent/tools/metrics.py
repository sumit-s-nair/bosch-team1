"""Prometheus queries and span-metric discovery (no configuration reads)."""
import json
import math
import re
import time
from agent import config
from agent.tools import http


def promql(query):
    data = http.get(f'{config.prometheus_url()}/api/v1/query', {'query': query})
    if data.get('status') != 'success':
        raise ValueError(data.get('error', 'Prometheus query failed'))
    rows = []
    for row in data['data']['result']:
        value = float(row['value'][1])
        rows.append({'metric': row['metric'], 'value': value if math.isfinite(value) else None})
    return rows


def metric_names(match=''):
    names = http.get(f'{config.prometheus_url()}/api/v1/label/__name__/values')['data']
    return [name for name in names if match in name]


def settings():
    calls = config.setting('METRIC_CALLS')
    bucket = config.setting('METRIC_DURATION')
    if not calls or not bucket:
        names = metric_names()
        calls = calls or next((n for n in ('traces_span_metrics_calls_total', 'traces_span_metrics_calls', 'span_metrics_calls_total', 'span_metrics_calls') if n in names), '')
        bucket = bucket or next((n for n in ('traces_span_metrics_duration_milliseconds_bucket', 'traces_span_metrics_duration_seconds_bucket', 'span_metrics_duration_milliseconds_bucket', 'span_metrics_duration_seconds_bucket', 'traces_span_metrics_duration_bucket', 'span_metrics_duration_bucket') if n in names), '')
    if not calls or not bucket:
        raise ValueError('Span call counter / histogram not found. Run python -m scripts.discover and set METRIC_CALLS and METRIC_DURATION.')
    for name in (calls, bucket):
        if not re.fullmatch(r'[a-zA-Z_:][a-zA-Z0-9_:]*', name):
            raise ValueError('Invalid metric name')
    series = http.get(f'{config.prometheus_url()}/api/v1/series', {'match[]': calls, 'start': time.time()-3600}).get('data', [])
    label = config.setting('METRIC_SERVICE_LABEL') or next((key for key in ('service_name', 'service') if any(key in row for row in series)), '')
    if not re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_]*', label):
        raise ValueError('Set METRIC_SERVICE_LABEL after inspecting discovery output')
    units = config.setting('METRIC_DURATION_UNIT')
    if not units:
        units = 'ms' if 'milliseconds' in bucket else ('s' if 'seconds' in bucket else '')
    if units not in ('ms', 's'):
        raise ValueError('Set METRIC_DURATION_UNIT=ms or s; histogram units cannot be inferred')
    kinds = {row.get('span_kind') for row in series}
    kind = next((k for k in ('SPAN_KIND_SERVER', 'server') if k in kinds), None)
    codes = sorted({str(row['status_code']) for row in series if 'status_code' in row})
    error_selector = config.setting('METRIC_ERROR_SELECTOR')
    if not error_selector and codes:
        error_code = next((code for code in ('STATUS_CODE_ERROR', 'ERROR', 'Error', '2') if code in codes), 'STATUS_CODE_ERROR')
        error_selector = 'status_code='+json.dumps(error_code)
    return {'calls': calls, 'bucket': bucket, 'label': label, 'unit': units, 'kind': kind, 'services_seen': sorted({str(row[label]) for row in series if label in row}), 'status_codes_seen': codes, 'error_selector': error_selector or None}


def selectors(settings, service):
    value = f'{settings["label"]}={json.dumps(service)}'
    if settings['kind']:
        value += ',span_kind='+json.dumps(settings['kind'])
    return value
