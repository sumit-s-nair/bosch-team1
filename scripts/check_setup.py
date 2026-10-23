"""Read-only integration check: python -m scripts.check_setup [--skip-llm]."""
import argparse
import json
from agent import config
from agent.tools import containers, logs, metrics, traces
from agent.loop import TOOLS


def c_containers():
    rows = containers.list_containers()
    if not rows:
        raise RuntimeError('No containers on configured demo network')
    bad = [row['name'] for row in rows if row['state'] != 'running']
    return {'count': len(rows), 'not_running': bad}


def c_prom():
    return metrics.settings()


def c_jaeger():
    names = traces.services()
    if not names:
        raise RuntimeError('No trace services; wait for load and telemetry')
    return {'services': names}


def c_logs():
    fields = logs.fields()
    hits = logs.search_logs(minutes=15, size=1)
    if not hits:
        raise RuntimeError('No recent logs; check load, ingestion and LOGS_INDEX')
    return {'fields': fields, 'recent_documents': len(hits)}


def c_llm():
    settings = config.llm_settings()
    response = config.llm_client().chat.completions.create(model=settings['model'], messages=[{'role': 'user', 'content': 'For this connection test, call container_health with no arguments. Do not answer in prose.'}], tools=TOOLS, tool_choice='auto', max_tokens=256)
    calls = response.choices[0].message.tool_calls or []
    if not any(call.function.name == 'container_health' and json.loads(call.function.arguments) == {} for call in calls):
        raise RuntimeError('Model answered without the requested tool call; verify tool-calling support and context size')
    return {'model': settings['model'], 'tool_calling': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--skip-llm', action='store_true')
    args = parser.parse_args()
    checks = [('Containers', c_containers), ('Prometheus', c_prom), ('Jaeger', c_jaeger), ('OpenSearch', c_logs)]
    if not args.skip_llm:
        checks.append(('LLM tool calling', c_llm))
    success = True
    for name, fn in checks:
        try:
            print(f'[ OK ] {name}: {json.dumps(fn())}')
        except Exception as exc:
            success = False
            print(f'[FAIL] {name}: {type(exc).__name__}: {exc}')
    print('ALL GOOD' if success else 'Some checks failed; correct these before live investigation')
    return 0 if success else 1


if __name__ == '__main__':
    raise SystemExit(main())
