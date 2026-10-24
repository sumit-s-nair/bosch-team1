'use client'

import { useMemo, useState } from 'react'
import {
  Activity,
  AlertTriangle,
  Archive,
  ArrowUpRight,
  Ban,
  Check,
  ChevronRight,
  CircleDot,
  Clock3,
  CloudOff,
  Download,
  FileJson,
  Gauge,
  GitBranch,
  HeartPulse,
  Info,
  Layers3,
  LifeBuoy,
  LockKeyhole,
  Menu,
  MoreHorizontal,
  Play,
  RefreshCw,
  Search,
  Server,
  Settings2,
  ShieldCheck,
  Siren,
  Sparkles,
  TerminalSquare,
  TimerReset,
  TriangleAlert,
  X,
  Zap,
} from 'lucide-react'

type IncidentStatus = 'Awaiting approval' | 'Investigating' | 'Resolved' | 'Escalated'

type Incident = {
  id: string
  service: string
  title: string
  severity: 'SEV-1' | 'SEV-2' | 'SEV-3'
  status: IncidentStatus
  detected: string
  stage: string
  confidence: number
  component: string
  cause: string
  evidence: { id: string; label: string; detail: string; time: string; tone: 'red' | 'amber' | 'cyan' }[]
}

const incidents: Incident[] = [
  {
    id: 'INC-2026-042',
    service: 'checkout-service',
    title: 'Elevated checkout errors after deployment',
    severity: 'SEV-1',
    status: 'Awaiting approval',
    detected: '12 min ago',
    stage: 'Human approval',
    confidence: 0.92,
    component: 'checkout-service',
    cause: 'Downstream payment calls are timing out after a recent service restart. Error rate is 18.4% with p95 latency at 4.8s.',
    evidence: [
      { id: '[E14]', label: 'Service overview', detail: 'checkout-service error rate 18.4%, baseline 0.7%; p95 is 4.8s.', time: '09:41:12', tone: 'red' },
      { id: '[E18]', label: 'Trace errors', detail: 'payment-gateway spans account for 87% of failed checkout traces.', time: '09:41:29', tone: 'amber' },
      { id: '[E21]', label: 'Recent changes', detail: 'checkout-service container restarted 14 minutes before alert.', time: '09:41:44', tone: 'cyan' },
    ],
  },
  {
    id: 'INC-2026-041',
    service: 'product-catalog',
    title: 'Catalog reads slower than baseline',
    severity: 'SEV-2',
    status: 'Investigating',
    detected: '28 min ago',
    stage: 'Collecting evidence',
    confidence: 0.64,
    component: 'astronomy-db',
    cause: 'Database health is degraded, but the agent has not isolated whether lock contention or query latency is primary.',
    evidence: [
      { id: '[E07]', label: 'Database health', detail: 'Query latency is above the learned baseline; verdict: slow-lock contention.', time: '09:25:03', tone: 'amber' },
      { id: '[E09]', label: 'Container health', detail: 'astronomy-db is running with no recent restart signal.', time: '09:25:24', tone: 'cyan' },
    ],
  },
  {
    id: 'INC-2026-039',
    service: 'cart-service',
    title: 'Cache saturation recovered',
    severity: 'SEV-3',
    status: 'Resolved',
    detected: '2 hr ago',
    stage: 'Verified recovery',
    confidence: 0.88,
    component: 'valkey-cart',
    cause: 'Cache memory pressure caused elevated cart read latency. Load was throttled and metrics returned to baseline.',
    evidence: [
      { id: '[E32]', label: 'Resource usage', detail: 'valkey-cart memory reached 91% of limit during the incident.', time: '07:14:22', tone: 'amber' },
      { id: '[E38]', label: 'Verification', detail: 'Error rate and p95 latency returned below alert thresholds.', time: '07:22:06', tone: 'cyan' },
    ],
  },
]

const navItems = [
  { label: 'Overview', icon: Activity },
  { label: 'Incidents', icon: Siren, count: 2 },
  { label: 'Approvals', icon: ShieldCheck, count: 1 },
  { label: 'Reports', icon: Archive },
]

const toolTrail = [
  { time: '09:41:05', tool: 'service_overview', reason: 'Confirm alert scope and identify abnormal services.', result: 'checkout-service: 18.4% errors · p95 4.8s', tone: 'red' },
  { time: '09:41:18', tool: 'container_health', reason: 'Check for restarts, OOMs, and unhealthy containers.', result: 'checkout-service restarted 14m ago', tone: 'amber' },
  { time: '09:41:29', tool: 'trace_errors', reason: 'Trace the failing request to a downstream dependency.', result: 'payment-gateway spans fail in 87% of traces', tone: 'red' },
  { time: '09:41:44', tool: 'recent_changes', reason: 'Compare the incident window with deployment activity.', result: 'Restart aligns with first error spike', tone: 'cyan' },
]

