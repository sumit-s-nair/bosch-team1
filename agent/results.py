"""Bound JSON without producing broken JSON or escaped partial documents."""
import copy
import json


def serialize(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def bounded(value, budget=3000):
    value = copy.deepcopy(value)
    while len(serialize(value)) > budget:
        containers, strings = [], []
        def walk(node, parent=None, key=None):
            if isinstance(node, list):
                if len(node) > 1:
                    containers.append(node)
                for index, item in enumerate(node):
                    walk(item, node, index)
            elif isinstance(node, dict):
                for name, item in node.items():
                    walk(item, node, name)
            elif isinstance(node, str) and len(node) > 80:
                strings.append((len(node), parent, key))
        walk(value)
        if containers:
            max(containers, key=lambda items: len(serialize(items))).pop()
        elif strings:
            _, parent, key = max(strings, key=lambda item: item[0])
            if parent is None:
                value = value[:max(20, len(value)//2)]
            else:
                parent[key] = parent[key][:max(20, len(parent[key])//2)] + '…'
        else:
            return {'error': 'Tool output exceeded size limit; narrow the query'}
    return value
