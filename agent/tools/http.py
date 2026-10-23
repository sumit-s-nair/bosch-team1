"""Read-only telemetry transport; redirects and flag endpoints are refused."""
from urllib.parse import unquote, urlsplit
import requests
from agent import config


class ForbiddenSource(ValueError):
    pass


def _check(url):
    parsed = urlsplit(url)
    low = unquote(parsed.netloc + parsed.path).lower()
    if parsed.scheme not in ('http', 'https') or parsed.username or parsed.password:
        raise ForbiddenSource('Only HTTP(S) endpoints without embedded credentials are allowed')
    if any(part in low for part in ('flagd', '/feature', ':8013', ':8016', '/flag')):
        raise ForbiddenSource('Feature-flag sources are off-limits')


def _request(method, url, **kwargs):
    _check(url)
    response = requests.request(method, url, timeout=config.http_timeout(), allow_redirects=False, **kwargs)
    if 300 <= response.status_code < 400:
        raise ForbiddenSource('Telemetry redirects are disabled; configure the final endpoint explicitly')
    response.raise_for_status()
    return response.json()


def get(url, params=None):
    return _request('GET', url, params=params)


def post(url, json=None):
    if not urlsplit(url).path.rstrip('/').endswith('/_search'):
        raise ForbiddenSource('POST is allowed only for read-only OpenSearch _search')
    return _request('POST', url, json=json)
