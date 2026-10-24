"""Team 1 Action API: the ONLY component allowed to change the running demo.

The agent never touches Docker, the load generator or feature flags directly. It sends a request here;
this service checks it against the allowlist, the human-approval record, the kill switch and rate limits,
executes it, and writes every call (including refusals) to the audit log.
"""
