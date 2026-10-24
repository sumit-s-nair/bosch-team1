# Incident Zero desktop

Electron window over the agent server (`agent/server.py`). The server serves this UI from `app/` and the JSON API under `/api/`.

```
cd ui/desktop
npm install
npm start
```

If nothing is listening on 127.0.0.1:8000 the app starts `python -m agent.server` from the repo root (it prefers `.venv`; set `PYTHON` to override) and stops it on quit. Start the Action API separately (`scripts/start-action-api.ps1`).

Without Electron, run `python -m agent.server` and open http://127.0.0.1:8000/ in a browser.
