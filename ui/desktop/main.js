// Electron shell for the agent server. Uses a running `python -m agent.server` if there is one,
// otherwise starts it from the repo root and stops it again on quit.
const { app, BrowserWindow, shell } = require("electron");
const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");
const http = require("http");

const PORT = process.env.AGENT_SERVER_PORT || "8000";
const URL = `http://127.0.0.1:${PORT}/`;
const REPO = path.resolve(__dirname, "..", "..");
let server = null;

function python() {
  if (process.env.PYTHON) return process.env.PYTHON;
  const win = path.join(REPO, ".venv", "Scripts", "python.exe");
  const nix = path.join(REPO, ".venv", "bin", "python");
  if (fs.existsSync(win)) return win;
  if (fs.existsSync(nix)) return nix;
  return process.platform === "win32" ? "python" : "python3";
}

function up() {
  return new Promise((resolve) => {
    const req = http.get(`${URL}api/health`, (res) => { res.resume(); resolve(res.statusCode === 200); });
    req.on("error", () => resolve(false));
    req.setTimeout(1500, () => { req.destroy(); resolve(false); });
  });
}

async function ensureServer() {
  if (await up()) return true;
  server = spawn(python(), ["-m", "agent.server"], { cwd: REPO, env: process.env, stdio: "inherit" });
  server.on("exit", () => { server = null; });
  for (let i = 0; i < 40; i++) {
    await new Promise((r) => setTimeout(r, 500));
    if (await up()) return true;
  }
  return false;
}

async function createWindow() {
  const win = new BrowserWindow({
    width: 1280, height: 820, minWidth: 900, minHeight: 600,
    backgroundColor: "#f2f2f2", title: "Incident Zero",
    autoHideMenuBar: true,
    webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true },
  });
  win.webContents.setWindowOpenHandler(({ url }) => { shell.openExternal(url); return { action: "deny" }; });
  if (await ensureServer()) {
    win.loadURL(URL);
  } else {
    win.loadURL("data:text/html," + encodeURIComponent(
      `<body style="font:14px sans-serif;padding:32px;color:#333;background:#f2f2f2">
       <h3>Agent server did not start</h3>
       <p>Run <code>python -m agent.server</code> from the repo root, then reopen this window.</p></body>`));
  }
}

app.whenReady().then(createWindow);
app.on("window-all-closed", () => app.quit());
app.on("before-quit", () => { if (server) server.kill(); });
