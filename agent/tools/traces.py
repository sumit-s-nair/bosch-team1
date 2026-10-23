"""Jaeger read-only API. JAEGER_URL is the base before /api."""
from agent import config
from agent.tools import http


def services():
    return http.get(f'{config.jaeger_url()}/api/services').get('data') or []


def find_traces(service, lookback='15m', limit=20, error_only=False):
    params = {'service': service, 'lookback': lookback, 'limit': limit}
    if error_only:
        params['tags'] = '{"error":"true"}'
    return http.get(f'{config.jaeger_url()}/api/traces', params).get('data') or []


def get_trace(trace_id):
    from urllib.parse import quote
    data = http.get(f'{config.jaeger_url()}/api/traces/{quote(trace_id, safe="")}').get('data') or []
    return data[0] if data else {}
