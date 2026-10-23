"""TODO-ALIGN: replace diagnosis.schema.json with the organizer schema."""
import json
from pathlib import Path
from jsonschema import Draft202012Validator
SCHEMA = json.loads(Path(__file__).with_name("diagnosis.schema.json").read_text())
def validate_output(d):
    errors = sorted((e.message for e in Draft202012Validator(SCHEMA).iter_errors(d)))
    return not errors, errors
