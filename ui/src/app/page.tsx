'use client'

import { useEffect, useMemo, useState } from 'react'
import {
    Activity,
    ArrowDownToLine,
    ArrowUpRight,
    Bot,
    Check,
    ChevronDown,
    ChevronRight,
    CircleHelp,
    Clipboard,
    Command,
    Database,
    FileCode2,
    Filter,
    Gauge,
    GitBranch,
    KeyRound,
    LayoutDashboard,
    Lock,
    LogOut,
    Maximize2,
    Menu,
    Network,
    PanelRight,
    Play,
    Plus,
    RefreshCw,
    Search,
    Settings2,
    Shield,
    SlidersHorizontal,
    Terminal,
    Timer,
    Trash2,
    Unlock,
    X,
    Zap,
} from 'lucide-react'

type View = 'Graph Explorer' | 'Agents & Access' | 'Config / Ontology' | 'Audit Log' | 'MCP Setup'

type Node = { id: string; label: string; scope: string; x: number; y: number; kind: string; locked?: boolean }

const nav: { label: View; icon: typeof Network }[] = [
    { label: 'Graph Explorer', icon: Network },
    { label: 'Agents & Access', icon: Bot },
    { label: 'Config / Ontology', icon: SlidersHorizontal },
    { label: 'Audit Log', icon: Activity },
    { label: 'MCP Setup', icon: Terminal },
]
const nodes: Node[] = [
    { id: 'n1', label: 'synapse', scope: 'PROJECT:synapse', x: 470, y: 205, kind: 'project' },
    { id: 'n2', label: 'memory graph', scope: 'PROJECT:synapse', x: 290, y: 135, kind: 'concept' },
    { id: 'n3', label: 'cryptographic partition', scope: 'research', x: 160, y: 245, kind: 'concept' },
    { id: 'n4', label: 'Agent-1', scope: 'research', x: 300, y: 345, kind: 'agent' },
    { id: 'n5', label: 'campus-bot', scope: 'PROJECT:campus', x: 625, y: 315, kind: 'agent' },
    { id: 'n6', label: 'ontology.yaml', scope: 'PROJECT:synapse', x: 650, y: 145, kind: 'config' },
    { id: 'n7', label: 'AES-256-GCM', scope: 'locked', x: 790, y: 255, kind: 'secret', locked: true },
    { id: 'n8', label: 'neo4j', scope: 'PROJECT:synapse', x: 515, y: 405, kind: 'service' },
    { id: 'n9', label: 'redis', scope: 'locked', x: 770, y: 420, kind: 'service', locked: true },
]
const edges = [['n1', 'n2'], ['n1', 'n4'], ['n1', 'n6'], ['n1', 'n8'], ['n2', 'n3'], ['n4', 'n3'], ['n4', 'n5'], ['n5', 'n8'], ['n6', 'n7'], ['n8', 'n9']]
const scopes = ['research', 'PROJECT:synapse', 'PROJECT:campus', 'locked']
const agents = [
    { id: 'campus-bot', initials: 'CB', scopes: ['PROJECT:campus', 'research'], status: 'active', active: '2m ago' },
    { id: 'Agent-1', initials: 'A1', scopes: ['research'], status: 'active', active: '8m ago' },
    { id: 'claude-desktop', initials: 'CD', scopes: ['PROJECT:synapse'], status: 'pending', active: '22m ago' },
    { id: 'cursor-agent', initials: 'CA', scopes: ['PROJECT:synapse', 'research'], status: 'revoked', active: '3h ago' },
]
const auditEvents = [
    ['14:42:08.331', 'Agent-1', 'traversal_step', 'research', 'allowed'],
    ['14:41:55.104', 'campus-bot', 'propose_fact', 'PROJECT:campus', 'allowed'],
    ['14:40:12.887', 'cursor-agent', 'permission_denied', 'locked', 'blocked'],
    ['14:39:44.010', 'Agent-1', 'traversal_step', 'PROJECT:synapse', 'allowed'],
    ['14:38:01.452', 'system', 'revoke', 'PROJECT:synapse', 'cascade'],
    ['14:36:21.729', 'campus-bot', 'traversal_step', 'research', 'allowed'],
]

