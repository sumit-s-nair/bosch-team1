import json
from types import SimpleNamespace as NS
from agent.loop import run_agent
from agent.evidence import verify_evidence
from agent.schema import validate_output
from agent import mock_tools


def diagnosis(**updates):
    value = {'root_cause_component': 'payment', 'root_cause': 'Provider connection refused', 'confidence': .9, 'evidence': [{'source': 'query_traces:mock-trace-001', 'quote': 'mock-trace-001'}, {'source': 'query_logs', 'quote': 'payment provider connection refused'}], 'escalate': False}
    return dict(value, **updates)


class Client:
    def __init__(self, calls):
        self.calls = iter(calls)
        self.chat = NS(completions=self)
    def create(self, **kwargs):
        name, args = next(self.calls)
        calls = [NS(id='call', function=NS(name=name, arguments=json.dumps(args)))] if name else []
        return NS(choices=[NS(message=NS(content=None, tool_calls=calls))])


def run(calls, **kwargs):
    return run_agent('payment errors', client=Client(calls), tools_module=mock_tools, **kwargs)


def test_clear(monkeypatch):
    monkeypatch.delenv('MOCK_MODE', raising=False)
    result = run([('query_metrics', {'service': 'payment'}), ('query_traces', {'service': 'payment'}), ('query_logs', {'service': 'payment'}), ('submit_diagnosis', diagnosis())])
    assert not result['escalate']
    assert not result['unverified_evidence']


def test_empty(monkeypatch):
    monkeypatch.setenv('MOCK_MODE', 'empty')
    result = run([('query_traces', {'service': 'payment'}), ('submit_diagnosis', diagnosis())])
    assert result['escalate'] and result['confidence'] <= .3


def test_unknown():
    result = run([('unknown', {}), ('submit_diagnosis', diagnosis(evidence=[]))])
    assert result['escalate']
    assert 'unknown tool' in result['investigation_trace'][0]['result_preview']


def test_exhaustion():
    assert run([(None, {})], max_steps=1)['root_cause'] == 'inconclusive after max steps'


def test_schema_retry():
    assert run([('submit_diagnosis', {}), ('submit_diagnosis', {})])['escalate']


def test_evidence_present():
    result = verify_evidence(diagnosis(), ['mock-trace-001 payment provider connection refused'])
    assert not result['unverified_evidence']


def test_evidence_absent():
    result = verify_evidence(diagnosis(), [])
    assert result['confidence'] <= .3 and result['escalate']
    assert len(result['unverified_evidence']) == 2


def test_no_evidence():
    result = verify_evidence(diagnosis(evidence=[]), [])
    assert result['confidence'] <= .2 and result['escalate']


def test_wrong_source():
    result = verify_evidence(diagnosis(), ['mock-trace-001 payment provider connection refused'], records=[('query_metrics', 'mock-trace-001 payment provider connection refused')])
    assert result['escalate']


def test_schema():
    assert validate_output(diagnosis())[0]
    assert not validate_output(diagnosis(confidence=2))[0]
    assert not validate_output({})[0]


def test_model_failure_escalates():
    class FailedClient:
        def __init__(self):
            self.chat = NS(completions=self)
        def create(self, **kwargs):
            raise TimeoutError('model unavailable')
    result = run_agent('alert', client=FailedClient(), tools_module=mock_tools)
    assert result['escalate'] and result['confidence'] == 0


def test_injected_client_needs_no_credentials(monkeypatch):
    monkeypatch.setenv('LLM_BASE_URL', 'https://example.invalid/api')
    monkeypatch.delenv('LLM_API_KEY', raising=False)
    assert run([('submit_diagnosis', diagnosis(evidence=[]))])['escalate']


def test_coverage_requires_metrics_traces_and_logs():
    result = verify_evidence(diagnosis(), ['mock-trace-001 payment provider connection refused'], tool_types={'container_health', 'deploy_history', 'query_logs'})
    assert result['escalate'] and result['confidence'] <= .5
