"""Keep feature-flag information away from the model (reading flag settings = disqualification).
Flag data can leak through more than flagd's API: flagd's own spans/logs, client spans that call flagd,
gateway access logs for the flag UI, and log lines that mention flags. Every tool output passes through here."""
import re

FLAG_SERVICES = {"flagd", "flagd-ui"}
FLAG_RE = re.compile(r"flagd|flag|openfeature|ofrep|Resolve(Boolean|String|Int|Float|Object|All)|EventStream", re.I)
FLAG_PATHS = ("/feature", "/flagservice", "/ofrep")
REDACTED = "[redacted: feature-flag reference]"


def is_flag_service(name) -> bool:
    return str(name or "").lower() in FLAG_SERVICES


def is_flag_text(text) -> bool:
    return bool(text) and bool(FLAG_RE.search(str(text)))


def is_flag_span(span: dict) -> bool:
    return is_flag_service(span.get("service")) or is_flag_text(span.get("name"))


def scrub(text):
    """Whole-string redaction: partial edits could still leave the flag name."""
    return REDACTED if is_flag_text(text) else text


def is_flag_path(path) -> bool:
    return str(path or "").lower().startswith(FLAG_PATHS)
