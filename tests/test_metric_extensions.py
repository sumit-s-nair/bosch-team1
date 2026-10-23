import json
import pytest
from agent import brain
from agent.tools import observe
from agent.tools import metric_details, mock_tools
from agent.evidence import Ledger
from agent.tools import metrics, http


@pytest.fixture(autouse=True)
def clear_discovery_cache():
    metric_details._NAMES.clear()
    yield
    metric_details._NAMES.clear()


@pytest.fixture
def prometheus(monkeypatch):
    calls = {'names': 0, 'queries': []}
    runtime_names = ['jvm_gc_duration_seconds_sum', 'jvm_memory_used_bytes', 'process_cpu_utilization_ratio']
    call_metric = 'traces_span_metrics_calls_total'
    def names():
        calls['names'] += 1
        return runtime_names + [call_metric, 'nginx_http_requests_total']
    def get(url, params=None):
        if isinstance(params['match[]'], list):
            return {'data': [{'__name__': name, 'service_name': 'store', 'jvm_memory_type': 'heap'} for name in runtime_names]}
        name = params['match[]']
        if name == call_metric:
            return {'data': [{'__name__': name, 'service_name': 'store', 'http_route': '/products', 'span_kind': 'SPAN_KIND_SERVER', 'status_code': 'STATUS_CODE_ERROR'}]}
        return {'data': [{'__name__': name, 'service_name': 'store'}]}
    def query(q):
        calls['queries'].append(q)
        if 'nginx_http_requests_total' in q:
            return [{'metric': {}, 'value': 10.0}]
        if call_metric in q:
            base = 'offset' in q
            error = 'status_code=' in q
            return [{'metric': {'service_name': 'store', 'http_route': '/products'}, 'value': 1.0 if error else 10.0 if base else 40.0}]
        if 'jvm_memory_used_bytes' in q:
            return [{'metric': {}, 'value': 100.0 if 'offset' in q else 150.0}]
        if 'jvm_gc' in q:
            return [{'metric': {}, 'value': .02 if 'offset' in q else .18}]
        if 'process_cpu_utilization_ratio' in q:
            return [{'metric': {}, 'value': .2 if 'offset' in q else .8}]
        return []
    monkeypatch.setattr(metrics, 'metric_names', names)
    monkeypatch.setattr(http, 'get', get)
    monkeypatch.setattr(metrics, 'promql', query)
    return calls


def test_runtime_discovery_and_growth(prometheus):
    result = observe.resource_usage(Ledger(), service='store')
    runtime = result['runtime']
    assert runtime['runtime_kind'] == 'jvm'
    assert runtime['heap']['growth'] == 50
    assert runtime['heap']['growth_pct'] == 50
    assert runtime['memory_growing'] is True
    assert runtime['gc']['baseline'] == .02
    assert runtime['process_cpu']['now'] == .8
    assert len(runtime['available_runtime_metrics']) == 3
    assert any('jvm_memory_type="heap"' in q for q in prometheus['queries'])
    assert any('[1h] offset 10m' in q for q in prometheus['queries'])
    assert len(json.dumps(result)) < 3000


def test_runtime_none_for_unmatched_service(prometheus):
    result = observe.resource_usage(Ledger(), service='different')
    assert result['runtime']['runtime_kind'] == 'unknown'
    assert 'no runtime metrics found for different' in result['runtime']['notes']


def test_runtime_no_names(monkeypatch):
    monkeypatch.setattr(metrics, 'metric_names', lambda: [])
    monkeypatch.setattr(metrics, 'promql', lambda q: [])
    assert observe.resource_usage(Ledger(), service='store')['runtime']['runtime_kind'] == 'unknown'


def test_discovery_cached_for_sixty_seconds(prometheus, monkeypatch):
    now = [0]
    monkeypatch.setattr(metric_details.time, 'monotonic', lambda: now[0])
    metric_details.names()
    now[0] = 59
    metric_details.names()
    assert prometheus['names'] == 1
    now[0] = 61
    metric_details.names()
    assert prometheus['names'] == 2


