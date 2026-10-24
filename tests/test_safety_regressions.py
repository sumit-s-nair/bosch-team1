"""Recovery, one-shot approvals and kill-switch checks; no live services."""
import threading
from unittest.mock import Mock

import pytest

from agent import detector, server, verify
from agent.tools import metrics, containers, observe
from action_api import server as action_server

ALERT = {'type': 'error_rate', 'service': 'payment'}
HEALTH = {'rps': 2.0, 'err_rate': 0.0, 'p95_ms': 50.0, 'base_rps': 2.0, 'base_err_rate': 0.0, 'base_p95_ms': 50.0}


def test_missing_telemetry_never_recovers(monkeypatch):
    def down(query):
        raise ConnectionError('telemetry down')
    monkeypatch.setattr(metrics, 'promql', down)
    monkeypatch.setattr(containers, 'list_containers', lambda: [])
    result = verify.recovered([ALERT], settle_s=0, poll_s=0)
    assert result['recovered'] is False
    assert all(not poll['telemetry_valid'] for poll in result['polls'])


@pytest.mark.parametrize('health,cs', [({}, [{'name': 'payment'}]), ({'other': HEALTH}, [{'name': 'payment'}]), ({'payment': HEALTH}, []), ({'payment': {**HEALTH, 'rps': 0}}, [{'name':'payment'}])])
def test_missing_required_signal_not_healthy(monkeypatch, health, cs):
    monkeypatch.setattr(observe, 'service_health_raw', lambda **kwargs: health)
    monkeypatch.setattr(containers, 'list_containers', lambda: cs)
    with pytest.raises(ValueError):
        detector.check(strict=True, original_alerts=[ALERT])


def test_original_container_must_still_exist(monkeypatch):
    monkeypatch.setattr(observe, 'service_health_raw', lambda **kwargs: {'checkout': HEALTH})
    monkeypatch.setattr(containers, 'list_containers', lambda: [{'name': 'checkout'}])
    with pytest.raises(ValueError, match='container payment is missing'):
        detector.check(strict=True, original_alerts=[{'type':'container','service':'payment'}])


@pytest.mark.parametrize('responses,expected', [([[],[],[]], True), ([[ALERT],[],[]], False), ([[],[ALERT],[]], False), ([[],[],[ALERT]], False)])
def test_requires_sustained_clean_polls(monkeypatch, responses, expected):
    check = Mock(side_effect=responses)
    monkeypatch.setattr(detector, 'check', check)
    assert verify.recovered([ALERT], settle_s=0, poll_s=0)['recovered'] is expected
    assert check.call_args.kwargs['strict'] is True


def test_valid_request_telemetry_and_containers_recover(monkeypatch):
    monkeypatch.setattr(observe, 'service_health_raw', lambda **kwargs: {'payment': HEALTH})
    monkeypatch.setattr(containers, 'list_containers', lambda: [{'name':'payment','state':'running','status':'Up'}])
    assert verify.recovered([ALERT], settle_s=0, poll_s=0)['recovered']


def test_duplicate_answer_cannot_replace_rejection():
    agent = server.Agent()
    agent.pending = {'id':'question'}
    assert agent.answer('question', 'no')
    assert not agent.answer('question', 'yes')
    assert agent._answer == 'no'


def test_next_question_can_be_answered_after_first():
    agent = server.Agent()
    replies = []
    def ask():
        replies.append(agent.ask('First question'))
        replies.append(agent.ask('Second question'))
    thread = threading.Thread(target=ask, daemon=True)
    thread.start()
    import time
    deadline = time.monotonic()+3
    previous = None
    for answer in ('no', 'name'):
        while time.monotonic() < deadline:
            with agent.lock:
                question = dict(agent.pending) if agent.pending else None
            if question and question['id'] != previous:
                break
            time.sleep(.001)
        else:
            raise AssertionError('No next question')
        previous = question['id']
        assert agent.answer(previous, answer)
    thread.join(3)
    assert replies == ['no', 'name']


def test_kill_engaged_while_waiting_for_execution_lock(tmp_path):
    state = action_server.State('test-token', str(tmp_path/'audit.jsonl'), str(tmp_path/'STOP'))
    waiting = threading.Event()
    release = threading.Event()
    class WaitingLock:
        def __enter__(self):
            waiting.set()
            assert release.wait(3)
        def __exit__(self, *args):
            pass
    state.lock = WaitingLock()
    run = Mock(return_value=({}, None))
    result = []
    worker = threading.Thread(target=lambda: result.append(action_server.execute(state, 'restart_service', {'service':'payment'}, {'approval':{'approved_by':'tester'}}, {'run':run})), daemon=True)
    worker.start()
    assert waiting.wait(3)
    state.kill = True
    release.set()
    worker.join(3)
    assert result[0][0] == 423
    run.assert_not_called()
    assert 'kill switch on' in (tmp_path/'audit.jsonl').read_text()
