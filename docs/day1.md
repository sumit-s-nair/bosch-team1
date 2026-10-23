# Day 1 checkpoint

See [the README](../README.md) for the maintained setup, offline rehearsal, telemetry discovery and live checkpoint commands. See [architecture](architecture.md) for the system sketch.

Minimum handoff checks:

1. `python -m pytest -q`
2. `python -m scripts.mock_demo` and `python -m scripts.mock_demo --mode empty`
3. With demo and model running, `python -m scripts.discover` and `python -m scripts.check_setup`
4. `python main.py "<practice incident alert>" --output runs/checkpoint/diagnosis.json`
5. Compare cause and cited quotes against full tool results in the saved artifact. Align the placeholder schema with the organizer schema before submission.
