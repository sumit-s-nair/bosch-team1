'use client'

// Live on-call console. All data comes from the agent server (agent/server.py), which reads runs/<id>/.
// The browser never calls the Action API: approvals are answers to the agent's own questions (POST /api/answer),
// and the kill switch is proxied by the agent server, which holds the token.

import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Activity,
  AlertTriangle,
  Archive,
  Ban,
  Check,
  ChevronRight,
  CircleDot,
  Clock3,
  Download,
  Eye,
  Gauge,
  HeartPulse,
  Info,
  LockKeyhole,
  Menu,
  Play,
  Radar,
  RefreshCw,
  Search,
  Send,
  Server,
  Settings2,
  ShieldCheck,
  Siren,
  TerminalSquare,
  TriangleAlert,
  X,
  Zap,
} from 'lucide-react'

const AGENT_URL = (process.env.NEXT_PUBLIC_AGENT_URL || 'http://127.0.0.1:8000').replace(/\/$/, '')
const POLL_MS = 2000
const ACT_CONFIDENCE = 0.7 // same threshold as agent/brain.py: below it the agent escalates
const FINAL = ['Resolved', 'Escalated', 'Rejected', 'No incident', 'Dry run', 'Rolled back', 'Interrupted']

type Summary = {
  id: string; status: string; active: boolean; alert_type?: string; service?: string; title: string; stage: string
  confidence?: number | null; component?: string; category?: string; cause?: string; decision?: string; outcome?: string
}
type Evidence = { id: string; observed: string; why: string; kind?: string; ref?: string; anomalous?: boolean }
type LedgerItem = { id: string; kind: string; ref?: string; detail: string; anomalous: boolean; value?: any }
type Detail = Summary & {
  alerts: any[]
  diagnosis: null | { summary?: string; confidence?: number; decision?: string; evidence?: Evidence[]; ledger?: LedgerItem[]
    tool_trail?: { tool: string; args: Record<string, unknown>; by: string }[]; validation_notes?: string[]
    root_cause?: { service?: string; category?: string; description?: string } }
  plan: null | { action: string; params: Record<string, unknown>; reason: string; rollback: string; dry_run_preview?: string }
  approval: null | { approved?: boolean; by?: string | null; reason?: string }
  validation: null | { recovered: boolean; polls: { poll: number; still_firing: any[]; new_alerts: any[] }[] }
  rollback: null | { status?: string; why?: string }
  report: any; report_md: string | null; timeline: { t: string; phase: string; msg: string }[]
}
type Pending = { id: string; run_id: string; prompt: string; kind: string; asked_at: string } | null
type Health = { ok: boolean; agent_running: boolean; run_id: string | null; mode: string | null; last_error: string | null
  pending: boolean; model: string; action_api: { configured: boolean; reachable: boolean; kill_switch: boolean | null } }

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`${AGENT_URL}${path}`, { cache: 'no-store', ...init,
    headers: init?.body ? { 'Content-Type': 'application/json' } : undefined })
  const body = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(body.error || `HTTP ${r.status}`)
  return body as T
}
const post = <T,>(path: string, body: unknown) => api<T>(path, { method: 'POST', body: JSON.stringify(body) })

// No severity in the agent's data: derived from the alert type only (shown with that explanation).
function severity(alertType?: string): 'SEV-1' | 'SEV-2' | 'SEV-3' {
  if (alertType === 'error_rate' || alertType === 'container') return 'SEV-1'
  if (alertType === 'latency' || alertType === 'traffic_drop' || alertType === 'traffic_spike') return 'SEV-2'
  return 'SEV-3'
}
function statusTone(status: string): 'red' | 'amber' | 'green' | 'cyan' | 'slate' {
  if (status === 'Resolved' || status === 'No incident') return 'green'
  if (status === 'Awaiting approval' || status === 'Acting' || status === 'Verifying') return 'amber'
  if (status === 'Escalated' || status === 'Rejected' || status === 'Rolled back') return 'red'
  if (status === 'Investigating' || status === 'Watching') return 'cyan'
  return 'slate'
}
function startedAt(id: string) { // run ids are YYYYMMDD-HHMMSS (agent local time)
  return `${id.slice(0, 4)}-${id.slice(4, 6)}-${id.slice(6, 8)} ${id.slice(9, 11)}:${id.slice(11, 13)}:${id.slice(13, 15)}`
}
const pct = (x?: number | null) => (x === null || x === undefined ? 'n/a' : `${Math.round(x * 100)}%`)

function cn(...classes: (string | false | null | undefined)[]) {
  return classes.filter(Boolean).join(' ')
}

function Badge({ children, tone = 'slate', title }: { children: React.ReactNode; tone?: 'red' | 'amber' | 'green' | 'cyan' | 'slate'; title?: string }) {
  return <span title={title} className={cn('inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-[11px] font-semibold uppercase tracking-[0.12em]', tone === 'red' && 'border-red-400/25 bg-red-400/10 text-red-300', tone === 'amber' && 'border-amber-300/25 bg-amber-300/10 text-amber-200', tone === 'green' && 'border-emerald-300/25 bg-emerald-300/10 text-emerald-300', tone === 'cyan' && 'border-cyan-300/25 bg-cyan-300/10 text-cyan-200', tone === 'slate' && 'border-slate-700 bg-slate-800/70 text-slate-300')}>{children}</span>
}

