"""One folder per incident under runs/, with a timeline of every phase. Everything the agent does is written here."""
import json
import os
import time


class Incident:
    def __init__(self, root="runs"):
        self.id = time.strftime("%Y%m%d-%H%M%S")
        self.dir = os.path.join(root, self.id)
        os.makedirs(self.dir, exist_ok=True)
        self.t0 = time.time()

    def save(self, name, data):
        with open(os.path.join(self.dir, name), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)

    def event(self, phase, msg, **data):
        row = {"t": time.strftime("%H:%M:%S"), "elapsed_s": round(time.time() - self.t0, 1), "phase": phase, "msg": msg, **data}
        with open(os.path.join(self.dir, "timeline.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(row, default=str) + "\n")
        print(f"[{row['t']}] {phase.upper():12} {msg}")
        return row

    def say(self, kind, **data):
        """One entry of the run's conversation (sweep, model tool calls, thinking, human questions and answers)."""
        row = {"t": time.strftime("%H:%M:%S"), "kind": kind, **data}
        with open(os.path.join(self.dir, "conversation.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(row, default=str) + "\n")

    def timeline(self):
        path = os.path.join(self.dir, "timeline.jsonl")
        return [json.loads(l) for l in open(path, encoding="utf-8")] if os.path.exists(path) else []
