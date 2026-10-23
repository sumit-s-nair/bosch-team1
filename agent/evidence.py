"""Evidence ledger: every fact a tool observes gets an id. The diagnosis may only cite ids
that are in the ledger, so the agent cannot invent evidence."""
import itertools


class Ledger:
    def __init__(self):
        self.items: dict[str, dict] = {}
        self._n = itertools.count(1)

    def add(self, kind: str, detail: str, ref: str | None = None, value=None, anomalous: bool = False) -> str:
        """anomalous=True marks facts that code judged abnormal (thresholds), not the LLM."""
        eid = f"E{next(self._n)}"
        self.items[eid] = {"id": eid, "kind": kind, "ref": ref, "value": value, "detail": detail,
                           "anomalous": anomalous}
        return eid

    def get(self, eid: str):
        return self.items.get(eid)
