from agent import telemetry as tools


def test_backend_failure_is_data(monkeypatch):
    def fail(*args, **kwargs):
        raise ConnectionError('offline backend')
    monkeypatch.setattr(tools.metrics, 'settings', fail)

    assert 'error' in tools.query_metrics('payment')


def test_invalid_duration():
    assert 'error' in tools.query_traces('payment', lookback='invalid')


def test_truncation_is_json():
    import json
    result = tools.bounded({'lines': ['x'*10000]})
    assert len(json.dumps(result)) < 3000
    assert isinstance(result['lines'], list)
    assert len(result['lines'][0]) < 3000


def test_deploy_unavailable():
    assert tools.deploy_history() == {'events': [], 'note': 'no deploy history source'}