function MetricCard({ label, value, hint, icon: Icon, tone = 'cyan' }: { label: string; value: string; hint: string; icon: React.ElementType; tone?: 'cyan' | 'red' | 'green' }) {
  return <div className="rounded-xl border border-slate-800/90 bg-[#101b2b] p-4 shadow-[0_12px_30px_rgba(0,0,0,.12)]">
    <div className="flex items-start justify-between"><span className="text-xs font-medium text-slate-400">{label}</span><Icon className={cn('size-4', tone === 'red' && 'text-red-300', tone === 'green' && 'text-emerald-300', tone === 'cyan' && 'text-cyan-300')} /></div>
    <div className="mt-3 flex items-end justify-between"><strong className="text-2xl font-semibold tracking-tight text-slate-100">{value}</strong><span className={cn('text-[11px]', tone === 'red' ? 'text-red-300' : tone === 'green' ? 'text-emerald-300' : 'text-cyan-300')}>{hint}</span></div>
  </div>
}

function Card({ title, subtitle, right, children, className }: { title: string; subtitle?: string; right?: React.ReactNode; children: React.ReactNode; className?: string }) {
  return <div className={cn('rounded-xl border border-slate-800/90 bg-[#101b2b]', className)}>
    <div className="flex items-center justify-between gap-3 border-b border-slate-800/90 p-4"><div><h2 className="text-sm font-semibold text-slate-100">{title}</h2>{subtitle && <p className="mt-1 text-xs text-slate-500">{subtitle}</p>}</div>{right}</div>
    <div className="p-4">{children}</div>
  </div>
}

// The agent asks several kinds of questions (approve, a value only a human may name, approver name, Action API down,
// rollback, acknowledge an escalation). The panel is generic: it shows the agent's exact prompt and sends the answer
// as if typed in the agent's terminal.
function PendingQuestion({ pending, onAnswer, busy }: { pending: NonNullable<Pending>; onAnswer: (answer: string) => void; busy: boolean }) {
  const [text, setText] = useState('')
  useEffect(() => setText(''), [pending.id])
  const yesNo = pending.kind === 'approve' || pending.kind === 'rollback'
  const reject = () => onAnswer(text.trim() && text.trim().toLowerCase() !== 'yes' ? `no: ${text.trim()}` : 'no')
  return <div className="rounded-xl border border-amber-300/30 bg-amber-300/[0.06] p-4">
    <div className="flex items-center justify-between"><div className="flex items-center gap-2 text-xs font-semibold text-amber-200"><LockKeyhole className="size-3.5" />The agent is waiting for you</div><span className="font-mono text-[10px] text-slate-500">asked {pending.asked_at}</span></div>
    <p className="mt-3 whitespace-pre-wrap font-mono text-[12px] leading-5 text-slate-200">{pending.prompt}</p>
    <input value={text} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && !yesNo) onAnswer(text) }}
      placeholder={yesNo ? 'optional note if you reject' : pending.kind === 'acknowledge' || pending.kind === 'action_api_down' ? 'leave empty = press Enter' : 'your answer'}
      className="mt-3 h-9 w-full rounded-md border border-slate-700 bg-[#0b1727] px-3 text-xs text-slate-200 outline-none placeholder:text-slate-600 focus:border-cyan-300/50" />
    {yesNo
      ? <div className="mt-3 grid grid-cols-2 gap-2"><button disabled={busy} onClick={() => onAnswer('yes')} className="flex items-center justify-center gap-2 rounded-lg bg-cyan-300 py-2 text-xs font-semibold text-[#081321] hover:bg-cyan-200 disabled:opacity-50"><Check className="size-3.5" />{pending.kind === 'rollback' ? 'Yes, roll back' : 'Approve action'}</button><button disabled={busy} onClick={reject} className="flex items-center justify-center gap-2 rounded-lg border border-red-300/25 bg-red-300/10 py-2 text-xs font-semibold text-red-200 hover:bg-red-300/15 disabled:opacity-50"><Ban className="size-3.5" />Reject</button></div>
      : <button disabled={busy} onClick={() => onAnswer(text)} className="mt-3 flex w-full items-center justify-center gap-2 rounded-lg bg-cyan-300 py-2 text-xs font-semibold text-[#081321] hover:bg-cyan-200 disabled:opacity-50"><Send className="size-3.5" />Send answer</button>}
    <div className="mt-3 flex items-start gap-2 text-[10px] leading-4 text-slate-500"><Info className="mt-0.5 size-3 shrink-0" />Your answer goes to the agent, which records the approval, calls the Action API, verifies recovery and offers rollback. Nothing is executed from this page.</div>
  </div>
}

