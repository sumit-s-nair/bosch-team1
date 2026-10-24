# Start the Action API (the only component allowed to change the demo). Run from the repo root, in its own terminal.
#   .\scripts\start-action-api.ps1
# Kill switch: create a file named STOP in the repo root, or POST /kill {"on": true}
if (Test-Path .venv\Scripts\Activate.ps1) { . .\.venv\Scripts\Activate.ps1 }
python -m action_api.server