function StatusDot({ status = 'active' }: { status?: string }) { return <span className={`status-dot ${status}`} aria-label={status} /> }
function Kbd({ children }: { children: React.ReactNode }) { return <span className="kbd">{children}</span> }
function ScopeChip({ name }: { name: string }) { return <span className={`scope-chip scope-${name.toLowerCase().replace(/[^a-z]/g, '-')}`}><span className="scope-mark" />{name}</span> }
function Button({ children, className = '', onClick, title }: { children: React.ReactNode; className?: string; onClick?: () => void; title?: string }) { return <button title={title} onClick={onClick} className={`ui-button ${className}`}>{children}</button> }

import LiveGraph from './GraphExplorer'

const API_BASE = process.env.NEXT_PUBLIC_SYNAPSE_URL || 'http://127.0.0.1:8000'

function apiFetch(path: string, init: RequestInit = {}) {
    const apiKey = typeof window !== 'undefined' ? window.localStorage.getItem('synapse_api_key') : null
    const headers = new Headers(init.headers)
    if (apiKey) headers.set('Authorization', `Bearer ${apiKey}`)
    return fetch(`${API_BASE}${path}`, { ...init, headers })
}

function Inspector({ node, onClose }: { node: any | null; onClose: () => void }) {
    if (!node) return null;
    return <aside className="inspector">
        <div className="inspector-head"><div><span className="eyebrow">Node inspector</span><h2>{node.locked ? 'Encrypted partition' : node.label || node.id}</h2></div><Button title="Close inspector" onClick={onClose}><X size={15} /></Button></div>
        <div className="inspector-state">{node.locked ? <><Lock size={16} /><div><strong>Locked partition</strong><span>Activate partition key to decrypt</span></div></> : <><Unlock size={16} /><div><strong>Decrypted fields</strong><span>Read access verified</span></div></>}</div>
        {node.locked ? <div className="locked-card"><KeyRound size={18} /><p>Fields are protected by AES-256-GCM. This agent does not have the required key scope.</p></div> : <div className="field-list"><div><span>node_id</span><code>{node.id}</code></div><div><span>type</span><code>{node.kind || node.type || 'Unknown'}</code></div><div><span>scope</span><ScopeChip name={node.scope || 'public'} /></div><div><span>valid_to</span><code>∞ active</code></div></div>}
        <div className="inspector-section">
            <span className="eyebrow">Properties</span>
            {Object.entries(node).filter(([k]) => !['x','y','vx','vy','fx','fy','index','__bckgDimensions','lastTraversedTime','label','kind'].includes(k)).map(([k, v]) => (
                <div className="edge-row" key={k}>
                   <GitBranch size={14} />
                   <code>{k}: {String(v)}</code>
                </div>
            ))}
        </div>
    </aside>
}

function GraphExplorer({ selected, onSelect }: any) {
    const [hopDepth, setHopDepth] = useState(2)
    return <section className="view-content graph-view">
        <div className="view-header compact-header">
            <div className="title-row">
                <h1>Graph Explorer</h1>
                <span className="divider">/</span>
                <p>Trace encrypted memory across Synapse partitions.</p>
            </div>
        </div>
        <div className="stats-strip">
            <div><span>Nodes</span><strong style={{ fontVariantNumeric: 'tabular-nums' }}>Live</strong></div>
            <div><span>Edges</span><strong style={{ fontVariantNumeric: 'tabular-nums' }}>Live</strong></div>
            <div><span>Hop depth</span><strong style={{ fontVariantNumeric: 'tabular-nums' }}>{hopDepth}</strong></div>
            <div><span>Last traversal</span><strong style={{ fontVariantNumeric: 'tabular-nums' }}>--<span className="unit">ms</span></strong></div>
            <div className="stats-status"><StatusDot /> <span>WebSocket stream healthy</span></div>
        </div>
        <div className="graph-layout">
            <LiveGraph selected={selected} onSelect={onSelect} hopDepth={hopDepth} />
            <Inspector node={selected} onClose={() => onSelect(null)} />
        </div>
    </section>
}

