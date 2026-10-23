"""Scripted model + synthetic telemetry demo; no network, Docker or API key."""
import argparse
import json
import os
from types import SimpleNamespace as NS
from agent import mock_tools
from agent.loop import run_agent


class ScriptedClient:
    """Demonstrates plumbing only, not actual model reasoning."""
    def __init__(self, mode='default'):
        self.mode = mode
        self.chat = NS(completions=self)
        self.step = 0

    def create(self, **kwargs):
        actions = [('query_metrics', {'service': 'payment'}), ('query_traces', {'service': 'payment'}), ('query_logs', {'service': 'payment'})]
        if self.step < len(actions):
            name, args = actions[self.step]
        else:
            name = 'submit_diagnosis'
            args = {'root_cause_component': 'payment', 'root_cause': 'Payment provider connection refused', 'confidence': .9, 'evidence': [{'source': 'query_traces:mock-trace-001', 'quote': 'mock-trace-001'}, {'source': 'query_logs', 'quote': 'payment provider connection refused'}], 'escalate': False}
            if self.mode == 'empty':
                args.update(root_cause_component='unknown', root_cause='Insufficient telemetry to determine the cause', confidence=0, evidence=[], escalate=True)
            elif self.mode == 'fabricated':
                args['evidence'] = [{'source': 'query_logs', 'quote': 'This log was never retrieved'}]
        self.step += 1
        return NS(choices=[NS(message=NS(content=None, tool_calls=[NS(id=f'mock-{self.step}', function=NS(name=name, arguments=json.dumps(args)))]))])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['default', 'empty', 'fabricated'], default='default')
    args = parser.parse_args()
    os.environ['MOCK_MODE'] = 'empty' if args.mode == 'empty' else 'default'
    print('SCRIPTED DEMO: model responses and telemetry are synthetic.')
    print(json.dumps(run_agent('High error rate on payment', client=ScriptedClient(args.mode), tools_module=mock_tools), indent=2))


if __name__ == '__main__':
    main()