function cn(...classes: (string | false | null | undefined)[]) {
  return classes.filter(Boolean).join(' ')
}

function Badge({ children, tone = 'slate' }: { children: React.ReactNode; tone?: 'red' | 'amber' | 'green' | 'cyan' | 'slate' }) {
  return <span className={cn('inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-[11px] font-semibold uppercase tracking-[0.12em]', tone === 'red' && 'border-red-400/25 bg-red-400/10 text-red-300', tone === 'amber' && 'border-amber-300/25 bg-amber-300/10 text-amber-200', tone === 'green' && 'border-emerald-300/25 bg-emerald-300/10 text-emerald-300', tone === 'cyan' && 'border-cyan-300/25 bg-cyan-300/10 text-cyan-200', tone === 'slate' && 'border-slate-700 bg-slate-800/70 text-slate-300')}>{children}</span>
}

function MetricCard({ label, value, hint, icon: Icon, tone = 'cyan' }: { label: string; value: string; hint: string; icon: React.ElementType; tone?: 'cyan' | 'red' | 'green' }) {
  return <div className="rounded-xl border border-slate-800/90 bg-[#101b2b] p-4 shadow-[0_12px_30px_rgba(0,0,0,.12)]">
    <div className="flex items-start justify-between"><span className="text-xs font-medium text-slate-400">{label}</span><Icon className={cn('size-4', tone === 'red' && 'text-red-300', tone === 'green' && 'text-emerald-300', tone === 'cyan' && 'text-cyan-300')} /></div>
    <div className="mt-3 flex items-end justify-between"><strong className="text-2xl font-semibold tracking-tight text-slate-100">{value}</strong><span className={cn('text-[11px]', tone === 'red' ? 'text-red-300' : tone === 'green' ? 'text-emerald-300' : 'text-cyan-300')}>{hint}</span></div>
  </div>
}

