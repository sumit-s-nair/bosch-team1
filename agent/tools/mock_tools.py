"""Offline fixtures for the two extended tools; no incident logic in live tools."""
import os
from agent.evidence import Ledger


def resource_usage(L=None, service=None, window='10m'):
    L = L or Ledger()
    service = service or 'example-service'
    if os.getenv('MOCK_MODE') in ('empty', 'no_runtime'):
        return {'summary': 'No resource samples', 'runtime': {'runtime_kind': 'unknown', 'notes': [f'no runtime metrics found for {service}']}}
    runtime = {'runtime_kind': 'jvm', 'available_runtime_metrics': ['jvm_gc_duration_seconds_sum', 'jvm_memory_used_bytes', 'process_cpu_utilization_ratio'], 'gc': {'metric': 'jvm_gc_duration_seconds_sum', 'unit': 'seconds_per_second', 'now': .18, 'baseline': .02}, 'heap': {'metric': 'jvm_memory_used_bytes', 'unit': 'bytes', 'now': 157286400, 'start': 104857600, 'growth': 52428800, 'growth_pct': 50.0}, 'process_cpu': {'metric': 'process_cpu_utilization_ratio', 'unit': 'ratio', 'now': .82, 'baseline': .2}, 'memory_growing': True, 'notes': []}
    for kind in ('gc', 'heap', 'process_cpu'):
        runtime[kind]['evidence_id'] = L.add('metric', f'{service} {kind}: {runtime[kind]}', ref=runtime[kind]['metric'])
    return {'summary': 'Synthetic container CPU=82%, memory=60%', 'runtime': runtime}


def service_overview(L=None, window='5m', service=None, group_by=None):
    L = L or Ledger()
    service = service or 'example-service'
    if group_by == 'client' or os.getenv('MOCK_MODE') == 'empty':
        return {'group_by': group_by, 'items': [], 'notes': [f'no {group_by} label found in span call metrics']}
    if group_by == 'route':
        item = {'service': service, 'route': '/products', 'rate_per_s': 40.0, 'baseline_rate_per_s': 10.0, 'ratio': 4.0, 'error_rate_per_s': 1.0, 'spike': True}
        item['evidence_id'] = L.add('metric', f'{service} /products: 40 rps vs baseline 10 rps', ref='mock-span-calls:/products')
        return {'window': window, 'group_by': 'route', 'items': [item], 'notes': []}
    eid = L.add('metric', f'{service}: rps=40 error=2.5% p95=100ms', ref='mock-span-calls')
    return f'{service} | rps=40 | error=2.5% | p95=100ms [{eid}]'
