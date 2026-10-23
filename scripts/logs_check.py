"""What OTel logs are actually in OpenSearch?  python -m scripts.logs_check [minutes]
Prints counts per service x severity, whether logs carry trace ids, and sample error-ish lines.
Saves runs/logs_check.json.
"""
import json
import os
import re
import sys
from collections import Counter

sys.path.insert(0, ".")
from agent import config
from agent.tools import http, logs

minutes = int(sys.argv[1]) if len(sys.argv) > 1 else 10
svc_f, lvl_f, body_f = logs.fields()
q = {"size": 1000, "sort": [{"@timestamp": "desc"}],
     "query": {"range": {"@timestamp": {"gte": f"now-{minutes}m"}}}}
hits = http.post(f"{config.opensearch_url()}/{logs.index()}/_search", q)["hits"]["hits"]

per = Counter()
with_trace = Counter()
errorish = {}
ERR_WORDS = re.compile(r"error|exception|fail|refused|timeout|unavailable|panic|denied", re.I)
for h in hits:
    s = h["_source"]
    svc = logs.value_at(s, svc_f)
    sev = logs.value_at(s, lvl_f)
    num = logs.value_at(s, "severity.number")
    per[(svc, f"{sev}/{num}")] += 1
    if s.get("traceId") or s.get("traceID") or s.get("trace_id"):
        with_trace[svc] += 1
    body = str(logs.value_at(s, body_f))
    if ERR_WORDS.search(body) and svc not in logs.NOISE_SERVICES:
        errorish.setdefault((svc, f"{sev}/{num}"), body[:200])

print(f"fields: service={svc_f} level={lvl_f} body={body_f}; {len(hits)} docs in last {minutes}m\n")
print("service | severity text/number | count | with trace id")
for (svc, sev), c in sorted(per.items(), key=lambda x: -x[1]):
    print(f"{svc} | {sev} | {c} | {with_trace[svc]}")
print("\nError-looking lines (by service/severity):")
for (svc, sev), b in errorish.items():
    print(f"- {svc} [{sev}]: {b}")
sample_keys = sorted(hits[0]["_source"].keys()) if hits else []
os.makedirs("runs", exist_ok=True)
json.dump({"fields": [svc_f, lvl_f, body_f], "counts": {f"{k[0]}|{k[1]}": v for k, v in per.items()},
           "with_trace": with_trace, "errorish": {f"{k[0]}|{k[1]}": v for k, v in errorish.items()},
           "doc_keys": sample_keys,
           "samples": [h["_source"] for h in hits if logs.value_at(h["_source"], svc_f) not in logs.NOISE_SERVICES][:5]},
          open("runs/logs_check.json", "w"), indent=1, default=str)
print("\nSaved runs/logs_check.json")
