"""JSON-schema check of the model's diagnosis (structure only; evidence is checked against the ledger)."""
import json
from pathlib import Path
from jsonschema import Draft202012Validator

SCHEMA = json.loads(Path(__file__).with_name("diagnosis.schema.json").read_text())
_VALIDATOR = Draft202012Validator(SCHEMA)


def validate_output(d):
    errors = sorted(f"{'/'.join(map(str, e.path)) or '(root)'}: {e.message}" for e in _VALIDATOR.iter_errors(d))
    return not errors, errors