function AgentsViewLegacy() {
    const [open, setOpen] = useState<string | null>(null); 
    const [fetchedAgents, setFetchedAgents] = useState<any[]>([]);
    const [apiKey, setApiKey] = useState(() => typeof window !== 'undefined' ? window.localStorage.getItem('synapse_api_key') || '' : '');
    const [saved, setSaved] = useState(false);
    
    useEffect(() => {
        apiFetch('/agents')
            .then(res => { if (!res.ok) throw new Error('Failed to fetch'); return res.json(); })
            .then(data => {
                const arr = Object.values(data)
                    .filter((a: any) => a && a.id)
                    .map((a: any) => ({
                        id: a.id,
                        initials: a.id.substring(0, 2).toUpperCase(),
                        scopes: a.allowed_scopes || [],
                        status: 'active',
                        active: 'just now'
                    }));
                setFetchedAgents(arr);
            })
            .catch(err => console.error(err));
    }, []);

    const saveKey = () => { window.localStorage.setItem('synapse_api_key', apiKey.trim()); setSaved(true); setTimeout(() => setSaved(false), 1500) }
    return <section className="view-content"><ViewTitle title="Agents & Access" subtitle="Manage agent sessions and partition boundaries." /><div className="panel setup-panel" style={{ marginBottom: 16 }}><span className="eyebrow">Backend authentication</span><p className="mono-muted">Enter an admin API key to load agents and manage access. Secrets stay in this browser only.</p><div style={{ display: 'flex', gap: 8 }}><input type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder="syn_live_…" aria-label="Synapse API key" style={{ flex: 1 }} /><Button onClick={saveKey} className="primary">{saved ? 'Saved' : 'Save key'}</Button></div></div><div className="panel table-panel"><div className="table-head"><span>Agent identity</span><span>Allowed scopes</span><span>Status</span><span>Last active</span><span /></div>{fetchedAgents.map((agent) => <div key={agent.id} className={`agent-row ${open === agent.id ? 'row-open' : ''}`}><div className="agent-main" onClick={() => setOpen(open === agent.id ? null : agent.id)}><div className="avatar">{agent.initials}</div><div><strong>{agent.id}</strong><span className="mono-muted">API identity</span></div></div><div className="agent-scopes">{agent.scopes.map((s: string) => <ScopeChip key={s} name={s} />)}</div><div className="agent-status"><StatusDot status={agent.status} /> {agent.status}</div><code>{agent.active}</code><Button onClick={() => setOpen(open === agent.id ? null : agent.id)} title="Expand agent"><ChevronDown size={15} className={open === agent.id ? 'rotate' : ''} /></Button>{open === agent.id && <div className="agent-details"><div><span className="eyebrow">MCP transport</span><code>stdio → {API_BASE}</code></div><div className="secret-line"><span>Credential</span><code>{apiKey ? 'Bearer key configured' : 'No API key configured'}</code></div><div className="detail-actions"><span className="mono-muted">Create and revoke secrets through the authenticated admin API.</span></div></div>}</div>)}</div></section>
}
function AgentsView() {
    const [agents, setAgents] = useState<any[]>([]);
    const [keys, setKeys] = useState<any[]>([]);
    const [scopeKeys, setScopeKeys] = useState<any[]>([]);
    const [apiKey, setApiKey] = useState(() => typeof window !== 'undefined' ? window.localStorage.getItem('synapse_api_key') || '' : '');
    const [agentId, setAgentId] = useState('');
    const [scopes, setScopes] = useState('');
    const [notice, setNotice] = useState('');
    const [adminReady, setAdminReady] = useState(false);
    const load = () => Promise.all([
        apiFetch('/agents').then(r => r.ok ? r.json() : Promise.reject(new Error('Admin API key required'))),
        apiFetch('/admin/api-keys').then(r => r.ok ? r.json() : Promise.reject(new Error('Admin API key required'))),
        apiFetch('/admin/scope-keys').then(r => r.ok ? r.json() : Promise.reject(new Error('Admin API key required'))),
    ]).then(([agentData, keyData, scopeData]) => {
        setAdminReady(true);
        const list = Object.values(agentData).filter((a: any) => a?.id).map((a: any) => ({ ...a, initials: a.id.substring(0, 2).toUpperCase() }));
        setAgents(list); setKeys(keyData.keys || []); setScopeKeys(scopeData.keys || []);
        if (!agentId && list[0]) { setAgentId(list[0].id); setScopes((list[0].allowed_scopes || []).join(', ')); }
    }).catch(e => { setAdminReady(false); setNotice(e.message); });
    useEffect(() => { load(); }, []);
    const saveKey = () => { window.localStorage.setItem('synapse_api_key', apiKey.trim()); setNotice('Credential saved in this browser.'); load(); };
    const scopeList = () => scopes.split(',').map(s => s.trim()).filter(Boolean);
    const selectAgent = (id: string) => { const a = agents.find(item => item.id === id); setAgentId(id); setScopes((a?.allowed_scopes || []).join(', ')); };
    const updateScopes = async () => { const r = await apiFetch('/admin/agents', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ agent_id: agentId, scopes: scopeList() }) }); setNotice(r.ok ? `Scopes updated for ${agentId}.` : 'Could not update scopes.'); load(); };
    const createKey = async () => { const r = await apiFetch('/admin/api-keys', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ agent_id: agentId, scopes: scopeList() }) }); const data = await r.json(); setNotice(r.ok ? `Copy this key now: ${data.api_key}` : (data.detail || 'Could not create key.')); load(); };
    const revokeKey = async (id: string) => { const r = await apiFetch(`/admin/api-keys/${id}/revoke`, { method: 'POST' }); setNotice(r.ok ? `Revoked ${id}.` : 'Could not revoke key.'); load(); };
    return <section className="view-content"><ViewTitle title="Agents & Access" subtitle="Control which dataset scopes each agent can use." /><div className="panel setup-panel" style={{ marginBottom: 16 }}><span className="eyebrow">Administrator credential</span><p className="mono-muted">This screen requires the server administrator API key. An agent/MCP key cannot configure users or scopes. Raw dataset encryption keys never leave the daemon.</p><div style={{ display: 'flex', gap: 8 }}><input type="password" value={apiKey} onChange={e => setApiKey(e.target.value)} placeholder="Paste SYNAPSE_ADMIN_API_KEY" aria-label="Administrator API key" style={{ flex: 1 }} /><Button onClick={saveKey} className="primary">Connect admin</Button></div>{notice && <p className="mono-muted" style={{ marginTop: 10, color: adminReady ? '#9dd9b0' : '#ff9b9b' }}>{notice}</p>}</div>{!adminReady && <div className="panel setup-panel access-empty"><strong>Administrator access required</strong><span>Enter SYNAPSE_ADMIN_API_KEY above to load agents, configured dataset keys, and issued agent credentials.</span></div>}<div className="access-grid"><div className="panel setup-panel"><div className="panel-title"><span><KeyRound size={15} /> Agent scope permissions</span><span className="read-only">admin only</span></div><p className="mono-muted">Scopes are permission labels, not raw encryption keys. Assign <code>diagnosis</code> to Agent 1, or <code>diagnosis, patients</code> to Agent 2.</p><label className="eyebrow" htmlFor="access-agent">Select agent</label><select id="access-agent" className="agent-select" value={agentId} onChange={e => selectAgent(e.target.value)} disabled={!agents.length}>{agents.map(a => <option key={a.id}>{a.id}</option>)}</select><label className="eyebrow" htmlFor="access-scopes">Allowed dataset scopes</label><input id="access-scopes" value={scopes} onChange={e => setScopes(e.target.value)} placeholder="diagnosis, patients" disabled={!adminReady} /><span className="mono-muted">The agent can search/decrypt only after activating these scopes in its session.</span><div style={{ display: 'flex', gap: 8, marginTop: 12 }}><Button onClick={updateScopes} className="primary">Save permissions</Button><Button onClick={createKey}>Issue agent API key</Button></div></div><div className="panel setup-panel"><div className="panel-title"><span><Lock size={15} /> Dataset encryption keys</span><span className="read-only">server status</span></div><p className="mono-muted">These are customer-managed scope keys loaded by the server. The UI shows status only; it never accepts or displays the raw key.</p>{scopeKeys.length ? scopeKeys.map(s => <div className="key-row" key={s.scope}><div><strong>{s.scope}</strong><span className="mono-muted">{s.configured ? `${s.key_id} · fingerprint ${s.fingerprint}` : 'No server key configured — writes blocked'}</span></div><span className={`status-dot ${s.configured ? 'active' : 'revoked'}`} /></div>) : <div className="access-empty"><span>No scope-key status available.</span><small>Configure SYNAPSE_SCOPE_KEYS_FILE or SYNAPSE_SCOPE_KEYS_JSON on the server.</small></div>}{keys.length > 0 && <><div className="panel-title" style={{ marginTop: 18 }}><span>Agent API credentials</span></div>{keys.map(k => <div className="key-row" key={k.key_id}><div><strong>{k.agent_id}</strong><span className="mono-muted">{k.key_id} · {(k.scopes || []).join(', ') || 'no scopes'}</span></div><span className={`status-dot ${k.status}`} /><Button onClick={() => revokeKey(k.key_id)} title="Revoke agent API key"><Trash2 size={14} /></Button></div>)}</>}</div></div><div className="panel table-panel"><div className="table-head"><span>Agent identity</span><span>Dataset scopes</span><span>Issued API keys</span><span>Status</span><span /></div>{agents.length ? agents.map(a => <div className="agent-row" key={a.id}><div className="agent-main"><div className="avatar"><span>{a.initials}</span></div><div><strong>{a.id}</strong><span className="mono-muted">Agent identity</span></div></div><div className="agent-scopes">{(a.allowed_scopes || []).map((s: string) => <ScopeChip key={s} name={s} />)}</div><code>{keys.filter(k => k.agent_id === a.id && k.status === 'active').length} active</code><div className="agent-status"><StatusDot /> configured</div><span /></div>) : <div className="access-empty"><strong>No agent data loaded.</strong><span>Connect with an administrator credential above.</span></div>}</div></section>;
}
function ViewTitle({ title, subtitle, action }: { title: string; subtitle: string; action?: React.ReactNode }) { return <div className="view-header compact-header"><div className="title-row"><h1>{title}</h1><span className="divider">/</span><p>{subtitle}</p></div>{action && <div className="header-actions">{action}</div>}</div> }