def test_routes_and_gateway(prometheus):
    result = observe.service_overview(Ledger(), service='store', group_by='route')
    route = result['items'][0]
    assert route['route'] == '/products'
    assert route['ratio'] == 4.0
    assert route['spike'] is True
    assert route['error_rate_per_s'] == 1.0
    assert result['gateway']['metric'] == 'nginx_http_requests_total'
    assert any('[1h] offset 5m' in q for q in prometheus['queries'])
    assert len(json.dumps(result)) < 3000


def test_client_absent_is_note(prometheus):
    result = observe.service_overview(Ledger(), group_by='client')
    assert 'error' not in result
    assert any('no client label' in note for note in result['notes'])


def test_missing_baseline_not_invented(prometheus, monkeypatch):
    old = metrics.promql
    monkeypatch.setattr(metrics, 'promql', lambda q: [] if 'offset' in q else old(q))
    item = observe.service_overview(Ledger(), group_by='route')['items'][0]
    assert item['baseline_rate_per_s'] is None
    assert item['ratio'] is None and item['spike'] is None


def test_routes_top_ten_and_bounded(prometheus, monkeypatch):
    old = metrics.promql
    def query(q):
        if 'traces_span_metrics_calls_total' in q:
            return [{'metric': {'service_name': 'store', 'http_route': f'/products/{i}'}, 'value': i+1.0} for i in range(30)]
        return old(q)
    monkeypatch.setattr(metrics, 'promql', query)
    result = observe.service_overview(Ledger(), group_by='route')
    assert len(result['items']) <= 10
    assert result['items'][0]['route'] == '/products/29'
    assert len(json.dumps(result)) < 3000


@pytest.mark.parametrize('name,args', [('resource_usage', {'service': 'store'}), ('service_overview', {}), ('service_overview', {'group_by': 'route'})])
def test_unreachable_returns_error(monkeypatch, name, args):
    def fail(*a, **k):
        raise ConnectionError('Prometheus unreachable')
    monkeypatch.setattr(metrics, 'promql', fail)
    monkeypatch.setattr(metrics, 'metric_names', fail)
    result = getattr(observe, name)(Ledger(), **args)
    assert 'Prometheus unreachable' in result['error']


def test_old_signatures_and_schema(prometheus):
    assert isinstance(observe.service_overview(Ledger()), str)
    assert 'summary' in observe.resource_usage(Ledger())
    specs = brain._tool_specs(['store'])
    assert len(specs) == len(observe.TOOLS) == 14   # 8 original + db_health + 2 P1 + 3 P2 tools
    parameters = {s['function']['name']: s['function']['parameters'] for s in specs}
    assert parameters['resource_usage']['required'] == []
    assert parameters['service_overview']['properties']['group_by']['enum'] == ['route', 'client']


def test_dict_tool_result_is_valid_json(prometheus):
    result = brain._run_tool(Ledger(), 'resource_usage', {'service': 'store'})
    assert json.loads(result)['runtime']['runtime_kind'] == 'jvm'


def test_mock_fallbacks(monkeypatch):
    monkeypatch.setenv('MOCK_MODE', 'no_runtime')
    assert mock_tools.resource_usage(service='store')['runtime']['runtime_kind'] == 'unknown'
    assert mock_tools.service_overview(group_by='client')['notes']


def test_gc_count_reports_events_not_seconds(prometheus, monkeypatch):
    monkeypatch.setattr(metrics, 'metric_names', lambda: ['go_gc_duration_seconds_count'])
    monkeypatch.setattr(http, 'get', lambda *args, **kwargs: {'data': [{'__name__': 'go_gc_duration_seconds_count', 'service_name': 'store'}]})
    monkeypatch.setattr(metrics, 'promql', lambda q: [{'metric': {}, 'value': 2.0}])
    runtime = observe.resource_usage(Ledger(), service='store')['runtime']
    assert runtime['runtime_kind'] == 'go'
    assert runtime['gc']['unit'] == 'events_per_second'


def test_zero_heap_start_not_growth_hint(prometheus, monkeypatch):
    old = metrics.promql
    def query(q):
        if 'jvm_memory_used_bytes' in q and 'offset' in q:
            return [{'metric': {}, 'value': 0.0}]
        return old(q)
    monkeypatch.setattr(metrics, 'promql', query)
    runtime = observe.resource_usage(Ledger(), service='store')['runtime']
    assert runtime['heap']['growth_pct'] is None
    assert 'memory_growing' not in runtime
    assert any('zero' in note for note in runtime['notes'])