export function IncidentZeroConsole() {
  const [activeNav, setActiveNav] = useState('Overview')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState('All')
  const [mobileNav, setMobileNav] = useState(false)
  const [list, setList] = useState<Summary[]>([])
  const [detail, setDetail] = useState<Detail | null>(null)
  const [pending, setPending] = useState<Pending>(null)
  const [health, setHealth] = useState<Health | null>(null)
  const [connected, setConnected] = useState<boolean | null>(null)
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)
  const [now, setNow] = useState<Date | null>(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [showPreview, setShowPreview] = useState(false)

  const refresh = useCallback(async () => {
    try {
      const [h, inc, p] = await Promise.all([api<Health>('/api/health'), api<{ incidents: Summary[] }>('/api/incidents'), api<{ pending: Pending }>('/api/pending')])
      setHealth(h); setList(inc.incidents); setPending(p.pending); setConnected(true); setLastUpdated(new Date())
      const id = selectedId ?? p.pending?.run_id ?? inc.incidents[0]?.id ?? null
      if (id !== selectedId) setSelectedId(id)
      setDetail(id ? await api<Detail>(`/api/incidents/${id}`) : null)
    } catch {
      setConnected(false)
    }
  }, [selectedId])

  useEffect(() => { refresh(); const t = setInterval(refresh, POLL_MS); return () => clearInterval(t) }, [refresh])
  useEffect(() => { setNow(new Date()); const t = setInterval(() => setNow(new Date()), 1000); return () => clearInterval(t) }, [])
  useEffect(() => setShowPreview(false), [selectedId])

  const act = async (fn: () => Promise<unknown>, ok?: string) => {
    setBusy(true); setMessage(null)
    try { await fn(); if (ok) setMessage(ok) } catch (e) { setMessage(e instanceof Error ? e.message : String(e)) }
    setBusy(false); refresh()
  }
  const answer = (text: string) => pending && act(() => post('/api/answer', { id: pending.id, answer: text }))
  const startRun = (mode: 'now' | 'watch') => act(() => post('/api/run', { mode }), mode === 'now' ? 'Investigation started' : 'Watching for alerts')
  // Only the Action API's answer changes the toggle: the next health poll shows the confirmed state.
  const toggleKill = () => act(async () => {
    const r = await post<{ kill_switch: boolean | null }>('/api/kill', { on: !health?.action_api.kill_switch, by: 'dashboard' })
    setHealth((h) => h && { ...h, action_api: { ...h.action_api, kill_switch: r.kill_switch } })
  })

  const statuses = useMemo(() => ['All', ...Array.from(new Set(list.map((i) => i.status)))], [list])
  const filtered = useMemo(() => list.filter((i) => {
    const q = `${i.id} ${i.service ?? ''} ${i.title} ${i.component ?? ''}`.toLowerCase().includes(query.toLowerCase())
    return q && (statusFilter === 'All' || i.status === statusFilter)
  }), [list, query, statusFilter])

  const open = list.filter((i) => !FINAL.includes(i.status)).length
  const diag = detail?.diagnosis
  const confidence = diag?.confidence ?? null
  const escalated = diag?.decision === 'escalate'
  const myPending = pending && detail && pending.run_id === detail.id ? pending : null
  // service-level rows from the first sweep (service_overview): ref "span_metrics:<service>"
  const services = useMemo(() => (diag?.ledger ?? []).filter((e) => e.kind === 'metric' && /^span_metrics:[^:]+$/.test(e.ref ?? '') && e.value && 'err_rate' in e.value)
    .sort((a, b) => (b.value.err_rate ?? 0) - (a.value.err_rate ?? 0)), [diag])
  const abnormal = services.filter((s) => s.anomalous).length
  const dropped = (diag?.validation_notes ?? []).filter((n) => n.startsWith('dropped invented evidence')).length
  const killOn = health?.action_api.kill_switch
  const api_ = health?.action_api

  const exportReport = () => {
    if (!detail) return
    const blob = new Blob([JSON.stringify(detail.report ?? detail.diagnosis, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `incident-${detail.id}-${detail.report ? 'report' : 'diagnosis'}.json`
    a.click()
    URL.revokeObjectURL(url)
  }

  const navItems = [
    { label: 'Overview', icon: Activity },
    { label: 'Incidents', icon: Siren, count: open },
    { label: 'Approvals', icon: ShieldCheck, count: pending ? 1 : 0 },
    { label: 'Reports', icon: Archive },
  ]

  return <main className="min-h-screen bg-[#091321] text-slate-100 selection:bg-cyan-300/20">
    <div className="flex min-h-screen">
      <aside className={cn('fixed inset-y-0 left-0 z-30 flex w-[246px] flex-col border-r border-slate-800/80 bg-[#0b1727] transition-transform lg:static lg:translate-x-0', mobileNav ? 'translate-x-0' : '-translate-x-full')}>
        <div className="flex h-[72px] items-center gap-3 border-b border-slate-800/80 px-5"><div className="grid size-9 place-items-center rounded-lg bg-cyan-300 text-[#071321]"><Zap className="size-5 fill-current" /></div><div><div className="text-sm font-semibold tracking-wide text-white">incident<span className="text-cyan-300">zero</span></div><div className="text-[10px] uppercase tracking-[0.16em] text-slate-500">on-call console</div></div></div>
        <div className="px-4 pt-6"><div className="mb-3 px-2 text-[10px] font-semibold uppercase tracking-[0.18em] text-slate-500">Workspace</div>{navItems.map(({ label, icon: Icon, count }) => <button key={label} onClick={() => { setActiveNav(label); setMobileNav(false) }} className={cn('mb-1 flex w-full items-center justify-between rounded-lg px-3 py-2.5 text-left text-sm transition-colors', activeNav === label ? 'bg-cyan-300/10 text-cyan-200' : 'text-slate-400 hover:bg-slate-800/60 hover:text-slate-200')}><span className="flex items-center gap-3"><Icon className="size-4" />{label}</span>{!!count && <span className="rounded-md bg-red-400/15 px-1.5 py-0.5 text-[10px] font-semibold text-red-300">{count}</span>}</button>)}</div>
        <div className="mt-7 px-4"><div className="mb-3 px-2 text-[10px] font-semibold uppercase tracking-[0.18em] text-slate-500">System</div><button onClick={() => setActiveNav('Settings')} className={cn('mb-1 flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-left text-sm', activeNav === 'Settings' ? 'bg-cyan-300/10 text-cyan-200' : 'text-slate-400 hover:bg-slate-800/60 hover:text-slate-200')}><Settings2 className="size-4" />Settings</button><button onClick={() => setActiveNav('System status')} className="flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-left text-sm text-slate-400 hover:bg-slate-800/60 hover:text-slate-200"><Server className="size-4" />System status</button></div>
        <div className="mt-auto border-t border-slate-800/80 p-4">
          <div className={cn('rounded-lg border p-3', connected ? 'border-emerald-300/20 bg-emerald-300/5' : 'border-red-300/20 bg-red-300/5')}>
            <div className={cn('flex items-center gap-2 text-xs font-medium', connected ? 'text-emerald-300' : 'text-red-300')}><span className={cn('size-1.5 rounded-full', connected ? 'bg-emerald-300' : 'bg-red-400')} />{connected === null ? 'Connecting…' : connected ? (health?.agent_running ? `Agent running (${health.mode})` : 'Agent server idle') : 'Agent server unreachable'}</div>
            <div className="mt-2 flex items-center justify-between text-[11px] text-slate-500"><span>{health?.model ?? '—'} · Ollama</span><span className="truncate pl-2 font-mono text-[10px]">{AGENT_URL.replace(/^https?:\/\//, '')}</span></div>
          </div>
          <div className="mt-3 text-center text-[10px] leading-4 text-slate-600">Telemetry tools are read-only · changes only via the Action API after approval</div>
        </div>
      </aside>
      {mobileNav && <button aria-label="Close navigation" onClick={() => setMobileNav(false)} className="fixed inset-0 z-20 bg-black/60 lg:hidden" />}
      <section className="min-w-0 flex-1">
        <header className="flex h-[72px] items-center justify-between border-b border-slate-800/80 bg-[#0b1727]/90 px-4 sm:px-6">
          <div className="flex items-center gap-3"><button className="rounded-lg p-2 text-slate-400 hover:bg-slate-800 lg:hidden" onClick={() => setMobileNav(true)} aria-label="Open navigation"><Menu className="size-5" /></button><div><div className="text-xs text-slate-500">Operations / {activeNav}</div><div className="mt-0.5 text-sm font-medium text-slate-200" suppressHydrationWarning>{now ? now.toLocaleDateString(undefined, { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' }) : '—'} <span className="mx-2 text-slate-700">·</span><span className="text-slate-500">{now ? now.toLocaleTimeString() : ''}</span></div></div></div>
          <div className="flex items-center gap-2 sm:gap-3">
            <button disabled={busy || !connected || health?.agent_running} onClick={() => startRun('now')} className="hidden items-center gap-1.5 rounded-md border border-cyan-300/25 bg-cyan-300/10 px-2.5 py-1.5 text-[11px] font-semibold text-cyan-200 hover:bg-cyan-300/15 disabled:opacity-40 sm:flex"><Play className="size-3" />Investigate now</button>
            <button disabled={busy || !connected || health?.agent_running} onClick={() => startRun('watch')} className="hidden items-center gap-1.5 rounded-md border border-slate-700 px-2.5 py-1.5 text-[11px] font-medium text-slate-300 hover:bg-slate-800 disabled:opacity-40 sm:flex"><Radar className="size-3" />Watch</button>
            <div className={cn('hidden items-center gap-2 rounded-md border px-2.5 py-1.5 text-[11px] md:flex', connected ? 'border-slate-800 bg-slate-900/40 text-slate-400' : 'border-red-300/25 bg-red-300/5 text-red-300')}><span className={cn('size-1.5 rounded-full', connected ? 'bg-emerald-300' : 'bg-red-400')} />{connected ? 'Backend connected' : connected === null ? 'Connecting…' : 'Backend unreachable'}</div>
            <button onClick={refresh} className="rounded-lg border border-slate-800 p-2 text-slate-400 hover:border-slate-700 hover:text-slate-200" aria-label="Refresh data"><RefreshCw className="size-4" /></button>
          </div>
        </header>
        <div className="mx-auto max-w-[1500px] p-4 sm:p-6 lg:p-8">
          <div className="mb-7 flex flex-col justify-between gap-4 lg:flex-row lg:items-end">
            <div>{(pending || open > 0) && <div className="mb-2 flex items-center gap-2"><span className="size-2 rounded-full bg-red-400 shadow-[0_0_10px_#f87171]" /><span className="text-xs font-semibold uppercase tracking-[0.18em] text-red-300">{pending ? 'Approval required' : 'Run in progress'}</span></div>}<h1 className="text-2xl font-semibold tracking-tight text-white sm:text-[30px]">Operations overview</h1><p className="mt-1 text-sm text-slate-400">{connected === false ? `Cannot reach the agent server at ${AGENT_URL}. Start it with: python -m agent.server` : health?.agent_running ? `The agent is ${pending ? 'waiting for your answer' : health.mode === 'watch' ? 'watching / investigating' : 'investigating'} (run ${health.run_id}).` : list.length ? 'The agent is idle. Start an investigation or watch for alerts.' : 'No runs yet. Start an investigation or watch for alerts.'}</p>{health?.last_error && <p className="mt-1 text-xs text-red-300">Last run failed: {health.last_error}</p>}{message && <p className="mt-1 text-xs text-amber-200">{message}</p>}</div>
            <div className="flex items-center gap-2"><Badge tone="slate"><Clock3 className="size-3" />{lastUpdated ? `Updated ${lastUpdated.toLocaleTimeString()}` : 'Not updated yet'}</Badge><Badge tone={connected ? 'cyan' : 'red'}>{connected ? 'Live · runs/' : 'Offline'}</Badge></div>
          </div>
          <div className="mb-7 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <MetricCard label="Open runs" value={String(open)} hint={pending ? '1 awaiting approval' : 'none awaiting approval'} icon={Siren} tone={open ? 'red' : 'green'} />
            <MetricCard label="Abnormal services (last sweep)" value={services.length ? `${abnormal} / ${services.length}` : 'n/a'} hint={detail ? `run ${detail.id}` : 'no run selected'} icon={HeartPulse} tone={abnormal ? 'red' : 'green'} />
            <MetricCard label="Alert" value={detail?.alert_type ?? 'n/a'} hint={detail?.service ?? ''} icon={TriangleAlert} tone={detail?.alert_type && detail.alert_type !== 'manual' ? 'red' : 'cyan'} />
            <MetricCard label="Agent confidence" value={pct(confidence)} hint={confidence === null ? 'no diagnosis yet' : confidence >= ACT_CONFIDENCE ? 'can propose a fix' : 'below 0.7: escalates'} icon={Gauge} tone={confidence !== null && confidence < ACT_CONFIDENCE ? 'red' : 'cyan'} />
          </div>
          <div className="grid gap-6 xl:grid-cols-[minmax(0,1.5fr)_minmax(360px,.9fr)]">
            <div className="min-w-0 space-y-6">
              <div className="overflow-hidden rounded-xl border border-slate-800/90 bg-[#101b2b]">
                <div className="flex flex-col gap-3 border-b border-slate-800/90 p-4 sm:flex-row sm:items-center sm:justify-between"><div><h2 className="text-sm font-semibold text-slate-100">Runs</h2><p className="mt-1 text-xs text-slate-500">Every agent run in runs/, newest first.</p></div><div className="flex gap-2"><div className="relative flex-1 sm:w-52"><Search className="absolute left-2.5 top-2.5 size-3.5 text-slate-500" /><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search runs" className="h-8 w-full rounded-md border border-slate-700 bg-[#0b1727] pl-8 pr-3 text-xs text-slate-200 outline-none placeholder:text-slate-600 focus:border-cyan-300/50" /></div><select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} className="h-8 rounded-md border border-slate-700 bg-[#0b1727] px-2 text-xs text-slate-300 outline-none">{statuses.map((s) => <option key={s}>{s}</option>)}</select></div></div>
                <div className="max-h-[420px] divide-y divide-slate-800/70 overflow-y-auto">
                  {filtered.length === 0 && <div className="p-6 text-center text-xs text-slate-500">{list.length ? 'No runs match.' : 'No runs yet.'}</div>}
                  {filtered.map((i) => { const sev = severity(i.alert_type); return <button key={i.id} onClick={() => setSelectedId(i.id)} className={cn('flex w-full items-start gap-3 p-4 text-left transition-colors hover:bg-slate-800/30', detail?.id === i.id && 'bg-cyan-300/[0.045]')}><div className={cn('mt-1 size-2 shrink-0 rounded-full', sev === 'SEV-1' ? 'bg-red-400 shadow-[0_0_8px_#f87171]' : sev === 'SEV-2' ? 'bg-amber-300' : 'bg-slate-500')} /><div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-2"><span className="font-mono text-[11px] font-medium text-cyan-200">{i.id}</span><Badge tone={sev === 'SEV-1' ? 'red' : sev === 'SEV-2' ? 'amber' : 'slate'} title="derived from the alert type">{sev}</Badge><Badge tone={statusTone(i.status)}>{i.status}</Badge></div><div className="mt-2 truncate text-sm font-medium text-slate-200">{i.title}</div><div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-slate-500"><span className="flex items-center gap-1"><Server className="size-3" />{i.component || i.service || '—'}</span><span className="flex items-center gap-1"><Clock3 className="size-3" />{startedAt(i.id)}</span><span className="flex min-w-0 items-center gap-1"><CircleDot className="size-3 shrink-0" /><span className="truncate">{i.stage}</span></span></div></div><ChevronRight className="mt-5 size-4 shrink-0 text-slate-600" /></button> })}
                </div>
              </div>
              <Card title="Service health" subtitle={detail ? `From the first sweep of run ${detail.id} (service_overview, read-only)` : 'Select a run'}>
                {services.length === 0 ? <div className="text-xs text-slate-500">No service metrics in this run yet.</div>
                  : <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">{services.slice(0, 8).map((s) => { const err = s.value.err_rate as number | null; return <div key={s.id} className="rounded-lg border border-slate-800 bg-[#0c1726] p-3" title={s.detail}><div className="flex items-center justify-between"><div className="flex min-w-0 items-center gap-2"><span className={cn('size-2 shrink-0 rounded-full', s.anomalous ? 'bg-red-400' : 'bg-emerald-300')} /><span className="truncate text-xs font-medium text-slate-300">{s.ref?.split(':')[1]}</span></div><span className="font-mono text-[10px] text-cyan-300">[{s.id}]</span></div><div className="mt-4 flex items-end justify-between"><span className="font-mono text-lg text-slate-100">{err === null || err === undefined ? 'n/a' : (err * 100).toFixed(1)}{err !== null && err !== undefined && <span className="ml-1 text-xs text-slate-500">%</span>}</span><span className="text-[10px] text-slate-500">error rate</span></div><div className="mt-2 h-1 overflow-hidden rounded-full bg-slate-800"><div className={cn('h-full rounded-full', s.anomalous ? 'bg-red-400' : 'bg-emerald-300')} style={{ width: `${Math.min(100, Math.max(2, (err ?? 0) * 100))}%` }} /></div><div className="mt-2 text-[10px] text-slate-500">{s.value.rps?.toFixed?.(2)} rps · p95 {s.value.p95_ms ? `${Math.round(s.value.p95_ms)}ms` : 'n/a'}</div></div> })}</div>}
              </Card>
            </div>
            <aside className="min-w-0 space-y-6">
              {myPending && <PendingQuestion pending={myPending} onAnswer={answer} busy={busy} />}
              {pending && !myPending && <button onClick={() => setSelectedId(pending.run_id)} className="w-full rounded-xl border border-amber-300/30 bg-amber-300/[0.06] p-3 text-left text-xs text-amber-200">Run {pending.run_id} is waiting for your answer → open it</button>}
              <div className={cn('rounded-xl border bg-[#121c2d] shadow-[0_16px_40px_rgba(0,0,0,.15)]', escalated ? 'border-red-300/30' : 'border-slate-800/90')}>
                <div className="border-b border-slate-800/90 p-4">
                  <div className="flex items-start justify-between gap-3"><div><div className="flex items-center gap-2"><Badge tone={detail ? statusTone(detail.status) : 'slate'}>{detail?.status ?? 'no run'}</Badge><span className="font-mono text-[11px] text-slate-500">{detail?.id}</span></div><h2 className="mt-3 text-base font-semibold leading-6 text-white">{detail?.title ?? 'Select a run'}</h2></div>{detail && <button onClick={() => setSelectedId(null)} className="rounded-md p-1 text-slate-500 hover:bg-slate-800 hover:text-slate-300" aria-label="Show newest run"><X className="size-4" /></button>}</div>
                  {detail?.alerts?.length ? <div className="mt-3 space-y-1">{detail.alerts.map((a, n) => <div key={n} className="flex items-center gap-2 font-mono text-[11px] text-slate-400"><Siren className="size-3 text-red-300" />{a.type} · {a.service} · value {String(a.value)}{a.baseline !== undefined && a.baseline !== null ? ` (baseline ${a.baseline})` : ''}</div>)}<div className="text-[10px] text-slate-600">alert raised {startedAt(detail.id)}</div></div> : null}
                  {escalated && <div className="mt-4 flex items-start gap-2 rounded-lg border border-red-300/25 bg-red-300/[0.07] px-3 py-2.5 text-xs text-red-200"><AlertTriangle className="mt-0.5 size-3.5 shrink-0" /><div><div className="font-semibold">Agent is not confident — here is what it found</div><div className="mt-1 text-[11px] text-red-200/80">No fix is proposed. A human should review the evidence below.</div></div></div>}
                </div>
                <div className="p-4">
                  {!diag ? <div className="text-xs text-slate-500">{detail ? 'No diagnosis yet: the agent is still investigating.' : 'No run selected.'}</div> : <>
                    <div className="mb-4 flex items-center justify-between"><div><div className="text-[10px] uppercase tracking-[0.16em] text-slate-500">Failing component</div><div className="mt-1 text-sm font-medium text-slate-200">{diag.root_cause?.service ?? '—'}</div><div className="mt-0.5 font-mono text-[10px] text-slate-500">{diag.root_cause?.category}</div></div><div className="text-right"><div className="text-[10px] uppercase tracking-[0.16em] text-slate-500">Confidence</div><div className={cn('mt-1 text-sm font-semibold', (confidence ?? 0) < ACT_CONFIDENCE ? 'text-red-300' : 'text-cyan-200')}>{pct(confidence)}</div></div></div>
                    <div className="mb-4 h-1.5 overflow-hidden rounded-full bg-slate-800"><div className={cn('h-full rounded-full', (confidence ?? 0) < ACT_CONFIDENCE ? 'bg-red-400' : 'bg-cyan-300')} style={{ width: `${Math.round((confidence ?? 0) * 100)}%` }} /></div>
                    <p className="text-xs leading-5 text-slate-300">{diag.summary}</p>
                    {diag.root_cause?.description && <p className="mt-2 text-xs leading-5 text-slate-400">{diag.root_cause.description}</p>}
                    <div className="mt-5"><div className="mb-2 flex items-center justify-between"><span className="text-[10px] font-semibold uppercase tracking-[0.16em] text-slate-500">Cited evidence</span><span className="font-mono text-[10px] text-cyan-300">{diag.evidence?.length ?? 0} refs</span></div>
                      <div className="space-y-2">{(diag.evidence ?? []).length === 0 && <div className="text-[11px] text-slate-500">No valid evidence was cited.</div>}{(diag.evidence ?? []).map((e) => <div key={e.id} className="rounded-lg border border-slate-800 bg-[#0b1727] p-3"><div className="flex items-center justify-between"><span className="font-mono text-[11px] text-cyan-200">[{e.id}]</span><span className="flex items-center gap-2 text-[10px] text-slate-600">{e.kind}{e.anomalous && <Badge tone="red">abnormal</Badge>}</span></div><div className="mt-1 text-[11px] leading-4 text-slate-300">{e.observed}</div><div className="mt-1 text-[11px] leading-4 text-slate-500">why: {e.why}</div>{e.ref && <div className="mt-1 font-mono text-[10px] text-slate-600">{e.ref}</div>}</div>)}</div>
                      {(diag.validation_notes ?? []).length > 0 && <div className="mt-3 space-y-1">{diag.validation_notes!.map((n, k) => <div key={k} className="flex items-start gap-1.5 text-[10px] leading-4 text-amber-200/80"><Info className="mt-0.5 size-3 shrink-0" />{n}</div>)}</div>}
                    </div>
                  </>}
                </div>
              </div>
              <Card title="Proposed remediation" subtitle="Allowlisted runbook action · sent to the Action API by the agent only after approval" right={detail?.approval ? <Badge tone={detail.approval.approved ? 'green' : myPending ? 'amber' : 'red'}>{detail.approval.approved ? 'approved' : detail.plan ? 'not approved' : 'none'}</Badge> : myPending?.kind === 'approve' ? <Badge tone="amber">pending</Badge> : undefined}>
                {!detail?.plan ? <div className="text-xs text-slate-500">{diag ? 'No fix proposed (escalated, no incident, or no safe allowlisted action for this category).' : 'Waiting for a diagnosis.'}</div> : <>
                  <div className="rounded-lg border border-cyan-300/20 bg-cyan-300/[0.045] p-3"><div className="flex items-center gap-2 text-xs font-semibold text-cyan-200"><TerminalSquare className="size-3.5" />{detail.plan.action}</div><div className="mt-2 space-y-0.5 font-mono text-[11px] text-slate-400">{Object.entries(detail.plan.params).map(([k, v]) => <div key={k}>{k}: {String(v)}</div>)}</div><div className="mt-2 text-[11px] leading-4 text-slate-400">{detail.plan.reason}</div><div className="mt-2 text-[11px] leading-4 text-slate-500">Rollback: {detail.plan.rollback}</div></div>
                  <button onClick={() => setShowPreview(!showPreview)} className="mt-3 flex w-full items-center justify-center gap-2 rounded-lg border border-slate-700 bg-slate-800/60 py-2 text-xs font-medium text-slate-300 hover:bg-slate-800"><Eye className="size-3" />{showPreview ? 'Hide dry run' : 'Preview dry run'}</button>
                  {showPreview && <div className="mt-2 rounded-lg border border-slate-800 bg-[#0b1727] p-3 font-mono text-[11px] leading-4 text-slate-300">{detail.plan.dry_run_preview ?? 'no dry run recorded'}</div>}
                  {detail.approval && <div className={cn('mt-3 rounded-lg border p-3 text-[11px] leading-4', detail.approval.approved ? 'border-emerald-300/20 bg-emerald-300/5 text-emerald-200' : 'border-red-300/20 bg-red-300/5 text-red-200')}>{detail.approval.approved ? `Approved by ${detail.approval.by}.` : `Not approved: ${detail.approval.reason}`}</div>}
                </>}
              </Card>
              {(detail?.validation || detail?.rollback || detail?.report?.action_result) && <Card title="Verification" subtitle="Alert signals at detection vs re-checks after the fix">
                {detail.report?.action_result && <div className="mb-3 text-[11px] text-slate-400">Action result: <span className="font-mono text-slate-200">{detail.report.action_result.status}</span>{detail.report.action_result.why ? ` (${detail.report.action_result.why})` : ''}{detail.report.action_result.would_send ? ` · would send: ${detail.report.action_result.would_send}` : ''}</div>}
                {detail.validation && <>
                  <div className={cn('mb-3 flex items-center gap-2 text-xs font-semibold', detail.validation.recovered ? 'text-emerald-300' : 'text-red-300')}>{detail.validation.recovered ? <Check className="size-3.5" /> : <AlertTriangle className="size-3.5" />}{detail.validation.recovered ? 'Recovered' : 'Not recovered'}</div>
                  <div className="text-[10px] uppercase tracking-[0.16em] text-slate-500">Before (at detection)</div>
                  <div className="mb-2 mt-1 space-y-0.5 font-mono text-[11px] text-slate-400">{(detail.alerts ?? []).map((a, n) => <div key={n}>{a.type} {a.service}: {String(a.value)}</div>)}</div>
                  {detail.validation.polls.map((p) => <div key={p.poll} className="mt-2"><div className="text-[10px] uppercase tracking-[0.16em] text-slate-500">After · re-check {p.poll}</div><div className="mt-1 font-mono text-[11px] text-slate-400">{p.still_firing.length === 0 && p.new_alerts.length === 0 ? 'no alerts firing' : <>{p.still_firing.map((a, n) => <div key={`s${n}`} className="text-red-300">still firing: {a.type} {a.service} {String(a.value)}</div>)}{p.new_alerts.map((a, n) => <div key={`n${n}`} className="text-amber-200">new: {a.type} {a.service} {String(a.value)}</div>)}</>}</div></div>)}
                </>}
                {detail.rollback && <div className="mt-3 rounded-lg border border-amber-300/20 bg-amber-300/5 p-3 text-[11px] text-amber-200">Rollback: {detail.rollback.status}{detail.rollback.why ? ` (${detail.rollback.why})` : ''}</div>}
              </Card>}
              <div className="rounded-xl border border-slate-800/90 bg-[#101b2b] p-4">
                <div className="flex items-center justify-between"><div className="flex items-center gap-2"><div className={cn('grid size-7 place-items-center rounded-md', killOn ? 'bg-red-400/15 text-red-300' : 'bg-emerald-300/10 text-emerald-300')}><ShieldCheck className="size-4" /></div><div><div className="text-xs font-semibold text-slate-200">Kill switch</div><div className="text-[10px] text-slate-500">{!api_?.configured ? 'Action API not configured: nothing can execute' : !api_.reachable ? 'Action API unreachable: state unknown' : killOn ? 'ON · the Action API refuses every action' : 'off · actions allowed after approval'}</div></div></div>
                  <button disabled={busy || !api_?.reachable} onClick={toggleKill} className={cn('relative h-5 w-9 rounded-full transition-colors disabled:opacity-40', killOn ? 'bg-red-400' : 'bg-slate-700')} aria-label={killOn ? 'Release kill switch' : 'Activate kill switch'}><span className={cn('absolute top-0.5 size-4 rounded-full bg-white transition-transform', killOn ? 'translate-x-[18px]' : 'translate-x-0.5')} /></button></div>
                <div className="mt-3 flex items-start gap-2 text-[10px] leading-4 text-slate-500"><Info className="mt-0.5 size-3 shrink-0" />The toggle shows the state the Action API confirms, never a local guess. A STOP file in the repo root also keeps it on (release it by deleting the file).</div>
              </div>
            </aside>
          </div>
          <div className="mt-6 rounded-xl border border-slate-800/90 bg-[#101b2b]">
            <div className="flex flex-col gap-3 border-b border-slate-800/90 p-4 sm:flex-row sm:items-center sm:justify-between"><div><h2 className="text-sm font-semibold text-slate-100">Investigation trail</h2><p className="mt-1 text-xs text-slate-500">Tools in the order the agent called them · run {detail?.id ?? '—'}</p></div><div className="flex gap-2"><button disabled={!detail?.diagnosis && !detail?.report} onClick={exportReport} className="flex items-center gap-1.5 rounded-md border border-slate-700 px-2.5 py-1.5 text-[11px] font-medium text-slate-300 hover:bg-slate-800 disabled:opacity-40"><Download className="size-3.5" />{detail?.report ? 'Download report.json' : 'Download diagnosis.json'}</button>{diag && <Badge tone={dropped ? 'amber' : 'green'} title="every cited id is checked against the evidence ledger by agent/brain.py">{diag.evidence?.length ?? 0} cited ids checked{dropped ? ` · ${dropped} invented dropped` : ''}</Badge>}</div></div>
            {(diag?.tool_trail ?? []).length === 0 ? <div className="p-4 text-xs text-slate-500">{detail ? 'No tool calls recorded yet (the trail is saved with the diagnosis).' : 'No run selected.'}</div>
              : <div className="grid gap-0 divide-y divide-slate-800/70 md:grid-cols-3 md:divide-x md:divide-y-0 xl:grid-cols-5">{diag!.tool_trail!.map((t, n) => <div key={n} className="p-4"><div className="mb-3 flex items-center justify-between"><span className={cn('grid size-7 place-items-center rounded-md text-xs font-semibold', t.by === 'llm' ? 'bg-cyan-300/10 text-cyan-200' : 'bg-slate-700/40 text-slate-300')}>{String(n + 1).padStart(2, '0')}</span><Badge tone={t.by === 'llm' ? 'cyan' : 'slate'}>{t.by === 'llm' ? 'chosen by LLM' : 'fixed sweep'}</Badge></div><div className="font-mono text-[11px] font-semibold text-slate-300">{t.tool}</div><div className="mt-2 break-all font-mono text-[10px] leading-4 text-slate-500">{Object.keys(t.args ?? {}).length ? JSON.stringify(t.args) : 'no arguments'}</div></div>)}</div>}
            {(detail?.timeline ?? []).length > 0 && <div className="border-t border-slate-800/90 p-4"><div className="mb-2 text-[10px] font-semibold uppercase tracking-[0.16em] text-slate-500">Timeline</div><div className="space-y-1">{detail!.timeline.map((e, n) => <div key={n} className="flex gap-3 font-mono text-[11px]"><span className="shrink-0 text-slate-600">{e.t}</span><span className="w-20 shrink-0 uppercase text-cyan-300/80">{e.phase}</span><span className="text-slate-400">{e.msg}</span></div>)}</div></div>}
            {detail?.report_md && <div className="border-t border-slate-800/90 p-4"><div className="mb-2 text-[10px] font-semibold uppercase tracking-[0.16em] text-slate-500">Incident report · {detail.outcome}</div><pre className="max-h-[360px] overflow-auto whitespace-pre-wrap rounded-lg border border-slate-800 bg-[#0b1727] p-3 text-[11px] leading-5 text-slate-300">{detail.report_md}</pre></div>}
          </div>
          <footer className="flex flex-col justify-between gap-2 py-6 text-[10px] text-slate-600 sm:flex-row"><span>Incident Zero · local operations console</span><span>Data: runs/ via the agent server at {AGENT_URL} · refreshes every {POLL_MS / 1000}s</span></footer>
        </div>
      </section>
    </div>
  </main>
}
