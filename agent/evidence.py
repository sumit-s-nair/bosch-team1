"""Verify quote provenance; these checks do not prove causal correctness."""
import json
import re
from copy import deepcopy


def normalize(value):
    return re.sub(r'\s+', ' ', value).strip().casefold()


def searchable(value):
    """Include decoded string leaves, so log quotes with quotes/newlines match."""
    parts = [value]
    try:
        node = json.loads(value)
    except (ValueError, TypeError):
        return normalize(value)
    def walk(item):
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict):
            for child in item.values():
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)
    walk(node)
    return normalize(' '.join(parts))


def verify_evidence(diag, seen, tool_types=None, records=None):
    diag = deepcopy(diag)
    missing = []
    blob = ' '.join(searchable(item) for item in seen)
    for item in diag['evidence']:
        source, _, identifier = item['source'].partition(':')
        candidates = [text for name, text in (records or []) if name == source.strip()]
        if records is not None:
            haystacks = [searchable(text) for text in candidates]
            verified = any(normalize(item['quote']) in text and (not identifier.strip() or normalize(identifier) in text) for text in haystacks)
        else:
            verified = normalize(item['quote']) in blob
        if not normalize(item['quote']) or not verified:
            missing.append(item)
    diag['unverified_evidence'] = missing
    if missing:
        diag['confidence'] = min(diag['confidence'], .3)
        diag['escalate'] = True
    if not diag['evidence']:
        diag['confidence'] = min(diag['confidence'], .2)
        diag['escalate'] = True
    if tool_types is not None and not {'query_metrics', 'query_traces', 'query_logs'}.issubset(tool_types):
        diag['confidence'] = min(diag['confidence'], .5)
        diag['escalate'] = True
    if diag['confidence'] < .6:
        diag['escalate'] = True
    return diag