function ConfigView() {
    const [unlocked, setUnlocked] = useState(false); 
    const [ontology, setOntology] = useState<string>("Loading...");

    useEffect(() => {
        apiFetch('/ontology')
            .then(res => res.json())
            .then(data => setOntology(data.content || ''))
            .catch(err => setOntology('Error loading ontology'));
    }, []);

    return <section className="view-content"><ViewTitle title="Config / Ontology" subtitle="Review Synapse rules and infrastructure health." action={<Button onClick={() => setUnlocked(!unlocked)} className={unlocked ? 'primary' : ''}>{unlocked ? <Unlock size={14} /> : <Lock size={14} />} {unlocked ? 'Editing unlocked' : 'Unlock to edit'}</Button>} /><div className="config-grid"><div className="panel code-panel"><div className="panel-title"><span><FileCode2 size={15} /> ontology.toml</span><span className="read-only">read-only</span></div><pre>{ontology}</pre></div><div className="panel settings-panel"><div className="panel-title"><span><Settings2 size={15} /> Runtime controls</span></div>{[['AES-256-GCM encryption', 'Always on', true], ['Blind index on query', 'Encrypted search', false], ['Auto revocation cascade', 'Propagates access changes', true]].map(([label, detail, on]) => <div className="toggle-row" key={String(label)}><div><strong>{label as string}</strong><span>{detail as string}</span></div><button className={`toggle ${(on || unlocked) ? 'on' : ''}`} disabled={!unlocked && !on} aria-label={label as string}><span /></button></div>)}<div className="range-row"><div><strong>Candidate set size</strong><span>Bounds blind-index recall</span></div><output>50</output></div><input type="range" min="10" max="100" defaultValue="50" disabled={!unlocked} aria-label="Candidate set size" /><div className="range-row"><div><strong>Default hop depth</strong><span>Maximum Cypher traversal</span></div><output>2</output></div></div></div><div className="infra-row"><Infra name="Neo4j" icon={<Database size={17} />} detail="bolt://localhost:7687" uptime="up 4d 12h" memory="412 MB" /><Infra name="Redis" icon={<Zap size={17} />} detail="redis://localhost:6379" uptime="up 4d 12h" memory="38 MB" /></div></section>
}
function Infra({ name, icon, detail, uptime, memory }: { name: string; icon: React.ReactNode; detail: string; uptime: string; memory: string }) { return <div className="panel infra-card"><div className="infra-icon">{icon}</div><div className="infra-title"><strong>{name}</strong><span><StatusDot /> running</span></div><code>{detail}</code><div className="infra-meta"><span>Uptime <b>{uptime}</b></span><span>Memory <b>{memory}</b></span></div></div> }

