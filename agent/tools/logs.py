"""Read-only OpenSearch queries with configurable/discovered field names."""
import re
from agent import config
from agent.tools import http


def index():
    value = config.setting('LOGS_INDEX', 'otel-logs-*')
    if not re.fullmatch(r'[a-zA-Z0-9_*.-]+', value):
        raise ValueError('Invalid LOGS_INDEX')
    return value


def field_caps():
    return http.get(f'{config.opensearch_url()}/{index()}/_field_caps', {'fields': '*'}).get('fields', {})


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


def search_documents(service=None, severity=None, text=None, minutes=15, size=30):
    service_field, level_field, body_field = fields()
    must = [{'range': {'@timestamp': {'gte': f'now-{max(1,min(int(minutes),1440))}m'}}}]
    if service:
        must.append({'match_phrase': {service_field: service}})
    if severity:
        must.append({'match': {level_field: severity}})
    if text:
        must.append({'match_phrase': {body_field: text}})
    query = {'size': max(1,min(int(size),30)), 'sort': [{'@timestamp': 'desc'}], 'query': {'bool': {'must': must}}, '_source': ['@timestamp', service_field, level_field, body_field, 'trace_id', 'traceId']}
    result = http.post(f'{config.opensearch_url()}/{index()}/_search', query)
    hits = result.get('hits', {}).get('hits', [])
    if service:
        # Text fields may tokenize hyphenated names; retain only exact services.
        hits = [hit for hit in hits if value_at(hit.get('_source', {}), service_field) == service]
    return hits, body_field


def search_logs(service=None, severity=None, text=None, minutes=15, size=30):
    hits, _ = search_documents(service, severity, text, minutes, size)
    return [hit['_source'] for hit in hits]
