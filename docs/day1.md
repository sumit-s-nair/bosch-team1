# Day 1 checkpoint runbook

1. `python -m pytest -q` — offline tests pass
2. `python -m scripts.check_setup` — all [ OK ], including LLM tool calling
3. `python -m scripts.eyes` — tools return sensible data on a healthy system
4. Inject a practice incident (faultctl), wait ~2 min, then `python -m agent.run --now`
5. Check `runs/<time>/diagnosis.json`: root cause, cited evidence (each with the observed fact), decision

See [architecture](architecture.md) for the system sketch.