function AuditView() { 
    const [filter, setFilter] = useState('all'); 
    const [events, setEvents] = useState<any[]>([]);

    useEffect(() => {
        apiFetch('/audit')
            .then(res => res.json())
            .then(data => setEvents(data))
            .catch(err => console.error(err));
    }, []);

    const filtered = filter === 'all' ? events : events.filter((e) => e[2] === filter); 
    return <section className="view-content"><ViewTitle title="Audit Log" subtitle="Immutable event stream from the graph gateway." action={<div className="filter-row"><select value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Filter audit events"><option value="all">All event types</option><option value="traversal_step">Traversal steps</option><option value="permission_denied">Denied events</option><option value="propose_fact">Proposed facts</option></select></div>} /><div className="panel audit-panel"><div className="audit-top"><span>STREAM / GRAPH_EVENTS</span><span><StatusDot /> tailing live</span></div>{filtered.map((event, i) => <div className={`audit-row ${event[4] === 'blocked' ? 'blocked' : ''}`} key={i}><code className="audit-time">{event[0]}</code><code className="audit-agent">{event[1]}</code><span className={`event-badge ${event[2]}`}>{event[2]}</span><ScopeChip name={event[3]} /><span className="audit-result">{event[4] === 'blocked' ? <><X size={13} /> blocked</> : event[4] === 'cascade' ? <><RefreshCw size={13} /> cascade complete</> : <><Check size={13} /> allowed</>}</span></div>)}</div></section> 
}

function McpView() { 
    const [agent, setAgent] = useState('campus-bot'); 
    const [copied, setCopied] = useState(false); 
    const [status, setStatus] = useState('idle'); 
    const [fetchedAgents, setFetchedAgents] = useState<any[]>([]);

    useEffect(() => {
        apiFetch('/agents')
            .then(res => { if (!res.ok) throw new Error('Failed to fetch'); return res.json(); })
            .then(data => {
                const arr = Object.values(data).filter((a: any) => a && a.id).map((a: any) => ({ id: a.id }));
                setFetchedAgents(arr);
                if (arr.length > 0 && !arr.find((a: any) => a.id === agent)) setAgent(arr[0].id);
            })
            .catch(err => console.error(err));
    }, []);

    const config = `{"mcpServers":{"synapse":{"command":"uv","args":["run","synapse-mcp"],"env":{"AGENT_ID":"${agent}","SYNAPSE_API_KEY":"<paste-api-key>","SYNAPSE_URL":"${API_BASE}"}}}}`; 
    const copy = () => { navigator.clipboard?.writeText(config); setCopied(true); setTimeout(() => setCopied(false), 1500) }; 
    const test = async () => { setStatus('connecting'); try { const response = await apiFetch('/health'); setStatus(response.ok ? 'connected' : 'error'); } catch { setStatus('error') } }; 
    return <section className="view-content mcp-view"><ViewTitle title="MCP Setup" subtitle="Connect Claude Desktop, Cursor, or any MCP-compatible assistant." /><div className="mcp-grid"><div className="panel setup-panel"><div className="step"><span className="step-num">1</span><div><strong>Choose an agent identity</strong><span>Requests will be scoped to this identity and API key.</span></div></div><select className="agent-select" value={agent} onChange={(e) => setAgent(e.target.value)} aria-label="Choose agent identity">{fetchedAgents.map((a) => <option key={a.id}>{a.id}</option>)}</select><div className="step"><span className="step-num">2</span><div><strong>Copy the connection config</strong><span>Replace the placeholder with a real API key created by an administrator.</span></div></div><div className="config-code"><Button onClick={copy} className="copy-button">{copied ? <Check size={14} /> : <Clipboard size={14} />} {copied ? 'Copied' : 'Copy JSON'}</Button><pre>{config}</pre></div><div className="step"><span className="step-num">3</span><div><strong>Verify the gateway</strong><span>Checks backend storage health; MCP sessions still require an active partition.</span></div></div><Button onClick={test} className="primary test-button"><Play size={14} /> Test gateway</Button>{status !== 'idle' && <div className={`connection-status ${status}`}><StatusDot status={status === 'connected' ? 'active' : status === 'error' ? 'revoked' : 'pending'} /> {status === 'connected' ? 'Gateway healthy' : status === 'error' ? 'Gateway unavailable' : 'Checking gateway…'}</div>}</div><div className="mcp-aside"><div className="gradient-card"><Terminal size={20} /><span className="eyebrow">MCP transport</span><strong>Authenticated, scoped, observable.</strong><p>Tool calls use SYNAPSE_API_KEY. Activate partitions with session_activate_tool before querying knowledge.</p></div><div className="quick-links"><span className="eyebrow">Supported clients</span><div><span>Claude Desktop</span><span>Cursor</span><span>Windsurf</span></div></div></div></div></section> 
}

function CommandPalette({ onClose, onNavigate, onNodeSelect }: { onClose: () => void; onNavigate: (view: View) => void, onNodeSelect: (n: any) => void }) { 
    const [query, setQuery] = useState(''); 
    const [nodes, setNodes] = useState<any[]>([]);

    useEffect(() => {
        apiFetch('/graph/snapshot?agent_id=admin')
            .then(res => res.json())
            .then(data => {
                const nodeMap = new Map();
                data.nodes.forEach((n: any) => nodeMap.set(n.id, n));
                data.edges.forEach((e: any) => {
                    if (!nodeMap.has(e.source)) nodeMap.set(e.source, { id: e.source, type: "Unknown", scope: e.scope });
                    if (!nodeMap.has(e.target)) nodeMap.set(e.target, { id: e.target, type: "Unknown", scope: e.scope });
                });
                setNodes(Array.from(nodeMap.values()));
            })
            .catch(err => console.error(err));
    }, []);

    const baseActions = [
        { icon: Search, name: 'Query memory', subtitle: 'Run query_memory_tool', shortcut: '↵', action: () => onNavigate('Graph Explorer') }, 
        { icon: Plus, name: 'Propose a fact', subtitle: 'Run propose_fact_tool', shortcut: '⌘ ↵', action: () => onNavigate('Graph Explorer') }, 
        { icon: Bot, name: 'Jump to Agents & Access', subtitle: 'Manage identities and sessions', shortcut: '⌘ 2', action: () => onNavigate('Agents & Access') }, 
        { icon: Shield, name: 'Jump to Audit Log', subtitle: 'Review graph events', shortcut: '⌘ 4', action: () => onNavigate('Audit Log') }
    ]; 
    
    const nodeActions = nodes.map(n => ({
        icon: Network,
        name: n.id,
        subtitle: `Node • ${n.type || 'Entity'} • ${n.scope}`,
        shortcut: '↵',
        action: () => {
            onNavigate('Graph Explorer');
            onNodeSelect(n);
        }
    }));

    const actions = [...baseActions, ...nodeActions];
    const shown = actions.filter((a) => `${a.name} ${a.subtitle}`.toLowerCase().includes(query.toLowerCase())); 
    
    return <div className="palette-backdrop" onClick={onClose}><div className="palette" onClick={(e) => e.stopPropagation()}><div className="palette-input"><Search size={18} /><input autoFocus value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search commands, agents, scopes…" onKeyDown={(e) => e.key === 'Escape' && onClose()} /><Kbd>esc</Kbd></div><div className="palette-label">Actions</div><div style={{maxHeight: '300px', overflowY: 'auto'}}>{shown.map((item) => <button className="palette-item" key={item.name} onClick={() => { item.action(); onClose() }}><span className="palette-icon"><item.icon size={16} /></span><span><strong>{item.name}</strong><small>{item.subtitle}</small></span><Kbd>{item.shortcut}</Kbd><ChevronRight size={14} /></button>)}{shown.length === 0 && <div className="no-results">No commands found</div>}</div><div className="palette-footer"><span><Kbd>↑↓</Kbd> Navigate</span><span><Kbd>↵</Kbd> Select</span><span><Kbd>esc</Kbd> Close</span></div></div></div> 
}

export default function Page() { 
    const [view, setView] = useState<View>('Graph Explorer'); 
    const [palette, setPalette] = useState(false); 
    const [mobileNav, setMobileNav] = useState(false); 
    const [selectedNode, setSelectedNode] = useState<any>(null);

    useEffect(() => { 
        const handler = (e: KeyboardEvent) => { 
            if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { 
                e.preventDefault(); 
                setPalette(true) 
            } 
            if (e.key === 'Escape') setPalette(false) 
        }; 
        window.addEventListener('keydown', handler); 
        return () => window.removeEventListener('keydown', handler) 
    }, []); 

    return <main className="app-shell"><aside className={`sidebar ${mobileNav ? 'mobile-open' : ''}`}><div className="brand"><div className="brand-mark"><Network size={17} /></div><span>Krypto<span>DB</span></span><Button className="mobile-close" onClick={() => setMobileNav(false)}><X size={15} /></Button></div><div className="workspace"><span className="eyebrow">Workspace</span><button><span className="workspace-dot" /> Synapse / prod <ChevronDown size={13} /></button></div><nav><span className="nav-label">Observe</span>{nav.slice(0, 2).map(({ label, icon: Icon }) => <button key={label} className={view === label ? 'active' : ''} onClick={() => { setView(label); setMobileNav(false) }}><Icon size={16} />{label}{label === 'Graph Explorer' && <span className="nav-pulse" />}</button>)}<span className="nav-label">Configure</span>{nav.slice(2).map(({ label, icon: Icon }) => <button key={label} className={view === label ? 'active' : ''} onClick={() => { setView(label); setMobileNav(false) }}><Icon size={16} />{label}</button>)}</nav><div className="sidebar-bottom"><div className="security-note"><Shield size={15} /><div><strong>Encrypted by default</strong><span>AES-256-GCM active</span></div></div></div></aside><div className="main-area"><header className="topbar"><Button className="menu-button" onClick={() => setMobileNav(true)}><Menu size={17} /></Button><button className="global-search" onClick={() => setPalette(true)}><Search size={15} /><span>Search anything…</span><Kbd>⌘ K</Kbd></button><div className="top-actions"><div className="stream-status"><StatusDot /> <span>Live</span></div></div></header>{view === 'Graph Explorer' && <GraphExplorer selected={selectedNode} onSelect={setSelectedNode} />}{view === 'Agents & Access' && <AgentsView />}{view === 'Config / Ontology' && <ConfigView />}{view === 'Audit Log' && <AuditView />}{view === 'MCP Setup' && <McpView />}</div>{palette && <CommandPalette onClose={() => setPalette(false)} onNavigate={setView} onNodeSelect={setSelectedNode} />}</main> 
}
