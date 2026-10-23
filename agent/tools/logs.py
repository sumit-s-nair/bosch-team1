"""Read-only OpenSearch queries with configurable/discovered field names."""
import re
from functools import lru_cache
from agent import config
from agent.tools import http


def index():
    value = config.setting('LOGS_INDEX', 'otel-logs-*')
    if not re.fullmatch(r'[a-zA-Z0-9_*.-]+', value):
        raise ValueError('Invalid LOGS_INDEX')
    return value


def field_caps():
    return http.get(f'{config.opensearch_url()}/{index()}/_field_caps', {'fields': '*'}).get('fields', {})


@lru_cache(maxsize=1)
def fields():
    overrides = [config.setting('LOGS_SERVICE_FIELD'), config.setting('LOGS_LEVEL_FIELD'), config.setting('LOGS_BODY_FIELD')]
    available = field_caps() if not all(overrides) else {}
    candidates = [('resource.service.name', 'resource.attributes.service.name', 'resource.attributes.service.name.stringValue', 'service.name'), ('severityText', 'severity_text', 'severity.text'), ('body', 'body.stringValue', 'message')]
    output = []
    for explicit, options in zip(overrides, candidates):
        choice = explicit or next((name for name in options if name in available and 'object' not in available[name]), '')
        if not choice:
            raise ValueError('Log fields not found; run python -m scripts.discover and set LOGS_*_FIELD')
        output.append(choice)
    return output


@lru_cache(maxsize=1)
def time_field():
    """Timestamp field to filter/sort on (the demo's log index uses observedTimestamp, not @timestamp)."""
    explicit = config.setting('LOGS_TIME_FIELD')
    if explicit:
        return explicit
    caps = field_caps()
    return next((f for f in ('@timestamp', 'observedTimestamp', 'time', 'timestamp') if f in caps), '@timestamp')


def value_at(doc, path):
    if path in doc:
        return doc[path]
    parts = path.split('.')
    for end in range(len(parts), 0, -1):
        key = '.'.join(parts[:end])
        if key in doc:
            value = doc[key]
            return value if end == len(parts) else value_at(value, '.'.join(parts[end:])) if isinstance(value, dict) else None
    return None


# collector's own debug-exporter chatter, and flagd (its logs would reveal flag state)
NOISE_SERVICES = ['otelcol-contrib', 'otel-collector', 'flagd', 'flagd-ui']


def search_logs(service=None, min_severity=None, text=None, minutes=15, size=30):
    """Normalised recent logs. min_severity uses OTel numbers: 9 INFO, 13 WARN, 17 ERROR, 21 FATAL."""
    service_field, level_field, body_field = fields()
    number_field = config.setting('LOGS_SEVERITY_NUMBER_FIELD', 'severity.number')
    tf = time_field()
    must = [{'range': {tf: {'gte': f'now-{max(1, min(int(minutes), 1440))}m'}}}]
    if service:
        must.append({'match_phrase': {service_field: service}})
    if min_severity:
        must.append({'range': {number_field: {'gte': int(min_severity)}}})
    if text:
        must.append({'match_phrase': {body_field: text}})
    must_not = [{'match_phrase': {service_field: s}} for s in NOISE_SERVICES]
    query = {'size': max(1, min(int(size), 200)), 'sort': [{tf: {'order': 'desc', 'unmapped_type': 'date'}}],
             'query': {'bool': {'must': must, 'must_not': must_not}}}
    hits = http.post(f'{config.opensearch_url()}/{index()}/_search', query).get('hits', {}).get('hits', [])
    out = []
    for hit in hits:
        src = hit.get('_source', {})
        svc = value_at(src, service_field)
        if service and svc != service:   # text fields tokenize hyphenated names; keep exact matches only
            continue
        out.append({'id': hit.get('_id'), 'ts': value_at(src, tf), 'service': svc,
                    'severity': value_at(src, level_field), 'body': str(value_at(src, body_field))[:400],
                    'trace_id': src.get('traceId') or src.get('traceID') or src.get('trace_id')})
    return out


def get_log(doc_id):
    res = http.post(f'{config.opensearch_url()}/{index()}/_search', {'query': {'ids': {'values': [doc_id]}}})
    hits = res.get('hits', {}).get('hits', [])
    return hits[0]['_source'] if hits else None