export function IncidentZeroConsole() {
  const [activeNav, setActiveNav] = useState('Overview')
  const [selectedId, setSelectedId] = useState('INC-2026-042')
  const [query, setQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState<'All' | IncidentStatus>('All')
  const [killSwitch, setKillSwitch] = useState(false)
  const [approval, setApproval] = useState<'pending' | 'approved' | 'rejected'>('pending')
  const [lastUpdated, setLastUpdated] = useState('just now')
  const [mobileNav, setMobileNav] = useState(false)

  const selected = incidents.find((incident) => incident.id === selectedId) ?? incidents[0]
  const filteredIncidents = useMemo(() => incidents.filter((incident) => {
    const matchesQuery = `${incident.id} ${incident.service} ${incident.title}`.toLowerCase().includes(query.toLowerCase())
    const matchesStatus = statusFilter === 'All' || incident.status === statusFilter
    return matchesQuery && matchesStatus
  }), [query, statusFilter])

  const refresh = () => setLastUpdated('just now')
  const exportReport = () => {
    const blob = new Blob([JSON.stringify({ incident_id: selected.id, diagnosis: selected.cause, evidence: selected.evidence }, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `${selected.id.toLowerCase()}-report.json`
    anchor.click()
    URL.revokeObjectURL(url)
  }

  return <main className="min-h-screen bg-[#091321] text-slate-100 selection:bg-cyan-300/20">
    <div className="flex min-h-screen">
      <aside className={cn('fixed inset-y-0 left-0 z-30 flex w-[246px] flex-col border-r border-slate-800/80 bg-[#0b1727] transition-transform lg:static lg:translate-x-0', mobileNav ? 'translate-x-0' : '-translate-x-full')}>
        <div className="flex h-[72px] items-center gap-3 border-b border-slate-800/80 px-5"><div className="grid size-9 place-items-center rounded-lg bg-cyan-300 text-[#071321]"><Zap className="size-5 fill-current" /></div><div><div className="text-sm font-semibold tracking-wide text-white">incident<span className="text-cyan-300">zero</span></div><div className="text-[10px] uppercase tracking-[0.16em] text-slate-500">on-call console</div></div></div>
        <div className="px-4 pt-6"><div className="mb-3 px-2 text-[10px] font-semibold uppercase tracking-[0.18em] text-slate-500">Workspace</div>{navItems.map(({ label, icon: Icon, count }) => <button key={label} onClick={() => { setActiveNav(label); setMobileNav(false) }} className={cn('mb-1 flex w-full items-center justify-between rounded-lg px-3 py-2.5 text-left text-sm transition-colors', activeNav === label ? 'bg-cyan-300/10 text-cyan-200' : 'text-slate-400 hover:bg-slate-800/60 hover:text-slate-200')}><span className="flex items-center gap-3"><Icon className="size-4" />{label}</span>{count && <span className="rounded-md bg-red-400/15 px-1.5 py-0.5 text-[10px] font-semibold text-red-300">{count}</span>}</button>)}</div>
        <div className="mt-7 px-4"><div className="mb-3 px-2 text-[10px] font-semibold uppercase tracking-[0.18em] text-slate-500">System</div><button onClick={() => setActiveNav('Settings')} className={cn('mb-1 flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-left text-sm', activeNav === 'Settings' ? 'bg-cyan-300/10 text-cyan-200' : 'text-slate-400 hover:bg-slate-800/60 hover:text-slate-200')}><Settings2 className="size-4" />Settings</button><button onClick={() => setActiveNav('System status')} className="flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-left text-sm text-slate-400 hover:bg-slate-800/60 hover:text-slate-200"><Server className="size-4" />System status</button></div>
        <div className="mt-auto border-t border-slate-800/80 p-4"><div className="rounded-lg border border-emerald-300/20 bg-emerald-300/5 p-3"><div className="flex items-center gap-2 text-xs font-medium text-emerald-300"><span className="size-1.5 rounded-full bg-emerald-300 shadow-[0_0_9px_#6ee7b7]" />Agent connected</div><div className="mt-2 flex items-center justify-between text-[11px] text-slate-500"><span>Qwen3 8B · Ollama</span><span>v0.9.4</span></div></div><div className="mt-3 text-center text-[10px] leading-4 text-slate-600">Local demo bundle · telemetry is read-only</div></div>
      </aside>
      {mobileNav && <button aria-label="Close navigation" onClick={() => setMobileNav(false)} className="fixed inset-0 z-20 bg-black/60 lg:hidden" />}
      <section className="min-w-0 flex-1">
        <header className="flex h-[72px] items-center justify-between border-b border-slate-800/80 bg-[#0b1727]/90 px-4 sm:px-6"><div className="flex items-center gap-3"><button className="rounded-lg p-2 text-slate-400 hover:bg-slate-800 lg:hidden" onClick={() => setMobileNav(true)} aria-label="Open navigation"><Menu className="size-5" /></button><div><div className="text-xs text-slate-500">Operations / {activeNav}</div><div className="mt-0.5 text-sm font-medium text-slate-200">Friday, October 24, 2026 <span className="mx-2 text-slate-700">·</span><span className="text-slate-500">09:53 UTC</span></div></div></div><div className="flex items-center gap-3"><div className="hidden items-center gap-2 rounded-md border border-slate-800 bg-slate-900/40 px-2.5 py-1.5 text-[11px] text-slate-400 sm:flex"><span className="size-1.5 rounded-full bg-emerald-300" />Backend connected</div><button onClick={refresh} className="rounded-lg border border-slate-800 p-2 text-slate-400 hover:border-slate-700 hover:text-slate-200" aria-label="Refresh data"><RefreshCw className="size-4" /></button><div className="grid size-8 place-items-center rounded-full border border-cyan-300/20 bg-cyan-300/10 text-xs font-semibold text-cyan-200">ON</div></div></header>
        <div className="mx-auto max-w-[1500px] p-4 sm:p-6 lg:p-8">
          <div className="mb-7 flex flex-col justify-between gap-4 lg:flex-row lg:items-end"><div><div className="mb-2 flex items-center gap-2"><span className="size-2 rounded-full bg-red-400 shadow-[0_0_10px_#f87171]" /><span className="text-xs font-semibold uppercase tracking-[0.18em] text-red-300">Attention required</span></div><h1 className="text-2xl font-semibold tracking-tight text-white sm:text-[30px]">Operations overview</h1><p className="mt-1 text-sm text-slate-400">Your AI on-call engineer is investigating an active incident.</p></div><div className="flex items-center gap-2"><Badge tone="slate"><Clock3 className="size-3" />Updated {lastUpdated}</Badge><Badge tone="cyan"><Sparkles className="size-3" />Offline demo mode</Badge></div></div>
          <div className="mb-7 grid gap-3 sm:grid-cols-2 xl:grid-cols-4"><MetricCard label="Active incidents" value="2" hint="1 awaiting approval" icon={Siren} tone="red" /><MetricCard label="Services healthy" value="11 / 13" hint="84.6%" icon={HeartPulse} tone="green" /><MetricCard label="Error rate" value="18.4%" hint="+17.7% vs baseline" icon={TriangleAlert} tone="red" /><MetricCard label="Agent confidence" value="92%" hint="high confidence" icon={Gauge} /></div>
          <div className="grid gap-6 xl:grid-cols-[minmax(0,1.5fr)_minmax(360px,.9fr)]">
            <div className="min-w-0 space-y-6">
              <div className="overflow-hidden rounded-xl border border-slate-800/90 bg-[#101b2b]"><div className="flex flex-col gap-3 border-b border-slate-800/90 p-4 sm:flex-row sm:items-center sm:justify-between"><div><h2 className="text-sm font-semibold text-slate-100">Active incidents</h2><p className="mt-1 text-xs text-slate-500">Select an incident to inspect the investigation trail.</p></div><div className="flex gap-2"><div className="relative flex-1 sm:w-52"><Search className="absolute left-2.5 top-2.5 size-3.5 text-slate-500" /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search incidents" className="h-8 w-full rounded-md border border-slate-700 bg-[#0b1727] pl-8 pr-3 text-xs text-slate-200 outline-none placeholder:text-slate-600 focus:border-cyan-300/50" /></div><select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value as 'All' | IncidentStatus)} className="h-8 rounded-md border border-slate-700 bg-[#0b1727] px-2 text-xs text-slate-300 outline-none"><option>All</option><option>Awaiting approval</option><option>Investigating</option><option>Resolved</option><option>Escalated</option></select></div></div><div className="divide-y divide-slate-800/70">{filteredIncidents.map((incident) => <button key={incident.id} onClick={() => setSelectedId(incident.id)} className={cn('flex w-full items-start gap-3 p-4 text-left transition-colors hover:bg-slate-800/30', selected.id === incident.id && 'bg-cyan-300/[0.045]')}><div className={cn('mt-1 size-2 shrink-0 rounded-full', incident.severity === 'SEV-1' ? 'bg-red-400 shadow-[0_0_8px_#f87171]' : incident.severity === 'SEV-2' ? 'bg-amber-300' : 'bg-slate-500')} /><div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-2"><span className="font-mono text-[11px] font-medium text-cyan-200">{incident.id}</span><Badge tone={incident.severity === 'SEV-1' ? 'red' : incident.severity === 'SEV-2' ? 'amber' : 'slate'}>{incident.severity}</Badge><Badge tone={incident.status === 'Resolved' ? 'green' : incident.status === 'Awaiting approval' ? 'amber' : 'slate'}>{incident.status}</Badge></div><div className="mt-2 truncate text-sm font-medium text-slate-200">{incident.title}</div><div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-slate-500"><span className="flex items-center gap-1"><Server className="size-3" />{incident.service}</span><span className="flex items-center gap-1"><Clock3 className="size-3" />Detected {incident.detected}</span><span className="flex items-center gap-1"><CircleDot className="size-3" />{incident.stage}</span></div></div><ChevronRight className="mt-5 size-4 shrink-0 text-slate-600" /></button>)}</div></div>
              <div className="rounded-xl border border-slate-800/90 bg-[#101b2b]"><div className="flex items-center justify-between border-b border-slate-800/90 p-4"><div><h2 className="text-sm font-semibold text-slate-100">Service health</h2><p className="mt-1 text-xs text-slate-500">Read-only telemetry summary · last 15 minutes</p></div><button className="text-slate-500 hover:text-slate-300" aria-label="More service health options"><MoreHorizontal className="size-4" /></button></div><div className="grid gap-3 p-4 sm:grid-cols-2 xl:grid-cols-4">{['checkout-service', 'product-catalog', 'cart-service', 'payment-gateway'].map((service, index) => <div key={service} className="rounded-lg border border-slate-800 bg-[#0c1726] p-3"><div className="flex items-center justify-between"><div className="flex items-center gap-2"><span className={cn('size-2 rounded-full', index === 0 ? 'bg-red-400' : index === 1 ? 'bg-amber-300' : 'bg-emerald-300')} /><span className="text-xs font-medium text-slate-300">{service}</span></div><ArrowUpRight className="size-3 text-slate-600" /></div><div className="mt-4 flex items-end justify-between"><span className="font-mono text-lg text-slate-100">{index === 0 ? '18.4' : index === 1 ? '3.1' : index === 2 ? '0.4' : '1.2'}<span className="ml-1 text-xs text-slate-500">%</span></span><span className="text-[10px] text-slate-500">error rate</span></div><div className="mt-2 h-1 overflow-hidden rounded-full bg-slate-800"><div className={cn('h-full rounded-full', index === 0 ? 'w-[82%] bg-red-400' : index === 1 ? 'w-[26%] bg-amber-300' : 'w-[4%] bg-emerald-300')} /></div></div>)}</div></div>
            </div>
            <aside className="min-w-0 space-y-6">
              <div className="rounded-xl border border-red-300/20 bg-[#121c2d] shadow-[0_16px_40px_rgba(0,0,0,.15)]"><div className="border-b border-slate-800/90 p-4"><div className="flex items-start justify-between gap-3"><div><div className="flex items-center gap-2"><Badge tone="red">{selected.severity}</Badge><span className="font-mono text-[11px] text-slate-500">{selected.id}</span></div><h2 className="mt-3 text-base font-semibold leading-6 text-white">{selected.title}</h2></div><button className="rounded-md p-1 text-slate-500 hover:bg-slate-800 hover:text-slate-300" aria-label="Close incident details"><X className="size-4" /></button></div><div className="mt-4 flex items-center justify-between rounded-lg border border-amber-300/20 bg-amber-300/[0.06] px-3 py-2.5"><div className="flex items-center gap-2 text-xs font-medium text-amber-200"><LockKeyhole className="size-3.5" />Approval required</div><span className="text-[11px] text-slate-500">{selected.stage}</span></div></div><div className="p-4"><div className="mb-5 flex items-center justify-between"><div><div className="text-[10px] uppercase tracking-[0.16em] text-slate-500">Diagnosis</div><div className="mt-1 text-sm font-medium text-slate-200">{selected.component}</div></div><div className="text-right"><div className="text-[10px] uppercase tracking-[0.16em] text-slate-500">Confidence</div><div className="mt-1 text-sm font-semibold text-cyan-200">{Math.round(selected.confidence * 100)}%</div></div></div><p className="text-xs leading-5 text-slate-400">{selected.cause}</p><div className="mt-5"><div className="mb-2 flex items-center justify-between"><span className="text-[10px] font-semibold uppercase tracking-[0.16em] text-slate-500">Supporting evidence</span><span className="font-mono text-[10px] text-cyan-300">{selected.evidence.length} refs</span></div><div className="space-y-2">{selected.evidence.map((item) => <div key={item.id} className="rounded-lg border border-slate-800 bg-[#0b1727] p-3"><div className="flex items-center justify-between"><span className="font-mono text-[11px] text-cyan-200">{item.id}</span><span className="text-[10px] text-slate-600">{item.time} UTC</span></div><div className="mt-1 text-[11px] font-medium text-slate-300">{item.label}</div><div className="mt-1 text-[11px] leading-4 text-slate-500">{item.detail}</div></div>)}</div></div></div></div>
              <div className="rounded-xl border border-slate-800/90 bg-[#101b2b]"><div className="flex items-center justify-between border-b border-slate-800/90 p-4"><div><h2 className="text-sm font-semibold text-slate-100">Proposed remediation</h2><p className="mt-1 text-xs text-slate-500">Allowlisted Action API · no changes yet</p></div><Badge tone={approval === 'pending' ? 'amber' : approval === 'approved' ? 'green' : 'red'}>{approval}</Badge></div><div className="p-4"><div className="rounded-lg border border-cyan-300/20 bg-cyan-300/[0.045] p-3"><div className="flex items-center gap-2 text-xs font-semibold text-cyan-200"><TerminalSquare className="size-3.5" />restart_service</div><div className="mt-2 font-mono text-[11px] text-slate-400">service: checkout-service</div><div className="mt-2 text-[11px] leading-4 text-slate-500">Restart the affected service after payment-gateway timeout evidence. Action is blocked until a human approves.</div></div><button className="mt-3 flex w-full items-center justify-center gap-2 rounded-lg border border-slate-700 bg-slate-800/60 py-2 text-xs font-medium text-slate-300 hover:bg-slate-800"><Play className="size-3" />Preview dry run</button>{approval === 'pending' && <div className="mt-3 grid grid-cols-2 gap-2"><button onClick={() => setApproval('approved')} className="flex items-center justify-center gap-2 rounded-lg bg-cyan-300 py-2 text-xs font-semibold text-[#081321] hover:bg-cyan-200"><Check className="size-3.5" />Approve action</button><button onClick={() => setApproval('rejected')} className="flex items-center justify-center gap-2 rounded-lg border border-red-300/25 bg-red-300/10 py-2 text-xs font-semibold text-red-200 hover:bg-red-300/15"><Ban className="size-3.5" />Reject</button></div>}{approval === 'approved' && <div className="mt-3 rounded-lg border border-emerald-300/20 bg-emerald-300/5 p-3 text-[11px] leading-4 text-emerald-200">Approved locally for demo preview. Execution remains delegated to the backend Action API.</div>}{approval === 'rejected' && <div className="mt-3 rounded-lg border border-red-300/20 bg-red-300/5 p-3 text-[11px] leading-4 text-red-200">Action rejected. No system change was requested.</div>}</div></div>
              <div className="rounded-xl border border-slate-800/90 bg-[#101b2b] p-4"><div className="flex items-center justify-between"><div className="flex items-center gap-2"><div className={cn('grid size-7 place-items-center rounded-md', killSwitch ? 'bg-red-400/15 text-red-300' : 'bg-emerald-300/10 text-emerald-300')}><ShieldCheck className="size-4" /></div><div><div className="text-xs font-semibold text-slate-200">Kill switch</div><div className="text-[10px] text-slate-500">Backend state · {killSwitch ? 'agent paused' : 'monitoring'}</div></div></div><button onClick={() => setKillSwitch(!killSwitch)} className={cn('relative h-5 w-9 rounded-full transition-colors', killSwitch ? 'bg-red-400' : 'bg-slate-700')} aria-label={killSwitch ? 'Release kill switch' : 'Activate kill switch'}><span className={cn('absolute top-0.5 size-4 rounded-full bg-white transition-transform', killSwitch ? 'translate-x-[18px]' : 'translate-x-0.5')} /></button></div><div className="mt-3 flex items-start gap-2 text-[10px] leading-4 text-slate-500"><Info className="mt-0.5 size-3 shrink-0" />Activation must be confirmed by the Action API. This console never claims a frontend-only stop succeeded.</div></div>
            </aside>
          </div>
          <div className="mt-6 rounded-xl border border-slate-800/90 bg-[#101b2b]"><div className="flex flex-col gap-3 border-b border-slate-800/90 p-4 sm:flex-row sm:items-center sm:justify-between"><div><h2 className="text-sm font-semibold text-slate-100">Investigation timeline</h2><p className="mt-1 text-xs text-slate-500">Why the agent chose each tool · incident {selected.id}</p></div><div className="flex gap-2"><button onClick={exportReport} className="flex items-center gap-1.5 rounded-md border border-slate-700 px-2.5 py-1.5 text-[11px] font-medium text-slate-300 hover:bg-slate-800"><Download className="size-3.5" />Export JSON</button><Badge tone="green"><span className="size-1.5 rounded-full bg-emerald-300" />Evidence ledger verified</Badge></div></div><div className="grid gap-0 divide-y divide-slate-800/70 md:grid-cols-4 md:divide-x md:divide-y-0">{toolTrail.map((item, index) => <div key={item.tool} className="relative p-4"><div className="mb-4 flex items-center justify-between"><span className={cn('grid size-7 place-items-center rounded-md text-xs font-semibold', item.tone === 'red' ? 'bg-red-400/10 text-red-300' : item.tone === 'amber' ? 'bg-amber-300/10 text-amber-200' : 'bg-cyan-300/10 text-cyan-200')}>{String(index + 1).padStart(2, '0')}</span><span className="font-mono text-[10px] text-slate-600">{item.time}</span></div><div className="font-mono text-[11px] font-semibold text-slate-300">{item.tool}</div><div className="mt-2 text-[11px] leading-4 text-slate-500">{item.reason}</div><div className="mt-3 border-l border-slate-700 pl-2 text-[11px] leading-4 text-slate-300">{item.result}</div></div>)}</div></div>
          <footer className="flex flex-col justify-between gap-2 py-6 text-[10px] text-slate-600 sm:flex-row"><span>Incident Zero · local operations console</span><span className="flex items-center gap-2"><CloudOff className="size-3" />Offline bundle data is clearly marked · no live telemetry claims</span></footer>
        </div>
      </section>
    </div>
  </main>
}
