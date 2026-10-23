"""Exercise real adapters over fake HTTP/Docker responses, never the network."""
import json
from types import SimpleNamespace as NS
import pytest
from agent import config, telemetry
from agent.tools import http, logs, metrics, containers
from agent.loop import run_agent
from agent.evidence import verify_evidence
from test_agent import Client, diagnosis


@pytest.fixture
def backend(monkeypatch):
    for name in ('METRIC_CALLS', 'METRIC_DURATION', 'METRIC_SERVICE_LABEL', 'METRIC_DURATION_UNIT', 'LOGS_SERVICE_FIELD', 'LOGS_LEVEL_FIELD', 'LOGS_BODY_FIELD', 'METRIC_ERROR_SELECTOR'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('PROMETHEUS_URL', 'http://prom.test:9090')
    monkeypatch.setenv('JAEGER_URL', 'http://jaeger.test/jaeger/ui')
    monkeypatch.setenv('OPENSEARCH_URL', 'http://logs.test:9200')
    calls = []
    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if url.endswith('/label/__name__/values'):
            value = {'data': ['traces_span_metrics_calls_total', 'traces_span_metrics_duration_seconds_bucket']}
        elif url.endswith('/series'):
            value = {'data': [{'service_name': s, 'span_kind': 'SPAN_KIND_SERVER', 'status_code': 'STATUS_CODE_ERROR'} for s in ('checkout', 'payment')]}
        elif url.endswith('/query'):
            query = kwargs['params']['query']
            value = {'status': 'success', 'data': {'result': [{'metric': {}, 'value': [123, '.95' if 'histogram_quantile' in query else '12' if 'status_code' in query else '20']}]}}
        elif url.endswith('/api/services'):
            value = {'data': ['checkout', 'payment']}
        elif url.endswith('/api/traces'):
            value = {'data': [{'traceID': 'trace-123', 'processes': {'p1': {'serviceName': 'checkout'}, 'p2': {'serviceName': 'payment'}}, 'spans': [{'spanID': 's1', 'processID': 'p1', 'operationName': 'checkout', 'duration': 950000, 'tags': []}, {'spanID': 's2', 'processID': 'p2', 'operationName': 'charge', 'duration': 800000, 'tags': [{'key': 'error', 'value': True}], 'references': [{'refType': 'CHILD_OF', 'spanID': 's1'}], 'logs': [{'fields': [{'key': 'message', 'value': 'provider connection refused'}]}]}]}]}
        elif url.endswith('/_field_caps'):
            value = {'fields': {name: {'text': {}} for name in ('resource.service.name', 'severityText', 'body')}}
        elif url.endswith('/_search'):
            value = {'hits': {'hits': [{'_id': 'log-1', '_source': {'@timestamp': '2026-01-01T00:00:00Z', 'resource': {'service.name': 'payment'}, 'body': 'provider connection refused'}}]}}
        else:
            raise AssertionError(f'Unexpected network request: {url}')
        return NS(status_code=200, raise_for_status=lambda: None, json=lambda: value)
    monkeypatch.setattr(http.requests, 'request', request)
    return calls


def test_metrics_discovery_baseline_and_units(backend):
    value = telemetry.query_metrics('payment')
    assert value['p95_ms'] == 950
    assert value['error_rate'] == 12
    assert value['baseline']['p95_ms'] == 950
    queries = [kwargs['params']['query'] for _, url, kwargs in backend if url.endswith('/query')]
    assert any('[1h] offset 5m' in query for query in queries)
    assert all('span_kind="SPAN_KIND_SERVER"' in query for query in queries)


def test_service_discovery(backend):
    assert 'payment' in telemetry.query_metrics('wrong-name')['services_seen']


def test_downstream_traces(backend):
    rows = telemetry.query_traces('checkout')
    assert rows[0]['service'] == 'payment'
    assert rows[0]['parent_span_ids'] == ['s1']
    assert rows[0]['messages'] == ['provider connection refused']


def test_log_mapping_and_read_only_search(backend):
    assert 'provider connection refused' in telemetry.query_logs('payment')[0]
    method, url, kwargs = backend[-1]
    assert method == 'POST' and url.endswith('/_search')
    assert {'match_phrase': {'resource.service.name': 'payment'}} in kwargs['json']['query']['bool']['must']


def test_adapter_loop_end_to_end(backend):
    diag = diagnosis(root_cause='Provider connection refused', evidence=[{'source': 'query_traces:trace-123', 'quote': 'trace-123'}, {'source': 'query_logs:log-1', 'quote': 'provider connection refused'}])
    client = Client([('query_metrics', {'service': 'payment'}), ('query_traces', {'service': 'checkout'}), ('query_logs', {'service': 'payment'}), ('submit_diagnosis', diag)])
    result = run_agent('Checkout failing', client=client, tools_module=telemetry)
    assert not result['escalate']
    assert not result['unverified_evidence']
    assert len(result['investigation_trace']) == 4
    assert all(kwargs['allow_redirects'] is False and kwargs['timeout'] <= 15 for _, _, kwargs in backend)


@pytest.mark.parametrize('url', ['http://flagd:8013', 'http://localhost:8080/feature', 'http://localhost:8016', 'http://localhost/%66lagd'])
def test_flag_sources_refused(url):
    with pytest.raises(http.ForbiddenSource):
        http.get(url)


def test_redirect_refused(monkeypatch):
    monkeypatch.setattr(http.requests, 'request', lambda *a, **k: NS(status_code=302))
    with pytest.raises(http.ForbiddenSource):
        http.get('http://prom.test/api')


def test_write_post_refused():
    with pytest.raises(http.ForbiddenSource):
        http.post('http://logs.test/index/_delete_by_query', {})


def test_flat_and_nested_log_fields():
    assert logs.value_at({'resource': {'attributes': {'service.name': 'payment'}}}, 'resource.attributes.service.name') == 'payment'
    assert logs.value_at({'resource.service.name': 'payment'}, 'resource.service.name') == 'payment'


def test_docker_health_limits_inspection(monkeypatch):
    calls = []
    def execute(args, **kwargs):
        calls.append(args)
        if args[1] == 'ps':
            text = json.dumps({'Names': 'payment', 'ID': 'abc', 'State': 'running', 'Status': 'Up'})
        elif args[1] == 'stats':
            text = json.dumps({'Name': 'payment', 'CPUPerc': '75%', 'MemUsage': '100MiB / 1GiB'})
        else:
            assert '--format' in args
            assert '.Config' not in args[args.index('--format')+1]
            text = json.dumps({'state': {'Status': 'running', 'Health': {'Status': 'unhealthy'}, 'OOMKilled': False}, 'restart_count': 4})
        return NS(stdout=text)
    monkeypatch.setattr(containers.subprocess, 'run', execute)
    result = telemetry.container_health()
    assert result['containers'][0]['health'] == 'unhealthy'
    assert result['containers'][0]['restart_count'] == 4
    assert result['containers'][0]['cpu'] == '75%'
    assert all(command[1] in ('ps', 'inspect', 'stats') for command in calls)


def test_docker_failure_is_error(monkeypatch):
    def fail(*args, **kwargs):
        raise FileNotFoundError('docker unavailable')
    monkeypatch.setattr(containers.subprocess, 'run', fail)
    assert 'error' in telemetry.container_health()


def test_legacy_ollama_config(monkeypatch):
    monkeypatch.setenv('LLM_MODEL', 'ollama_chat/qwen3:8b')
    monkeypatch.delenv('LLM_BASE_URL', raising=False)
    monkeypatch.setenv('LLM_API_BASE', 'http://localhost:11434')
    monkeypatch.delenv('LLM_API_KEY', raising=False)
    settings = config.llm_settings()
    assert settings == {'model': 'qwen3:8b', 'base_url': 'http://localhost:11434/v1', 'api_key': 'ollama'}


def test_explicit_provider_path_preserved(monkeypatch):
    monkeypatch.setenv('LLM_BASE_URL', 'https://provider.example/custom/api')
    monkeypatch.setenv('LLM_API_KEY', 'fake-test-key')
    assert config.llm_settings()['base_url'] == 'https://provider.example/custom/api'


def test_evidence_decodes_log_text():
    quote = 'failed with "connection refused"\nretrying'
    diag = diagnosis(evidence=[{'source': 'query_logs:log-1', 'quote': quote}])
    text = json.dumps(['log-1 '+quote])
    assert not verify_evidence(diag, [text], records=[('query_logs', text)])['unverified_evidence']


def test_wrong_evidence_id_rejected():
    diag = diagnosis(evidence=[{'source': 'query_logs:invented-id', 'quote': 'connection refused'}])
    result = verify_evidence(diag, ['connection refused'], records=[('query_logs', 'connection refused')])
    assert result['escalate']


def test_missing_series_not_healthy(backend, monkeypatch):
    monkeypatch.setattr(metrics, 'promql', lambda query: [])
    result = telemetry.query_metrics('payment')
    assert result['request_rate'] is None
    assert result['error_rate'] is None
    assert result['p95_ms'] is None


def test_nan_prometheus_value_becomes_none(monkeypatch):
    monkeypatch.setattr(http, 'get', lambda *a, **k: {'status': 'success', 'data': {'result': [{'metric': {}, 'value': [0, 'NaN']}]}})
    assert metrics.promql('test')[0]['value'] is None


def test_http_failure_becomes_tool_error(backend, monkeypatch):
    def fail(*args, **kwargs):
        raise TimeoutError('backend timeout')
    monkeypatch.setattr(http.requests, 'request', fail)
    assert 'error' in telemetry.query_logs('payment')
    assert 'error' in telemetry.query_traces('payment')
    assert 'error' in telemetry.query_metrics('payment')


def test_setup_checker_uses_shared_model(monkeypatch):
    from scripts import check_setup
    captured = {}
    def create(**kwargs):
        captured.update(kwargs)
        return NS(choices=[NS(message=NS(tool_calls=[NS(function=NS(name='container_health', arguments='{}'))]))])
    monkeypatch.setattr(config, 'llm_client', lambda: NS(chat=NS(completions=NS(create=create))))
    monkeypatch.setenv('LLM_MODEL', 'ollama_chat/qwen3:8b')
    monkeypatch.setenv('LLM_BASE_URL', 'http://localhost:11434/v1')
    assert check_setup.c_llm()['tool_calling']
    assert captured['model'] == 'qwen3:8b'


def test_cli_saves_artifact(monkeypatch, tmp_path, capsys):
    import main
    target = tmp_path/'run'/'diagnosis.json'
    result = diagnosis()
    monkeypatch.setattr(main, 'run_agent', lambda *args: result)
    monkeypatch.setattr('sys.argv', ['main.py', 'payment alert', '--output', str(target)])
    assert main.main() == 0
    assert json.loads(target.read_text()) == result
    assert json.loads(capsys.readouterr().out) == result


def test_log_service_must_match_exactly(backend, monkeypatch):
    original = http.post
    def search(*args, **kwargs):
        result = original(*args, **kwargs)
        result['hits']['hits'][0]['_source']['resource']['service.name'] = 'payment-helper'
        return result
    monkeypatch.setattr(http, 'post', search)
    assert telemetry.query_logs('payment')['logs'] == []
