"""Print actual discovered metric names and raw outputs of the two extended tools."""
import argparse
import json
from pathlib import Path
from agent import metric_details, observe
from agent.evidence import Ledger


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--service', help='Exact service name; otherwise discover a service with runtime metrics')
    parser.add_argument('--output', type=Path, default=Path('runs/runtime-validation'))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    try:
        names = metric_details.names()
    except Exception as exc:
        print(json.dumps({'error': f'Live metric discovery failed: {exc}'}, indent=2))
        return 1
    (args.output/'metric-names.json').write_text(json.dumps(names, indent=2))
    print('DISCOVERED METRIC NAMES:')
    print(json.dumps(names, indent=2))
    service = args.service
    if not service:
        for name in names:
            if name.startswith(metric_details.RUNTIME_PREFIXES):
                try:
                    rows = metric_details.series(name)
                except Exception as exc:
                    print(json.dumps({'error': f'Runtime service discovery failed: {exc}'}))
                    return 1
                service = next((row[label] for row in rows for label in metric_details.SERVICE_LABELS if row.get(label)), None)
                if service:
                    break
    runtime = observe.resource_usage(Ledger(), service=service)
    routes = observe.service_overview(Ledger(), service=service, group_by='route')
    for name, value in [('resource-usage', runtime), ('route-overview', routes)]:
        print(f'RAW {name} (service={service!r}):')
        print(json.dumps(value, indent=2))
        (args.output/f'{name}.json').write_text(json.dumps(value, indent=2))
    print(f'Saved raw outputs and all discovered names to {args.output}')
    return int('error' in runtime or 'error' in routes)


if __name__ == '__main__':
    raise SystemExit(main())
