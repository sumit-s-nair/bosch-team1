"""Read-only discovery of telemetry names and mappings, never flag configuration."""
import json
from pathlib import Path
from agent.tools import metrics, traces, logs


def discover():
    result = {}
    for name, fn in [('metric_names', lambda: [n for n in metrics.metric_names() if 'span' in n or 'calls' in n or 'duration' in n][:150]), ('span_metrics', metrics.settings), ('trace_services', traces.services), ('log_fields', lambda: sorted(logs.field_caps())[:200]), ('resolved_log_fields', logs.fields)]:
        try:
            result[name] = fn()
        except Exception as exc:
            result[name] = {'error': str(exc)[:500]}
    return result


def main():
    result = discover()
    path = Path('runs/discovery.json')
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
    print(f'Saved {path}')
    return int(any(isinstance(item, dict) and 'error' in item for item in result.values()))


if __name__ == '__main__':
    raise SystemExit(main())
