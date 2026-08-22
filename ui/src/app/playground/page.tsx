'use client'
import AgentActivityModal from '../ChatPopup'

import { useEffect, useMemo, useRef, useState } from 'react'
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
    Download,
    FileCode2,
    Filter,
    Gauge,
    GitBranch,
    GripHorizontal,
    KeyRound,
    LayoutDashboard,
    Lock,
    LogOut,
    Maximize2,
    Menu,
    Move,
    Network,
    PanelRight,
    Play,
    Plus,
    RefreshCw,
    RotateCcw,
    Search,
    Send,
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
    { id: 'n1', label: 'kryptodb', scope: 'PROJECT:kryptodb', x: 470, y: 205, kind: 'project' },
    { id: 'n2', label: 'memory graph', scope: 'PROJECT:kryptodb', x: 290, y: 135, kind: 'concept' },
    { id: 'n3', label: 'cryptographic partition', scope: 'research', x: 160, y: 245, kind: 'concept' },
    { id: 'n4', label: 'Agent-1', scope: 'research', x: 300, y: 345, kind: 'agent' },
    { id: 'n5', label: 'campus-bot', scope: 'PROJECT:campus', x: 625, y: 315, kind: 'agent' },
    { id: 'n6', label: 'ontology.yaml', scope: 'PROJECT:kryptodb', x: 650, y: 145, kind: 'config' },
    { id: 'n7', label: 'AES-256-GCM', scope: 'locked', x: 790, y: 255, kind: 'secret', locked: true },
    { id: 'n8', label: 'neo4j', scope: 'PROJECT:kryptodb', x: 515, y: 405, kind: 'service' },
    { id: 'n9', label: 'redis', scope: 'locked', x: 770, y: 420, kind: 'service', locked: true },
]
const edges = [['n1', 'n2'], ['n1', 'n4'], ['n1', 'n6'], ['n1', 'n8'], ['n2', 'n3'], ['n4', 'n3'], ['n4', 'n5'], ['n5', 'n8'], ['n6', 'n7'], ['n8', 'n9']]
const scopes = ['research', 'PROJECT:kryptodb', 'PROJECT:campus', 'locked']
const agents = [
    { id: 'campus-bot', initials: 'CB', scopes: ['PROJECT:campus', 'research'], status: 'active', active: '2m ago' },
    { id: 'Agent-1', initials: 'A1', scopes: ['research'], status: 'active', active: '8m ago' },
    { id: 'claude-desktop', initials: 'CD', scopes: ['PROJECT:kryptodb'], status: 'pending', active: '22m ago' },
    { id: 'cursor-agent', initials: 'CA', scopes: ['PROJECT:kryptodb', 'research'], status: 'revoked', active: '3h ago' },
]
const auditEvents = [
    ['14:42:08.331', 'Agent-1', 'traversal_complete', 'research', 'allowed'],
    ['14:41:55.104', 'campus-bot', 'propose_fact', 'PROJECT:campus', 'allowed'],
    ['14:40:12.887', 'cursor-agent', 'permission_denied', 'locked', 'blocked'],
    ['14:39:44.010', 'Agent-1', 'traversal_complete', 'PROJECT:kryptodb', 'allowed'],
    ['14:38:01.452', 'system', 'revoke', 'PROJECT:kryptodb', 'cascade'],
    ['14:36:21.729', 'campus-bot', 'traversal_complete', 'research', 'allowed'],
]

function StatusDot({ status = 'active' }: { status?: string }) { return <span className={`status-dot ${status}`} aria-label={status} /> }
function Kbd({ children }: { children: React.ReactNode }) { return <span className="kbd">{children}</span> }
function ScopeChip({ name }: { name: string }) { return <span className={`scope-chip scope-${name.toLowerCase().replace(/[^a-z]/g, '-')}`}><span className="scope-mark" />{name}</span> }
function Button({ children, className = '', onClick, title, style }: { children: React.ReactNode; className?: string; onClick?: () => void; title?: string; style?: React.CSSProperties }) { return <button title={title} onClick={onClick} style={style} className={`ui-button ${className}`}>{children}</button> }

import dynamic from 'next/dynamic'
const LiveGraph = dynamic(() => import('../GraphExplorer'), { ssr: false })

const API_BASE = process.env.NEXT_PUBLIC_KRYPTODB_URL || (typeof window !== 'undefined' ? `http://${window.location.hostname}:8000` : 'http://127.0.0.1:8000')

function apiFetch(path: string, init: RequestInit = {}) {
    const apiKey = typeof window !== 'undefined' ? (window.localStorage.getItem('kryptodb_api_key') || 'dev-admin-key') : 'dev-admin-key'
    const headers = new Headers(init.headers)
    if (apiKey) headers.set('Authorization', `Bearer ${apiKey}`)
    return fetch(`${API_BASE}${path}`, { ...init, headers })
}

type BackendAgent = {
    id: string
    allowed_scopes: string[]
    trust_prior?: number
    initials: string
}

function normalizeAgents(data: unknown): BackendAgent[] {
    const records = Array.isArray(data) ? data : Object.values((data || {}) as Record<string, any>)
    const seen = new Set<string>()
    return records
        .filter((agent: any) => agent && typeof agent.id === 'string' && agent.id.trim())
        .map((agent: any) => ({
            ...agent,
            id: agent.id.trim(),
            allowed_scopes: Array.isArray(agent.allowed_scopes) ? agent.allowed_scopes.filter(Boolean) : [],
            initials: agent.id.trim().slice(0, 2).toUpperCase(),
        }))
        .filter((agent: BackendAgent) => !seen.has(agent.id) && seen.add(agent.id))
}

function Inspector({ node, onClose }: { node: any | null; onClose: () => void }) {
    if (!node) return null;
    return <aside className="inspector">
        <div className="inspector-head"><div><span className="eyebrow">Node inspector</span><h2>{node.label || node.id}</h2></div><Button title="Close inspector" onClick={onClose}><X size={15} /></Button></div>
        <div className="inspector-state">{node.is_encrypted ? <><Shield size={16} /><div><strong>Encrypted knowledge</strong><span>Content protected by AES-GCM{node.is_pending ? ' (with some pending connections)' : ''}</span></div></> : node.is_pending ? <><Unlock size={16} /><div><strong>Unencrypted knowledge</strong><span>Pending scope-key migration</span></div></> : <><Unlock size={16} /><div><strong>Metadata only</strong><span>No encrypted knowledge envelope detected</span></div></>}</div>
        <div className="field-list"><div><span>node_id</span><code>{node.id}</code></div><div><span>type</span><code>{node.kind || node.type || 'Unknown'}</code></div><div><span>scope</span><ScopeChip name={node.scope || 'public'} /></div><div><span>valid_to</span><code>∞ active</code></div></div>
        <div className="inspector-section">
            <span className="eyebrow">Properties</span>
            {Object.entries(node).filter(([k]) => !['x', 'y', 'vx', 'vy', 'fx', 'fy', 'index', '__bckgDimensions', 'lastTraversedTime', 'label', 'kind', '__indexColor'].includes(k)).map(([k, v]) => (
                <div className="edge-row" key={k}>
                    <GitBranch size={14} />
                    <code>{k}: {String(v)}</code>
                </div>
            ))}
        </div>
    </aside>
}

function GraphExplorer({ selected, onSelect }: any) {
    return <section className="view-content graph-view">
        <div className="view-header compact-header">
            <div className="title-row">
                <h1>Graph Explorer</h1>
                <span className="divider">/</span>
                <p>Trace encrypted memory across KryptoDB partitions.</p>
            </div>
        </div>
        <div className="stats-strip">
            <div><span>Nodes</span><strong style={{ fontVariantNumeric: 'tabular-nums' }}>Live</strong></div>
            <div><span>Edges</span><strong style={{ fontVariantNumeric: 'tabular-nums' }}>Live</strong></div>
            <div><span>Last traversal</span><strong style={{ fontVariantNumeric: 'tabular-nums' }}>--<span className="unit">ms</span></strong></div>
            <div className="stats-status"><StatusDot /> <span>WebSocket stream healthy</span></div>
        </div>
        <div className="graph-layout">
            <LiveGraph selected={selected} onSelect={onSelect} />
            <Inspector node={selected} onClose={() => onSelect(null)} />
        </div>
    </section>
}

function AgentsViewLegacy() {
    const [open, setOpen] = useState<string | null>(null);
    const [fetchedAgents, setFetchedAgents] = useState<any[]>([]);
    const [apiKey, setApiKey] = useState(() => typeof window !== 'undefined' ? window.localStorage.getItem('kryptodb_api_key') || '' : '');
    const [saved, setSaved] = useState(false);

    useEffect(() => {
        apiFetch('/agents')
            .then(res => { if (!res.ok) throw new Error('Failed to fetch'); return res.json(); })
            .then(data => {
                const arr = normalizeAgents(data).map(a => ({
                    id: a.id,
                    initials: a.initials,
                    scopes: a.allowed_scopes,
                    status: 'active',
                    active: 'available'
                }));
                setFetchedAgents(arr);
            })
            .catch(err => console.error(err));
    }, []);

    const saveKey = () => { window.localStorage.setItem('kryptodb_api_key', apiKey.trim()); setSaved(true); setTimeout(() => setSaved(false), 1500) }
    return <section className="view-content"><ViewTitle title="Agents & Access" subtitle="Manage agent sessions and partition boundaries." /><div className="panel setup-panel" style={{ marginBottom: 16 }}><span className="eyebrow">Backend authentication</span><p className="mono-muted">Enter an admin API key to load agents and manage access. Secrets stay in this browser only.</p><div style={{ display: 'flex', gap: 8 }}><input type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder="syn_live_…" aria-label="KryptoDB API key" style={{ flex: 1 }} /><Button onClick={saveKey} className="primary">{saved ? 'Saved' : 'Save key'}</Button></div></div><div className="panel table-panel"><div className="table-head"><span>Agent identity</span><span>Allowed scopes</span><span>Status</span><span>Last active</span><span /></div>{fetchedAgents.map((agent) => <div key={agent.id} className={`agent-row ${open === agent.id ? 'row-open' : ''}`}><div className="agent-main" onClick={() => setOpen(open === agent.id ? null : agent.id)}><div className="avatar">{agent.initials}</div><div><strong>{agent.id}</strong><span className="mono-muted">API identity</span></div></div><div className="agent-scopes">{agent.scopes.map((s: string) => <ScopeChip key={s} name={s} />)}</div><div className="agent-status"><StatusDot status={agent.status} /> {agent.status}</div><code>{agent.active}</code><Button onClick={() => setOpen(open === agent.id ? null : agent.id)} title="Expand agent"><ChevronDown size={15} className={open === agent.id ? 'rotate' : ''} /></Button>{open === agent.id && <div className="agent-details"><div><span className="eyebrow">MCP transport</span><code>stdio → {API_BASE}</code></div><div className="secret-line"><span>Credential</span><code>{apiKey ? 'Bearer key configured' : 'No API key configured'}</code></div><div className="detail-actions"><span className="mono-muted">Create and revoke secrets through the authenticated admin API.</span></div></div>}</div>)}</div></section>
}
function AgentsView() {
    const [agents, setAgents] = useState<any[]>([]);
    const [keys, setKeys] = useState<any[]>([]);
    const [scopeKeys, setScopeKeys] = useState<any[]>([]);
    const [pendingStats, setPendingStats] = useState<any>(null);
    const [apiKey, setApiKey] = useState(() => typeof window !== 'undefined' ? window.localStorage.getItem('kryptodb_api_key') || '' : '');
    const [agentId, setAgentId] = useState('');
    const [scopes, setScopes] = useState('');
    const [notice, setNotice] = useState('');
    const [adminReady, setAdminReady] = useState(false);

    // Dataset setup state
    const [datasetName, setDatasetName] = useState('');
    const [encryptionMode, setEncryptionMode] = useState('new');
    const [selectedScope, setSelectedScope] = useState('');

    // Migration state
    const [migrationResult, setMigrationResult] = useState<any>(null);
    const [legacyNodeId, setLegacyNodeId] = useState('');
    const [legacyNodeScope, setLegacyNodeScope] = useState('');

    const load = () => Promise.all([
        apiFetch('/agents').then(r => r.ok ? r.json() : Promise.reject(new Error('Admin API key required'))),
        apiFetch('/admin/api-keys').then(r => r.ok ? r.json() : Promise.reject(new Error('Admin API key required'))),
        apiFetch('/admin/scope-keys').then(r => r.ok ? r.json() : Promise.reject(new Error('Admin API key required'))),
        apiFetch('/maintenance/migration-preflight').then(r => r.ok ? r.json() : { total_pending: 0 }),
    ]).then(([agentData, keyData, scopeData, preflightData]) => {
        setAdminReady(true);
        const list = normalizeAgents(agentData);
        setAgents(list); setKeys(keyData.keys || []); setScopeKeys(scopeData.keys || []);
        setPendingStats(preflightData);
        if (!agentId && list[0]) { setAgentId(list[0].id); setScopes((list[0].allowed_scopes || []).join(', ')); }
    }).catch(e => { setAdminReady(false); setNotice(e.message); });

    useEffect(() => { load(); }, []);

    const saveKey = () => { window.localStorage.setItem('kryptodb_api_key', apiKey.trim()); setNotice('Credential saved in this browser.'); load(); };
    const scopeList = () => scopes.split(',').map(s => s.trim()).filter(Boolean);
    const selectAgent = (id: string) => {
        const a = agents.find(item => item.id === id);
        setAgentId(id);
        setScopes((a?.allowed_scopes || []).join(', '));
        if (typeof window !== 'undefined') {
            window.localStorage.setItem('kryptodb_active_agent', id);
            window.dispatchEvent(new Event('kryptodb_active_agent_changed'));
        }
    };
    const updateScopes = async () => { const r = await apiFetch('/admin/agents', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ agent_id: agentId, scopes: scopeList() }) }); setNotice(r.ok ? `Scopes updated for ${agentId}.` : 'Could not update scopes.'); load(); };
    const createKey = async () => { const r = await apiFetch('/admin/api-keys', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ agent_id: agentId, scopes: scopeList() }) }); const data = await r.json(); setNotice(r.ok ? `Copy this key now: ${data.api_key}` : (data.detail || 'Could not create key.')); load(); };
    const revokeKey = async (id: string) => { const r = await apiFetch(`/admin/api-keys/${id}/revoke`, { method: 'POST' }); setNotice(r.ok ? `Revoked ${id}.` : 'Could not revoke key.'); load(); };
    const prepareDataset = async () => {
        if (!datasetName.trim() || !selectedScope.trim()) return setNotice('Dataset name and scope are required.');
        const r = await apiFetch('/admin/datasets/prepare', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ dataset_id: datasetName.trim(), scope: selectedScope.trim(), mode: encryptionMode }) });
        const data = await r.json();
        setNotice(r.ok ? `Dataset ${data.dataset_id} prepared under ${data.scope} (${data.status}).` : (data.detail || 'Could not prepare dataset.'));
    };
    const encryptLegacyNode = async () => {
        if (!legacyNodeId.trim() || !legacyNodeScope.trim()) return setNotice('Node ID and target scope are required.');
        const r = await apiFetch('/reclassify', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ node_id: legacyNodeId.trim(), to_scope: legacyNodeScope.trim() }) });
        const data = await r.json();
        setNotice(r.ok ? `Legacy node encrypted under ${data.to_scope}.` : (data.detail || 'Could not encrypt legacy node.'));
    };

    const migrateLegacy = async (migrationScopes: string[]) => {
        const r = await apiFetch('/maintenance/migrate-legacy', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ scopes: migrationScopes, lock: true }) });
        const data = await r.json();
        setMigrationResult(data);
        load();
    };

    const generateScopeKey = (scope: string) => {
        if (!scope) return;
        const raw = crypto.getRandomValues(new Uint8Array(32));
        const b64 = btoa(String.fromCharCode(...raw));
        const keyId = `${scope}:v1`;
        const content = { [scope]: { key_id: keyId, key: b64 } };
        const blob = new Blob([JSON.stringify(content, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `kryptodb-scope-key-${scope}.json`;
        a.click();
        URL.revokeObjectURL(url);
        setNotice(`Key for ${scope} downloaded. Merge into KRYPTODB_SCOPE_KEYS_FILE and restart to apply.`);
    };

    const useSuppliedKey = () => {
        const input = document.createElement('input');
        input.type = 'file';
        input.accept = 'application/json,.json';
        input.onchange = () => {
            const file = input.files?.[0];
            if (!file) return;
            const reader = new FileReader();
            reader.onload = () => {
                try {
                    const parsed = JSON.parse(String(reader.result));
                    if (!parsed || typeof parsed !== 'object' || !Object.values(parsed).every((v: any) => v?.key_id && v?.key)) throw new Error('Expected scope key JSON');
                    const blob = new Blob([JSON.stringify(parsed, null, 2)], { type: 'application/json' });
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = url;
                    a.download = 'kryptodb-scope-keys.json';
                    a.click();
                    URL.revokeObjectURL(url);
                    setNotice('Scope key file validated. Mount this file as KRYPTODB_SCOPE_KEYS_FILE and restart the server.');
                } catch (error) {
                    setNotice(error instanceof Error ? error.message : 'Invalid scope key file');
                }
            };
            reader.readAsText(file);
        };
        input.click();
    };

    const isBootstrap = pendingStats && (pendingStats.total_pending > 0 || pendingStats.invalid_records > 0);

    if (!adminReady) return (
        <section className="view-content">
            <ViewTitle title="Agents & Access" subtitle="Configure agent permissions for dataset scopes." />
            <div className="panel setup-panel admin-connect-card">
                <span className="eyebrow">Administrator connection</span>
                <h2>Connect to manage access</h2>
                <p className="mono-muted">This page manages agent permissions and API credentials. Dataset encryption keys stay configured on the backend and are never entered here.</p>
                <div className="admin-connect-row">
                    <input type="password" value={apiKey} onChange={e => setApiKey(e.target.value)} placeholder="Paste KRYPTODB_ADMIN_API_KEY" aria-label="Administrator API key" />
                    <Button onClick={saveKey} className="primary">Connect admin</Button>
                </div>
                {notice && <p className="auth-error">{notice}</p>}
                <span className="mono-muted">An agent/MCP key cannot configure users or scopes. Raw dataset encryption keys never leave the daemon.</span>
            </div>
        </section>
    );

    return (
        <section className="view-content" style={{ maxWidth: 800 }}>
            <ViewTitle title="Agents & Access" subtitle="Control which dataset scopes each agent can use." />

            <div className="live-agents-panel">
                <div className="live-agents-heading">
                    <div>
                        <span className="eyebrow">Live agent identities</span>
                        <h2>{agents.length} available through the gateway</h2>
                    </div>
                    <span className="live-agents-state"><StatusDot /> backend synced</span>
                </div>
                <div className="live-agents-grid">
                    {agents.map(agent => (
                        <button key={agent.id} className={`live-agent-card ${agentId === agent.id ? 'selected' : ''}`} onClick={() => selectAgent(agent.id)}>
                            <span className="avatar">{agent.initials}</span>
                            <span className="live-agent-copy">
                                <strong>{agent.id}</strong>
                                <small>{agent.allowed_scopes?.length || 0} authorized scope{agent.allowed_scopes?.length === 1 ? '' : 's'}</small>
                            </span>
                            <span className="live-agent-check"><StatusDot /></span>
                        </button>
                    ))}
                    {agents.length === 0 && <div className="live-agents-empty">No agent identities were returned by the gateway.</div>}
                </div>
            </div>

            {/* Administrator connection */}
            <div className="panel setup-panel" style={{ marginBottom: 16 }}>
                <span className="eyebrow">Administrator credential</span>
                <div style={{ display: 'flex', gap: 8 }}>
                    <input type="password" value={apiKey} onChange={e => setApiKey(e.target.value)} placeholder="Paste KRYPTODB_ADMIN_API_KEY" aria-label="Administrator API key" style={{ flex: 1 }} />
                    <Button onClick={saveKey} className="primary">Connect admin</Button>
                </div>
                {notice && <p className="mono-muted" style={{ marginTop: 10, color: '#9dd9b0' }}>{notice}</p>}
            </div>

            {/* 3. Dataset setup */}
            <div className="panel setup-panel" style={{ marginBottom: 16 }}>
                <span className="eyebrow">Dataset setup</span>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 12, marginTop: 12 }}>
                    <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
                        <label style={{ width: 140 }}>Dataset name:</label>
                        <input value={datasetName} onChange={e => setDatasetName(e.target.value)} placeholder="Patient Records" style={{ flex: 1 }} />
                    </div>
                    <div style={{ display: 'flex', gap: 12 }}>
                        <label style={{ width: 140 }}>Encryption mode:</label>
                        <div>
                            <label style={{ display: 'block', marginBottom: 4 }}><input type="radio" checked={encryptionMode === 'new'} onChange={() => setEncryptionMode('new')} /> Create new encryption scope</label>
                            <label style={{ display: 'block' }}><input type="radio" checked={encryptionMode === 'existing'} onChange={() => setEncryptionMode('existing')} /> Use existing encryption scope</label>
                        </div>
                    </div>
                    <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
                        <label style={{ width: 140 }}>{encryptionMode === 'new' ? 'New scope:' : 'Existing scope:'}</label>
                        {encryptionMode === 'new' ? (
                            <input value={selectedScope} onChange={e => setSelectedScope(e.target.value)} placeholder="diagnosis" style={{ flex: 1 }} />
                        ) : (
                            <select value={selectedScope} onChange={e => setSelectedScope(e.target.value)} style={{ flex: 1 }}>
                                <option value="">Select scope</option>
                                {scopeKeys.map(s => <option key={s.scope} value={s.scope}>{s.scope}</option>)}
                            </select>
                        )}
                    </div>
                    <div style={{ display: 'flex', gap: 8, marginTop: 4 }}>
                        <Button onClick={() => generateScopeKey(selectedScope)}>Generate scope key</Button>
                        <Button onClick={useSuppliedKey}>Validate supplied key file</Button>
                        <Button onClick={prepareDataset} className="primary">Prepare dataset</Button>
                    </div>

                    {selectedScope && (
                        <div style={{ padding: 12, background: 'rgba(255,255,255,0.05)', borderRadius: 4, marginTop: 12 }}>
                            {encryptionMode === 'existing' ? (
                                <>
                                    <strong>Existing scope:</strong> {selectedScope}<br />
                                    <strong>Key ID:</strong> {scopeKeys.find(s => s.scope === selectedScope)?.key_id || 'Unknown'}<br />
                                    <strong>Key status:</strong> {scopeKeys.find(s => s.scope === selectedScope)?.configured ? 'Configured' : 'Missing'}<br />
                                    <strong>Agents with access:</strong> {agents.filter(a => (a.allowed_scopes || []).includes(selectedScope)).map(a => a.id).join(', ') || 'None'}
                                </>
                            ) : (
                                <>
                                    <strong>New scope:</strong> {selectedScope}<br />
                                    <strong>Key status:</strong> Not configured<br />
                                    <strong>Action required:</strong> Export key and configure backend secret
                                </>
                            )}
                        </div>
                    )}
                </div>
            </div>

            {/* 4. Configure Scope Key */}
            <div className="panel setup-panel" style={{ marginBottom: 16 }}>
                <span className="eyebrow">Configure Scope Key</span>
                <p className="mono-muted">Fingerprint and explicitly configure a scope key for the backend dynamically.</p>
                <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
                    <input value={legacyNodeScope} onChange={e => setLegacyNodeScope(e.target.value)} placeholder="Scope (e.g., ORG:acme-structure)" aria-label="Scope" style={{ flex: 1 }} />
                    <input type="password" value={legacyNodeId} onChange={e => setLegacyNodeId(e.target.value)} placeholder="Base64 Key" aria-label="Base64 Key" style={{ flex: 2 }} />
                    <Button onClick={async () => {
                        if (!legacyNodeScope.trim() || !legacyNodeId.trim()) return setNotice('Scope and Key are required.');
                        const r = await apiFetch('/admin/scope-keys/configure', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ scope: legacyNodeScope.trim(), key_id: `${legacyNodeScope.trim()}:v1`, key_base64: legacyNodeId.trim() }) });
                        const data = await r.json();
                        setNotice(r.ok ? `Scope key configured for ${data.scope}.` : (data.detail || 'Could not configure scope key.'));
                        load();
                        setLegacyNodeId('');
                    }} className="primary">Configure key</Button>
                </div>
            </div>

            {/* 5. Scope-key management */}
            <div className="panel setup-panel" style={{ marginBottom: 16 }}>
                <span className="eyebrow">Scope-key management</span>
                <table style={{ width: '100%', marginTop: 12, textAlign: 'left', borderCollapse: 'collapse', fontSize: '0.9em' }}>
                    <thead>
                        <tr style={{ borderBottom: '1px solid #333', paddingBottom: 8 }}>
                            <th style={{ padding: '8px 0' }}>Scope</th>
                            <th style={{ padding: '8px 0' }}>Key ID</th>
                            <th style={{ padding: '8px 0' }}>Fingerprint</th>
                            <th style={{ padding: '8px 0' }}>Status</th>
                        </tr>
                    </thead>
                    <tbody>
                        {scopeKeys.map(s => (
                            <tr key={s.scope} style={{ borderBottom: '1px solid #222' }}>
                                <td style={{ padding: '8px 0' }}>{s.scope}</td>
                                <td style={{ padding: '8px 0' }}>{s.key_id || 'N/A'}</td>
                                <td style={{ padding: '8px 0' }} className="mono-muted">{s.fingerprint || 'N/A'}</td>
                                <td style={{ padding: '8px 0' }}>{s.configured ? 'Configured' : 'Missing'}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
                <div style={{ marginTop: 16 }}>
                    <Button onClick={() => generateScopeKey(`new-scope-${Date.now()}`)}>Generate new key</Button>
                </div>
            </div>

            {/* 5. Agent permissions */}
            <div className="panel setup-panel" style={{ marginBottom: 16 }}>
                <span className="eyebrow">Agent permissions</span>
                <blockquote style={{ borderLeft: '4px solid #1890ff', paddingLeft: 12, color: '#1890ff', marginTop: 12, marginBottom: 16 }}>
                    Scopes grant access to backend encryption keys. Agents never receive raw encryption keys.
                </blockquote>

                <div style={{ display: 'flex', flexDirection: 'column', gap: 12, marginBottom: 24 }}>
                    {agents.map(a => (
                        <div key={a.id}>
                            <strong>{a.id}</strong><br />
                            <span className="mono-muted" style={{ fontSize: '0.9em' }}>Allowed dataset scopes:</span>
                            <div style={{ display: 'flex', gap: 4, marginTop: 4 }}>
                                {(a.allowed_scopes || []).length > 0 ? (
                                    (a.allowed_scopes || []).map((s: string) => <ScopeChip key={s} name={s} />)
                                ) : (
                                    <span className="mono-muted" style={{ fontSize: '0.85em' }}>None</span>
                                )}
                            </div>
                        </div>
                    ))}
                </div>

                <div style={{ display: 'flex', flexDirection: 'column', gap: 12, padding: 16, background: 'rgba(255,255,255,0.03)', borderRadius: 6 }}>
                    <strong>Manage Permissions & Credentials</strong>
                    <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
                        <select
                            style={{ flex: 1, padding: '8px 12px', background: '#141722', border: '1px solid rgba(255,255,255,0.15)', borderRadius: '6px', color: '#fff', fontSize: '0.9em' }}
                            value={agentId}
                            onChange={e => selectAgent(e.target.value)}
                        >
                            {agents.map(a => (
                                <option key={a.id} value={a.id} style={{ background: '#141722', color: '#fff' }}>
                                    {a.id} {(a.allowed_scopes || []).length > 0 ? `(scopes: ${(a.allowed_scopes || []).join(', ')})` : ''}
                                </option>
                            ))}
                        </select>
                    </div>
                    <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
                        <label style={{ width: 100 }}>Scopes: </label>
                        <input value={scopes} onChange={e => setScopes(e.target.value)} placeholder="diagnosis, patients" style={{ flex: 1 }} />
                    </div>
                    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 8 }}>
                        <Button onClick={updateScopes} className="primary">Save permissions</Button>
                        <Button onClick={createKey}>Issue agent API key</Button>
                        <Button onClick={() => setNotice('View active sessions not fully implemented.')}>View active sessions</Button>
                    </div>

                    {keys.filter(k => k.agent_id === agentId).length > 0 && (
                        <div style={{ marginTop: 12 }}>
                            <strong style={{ fontSize: '0.9em' }}>Active Keys for {agentId}:</strong>
                            <div style={{ display: 'flex', flexDirection: 'column', gap: 4, marginTop: 8 }}>
                                {keys.filter(k => k.agent_id === agentId).map(k => (
                                    <div key={k.key_id} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: '0.9em', padding: 8, background: 'rgba(0,0,0,0.2)', borderRadius: 4 }}>
                                        <span className="mono-muted">{k.key_id} ({k.status})</span>
                                        <Button onClick={() => revokeKey(k.key_id)}><Trash2 size={12} /></Button>
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}
                </div>
            </div>

            {/* 6. Pending-data migration */}
            {isBootstrap && (
                <div className="panel setup-panel" style={{ marginBottom: 16 }}>
                    <span className="eyebrow">Pending knowledge migration</span>

                    <div style={{ marginTop: 12, marginBottom: 16 }}>
                        {Object.entries(pendingStats?.pending_counts || {}).map(([scope, count]) => (
                            <div key={scope} style={{ display: 'flex', justifyContent: 'space-between', maxWidth: 300, marginBottom: 4 }}>
                                <span>{scope}</span><span className="mono-muted">{count as number} records</span>
                            </div>
                        ))}
                        {Object.keys(pendingStats?.pending_counts || {}).length === 0 && (
                            <span className="mono-muted">No pending records found per scope.</span>
                        )}
                    </div>

                    <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
                        <Button onClick={load}>Run migration preflight</Button>
                        <Button onClick={() => {
                            if (confirm("This will encrypt all pending knowledge and disable unencrypted bootstrap writes.")) {
                                migrateLegacy(Object.keys(pendingStats.pending_counts));
                            }
                        }} className="primary" style={{ background: '#ff4d4f', color: '#fff', borderColor: '#ff4d4f' }}>Migrate and lock encryption</Button>
                    </div>

                    <div style={{ padding: 12, background: 'rgba(0,0,0,0.3)', borderRadius: 4, fontSize: '0.9em' }}>
                        <strong>Preflight details:</strong>
                        <ul style={{ margin: '8px 0 0 16px', padding: 0 }}>
                            <li>Missing keys: {pendingStats?.missing_keys?.length || 0}</li>
                            <li>Invalid records: {pendingStats?.invalid_records || 0}</li>
                            <li>Records already encrypted: {pendingStats?.total_encrypted || 0}</li>
                            <li>Records still unprotected: {pendingStats?.total_pending || 0}</li>
                        </ul>
                    </div>
                </div>
            )}

            {/* 7. Migration result */}
            {migrationResult && (
                <div className="panel setup-panel" style={{ marginBottom: 16, border: migrationResult.failed > 0 ? '1px solid #ff4d4f' : '1px solid #52c41a' }}>
                    <span className="eyebrow" style={{ color: migrationResult.failed > 0 ? '#ff4d4f' : '#52c41a' }}>
                        {migrationResult.failed > 0 ? 'Migration incomplete' : 'Migration complete'}
                    </span>
                    <div style={{ marginTop: 12 }}>
                        Encrypted: {migrationResult.migrated}<br />
                        Failed: {migrationResult.failed}<br />
                        Remaining unencrypted: {pendingStats?.total_pending || 0}<br /><br />
                        <strong>Bootstrap mode: {migrationResult.failed > 0 || (pendingStats?.total_pending || 0) > 0 ? 'enabled' : 'Disabled'}</strong>
                    </div>
                </div>
            )}

        </section>
    );
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

    return <section className="view-content"><ViewTitle title="Config / Ontology" subtitle="Review KryptoDB rules and infrastructure health." action={<Button onClick={() => setUnlocked(!unlocked)} className={unlocked ? 'primary' : ''}>{unlocked ? <Unlock size={14} /> : <Lock size={14} />} {unlocked ? 'Editing unlocked' : 'Unlock to edit'}</Button>} /><div className="config-grid"><div className="panel code-panel"><div className="panel-title"><span><FileCode2 size={15} /> ontology.toml</span><span className="read-only">read-only</span></div><pre>{ontology}</pre></div><div className="panel settings-panel"><div className="panel-title"><span><Settings2 size={15} /> Runtime controls</span></div>{[['AES-256-GCM encryption', 'Always on', true], ['Blind index on query', 'Encrypted search', false], ['Auto revocation cascade', 'Propagates access changes', true]].map(([label, detail, on]) => <div className="toggle-row" key={String(label)}><div><strong>{label as string}</strong><span>{detail as string}</span></div><button className={`toggle ${(on || unlocked) ? 'on' : ''}`} disabled={!unlocked && !on} aria-label={label as string}><span /></button></div>)}<div className="range-row"><div><strong>Candidate set size</strong><span>Bounds blind-index recall</span></div><output>50</output></div><input type="range" min="10" max="100" defaultValue="50" disabled={!unlocked} aria-label="Candidate set size" /><div className="range-row"><div><strong>Default hop depth</strong><span>Maximum Cypher traversal</span></div><output>2</output></div></div></div><div className="infra-row"><Infra name="Neo4j" icon={<Database size={17} />} detail="bolt://localhost:7687" uptime="up 4d 12h" memory="412 MB" /><Infra name="Redis" icon={<Zap size={17} />} detail="redis://localhost:6379" uptime="up 4d 12h" memory="38 MB" /></div></section>
}
function Infra({ name, icon, detail, uptime, memory }: { name: string; icon: React.ReactNode; detail: string; uptime: string; memory: string }) { return <div className="panel infra-card"><div className="infra-icon">{icon}</div><div className="infra-title"><strong>{name}</strong><span><StatusDot /> running</span></div><code>{detail}</code><div className="infra-meta"><span>Uptime <b>{uptime}</b></span><span>Memory <b>{memory}</b></span></div></div> }

function AuditView() {
    const [filter, setFilter] = useState('all');
    const [events, setEvents] = useState<any[]>([]);

    useEffect(() => {
        apiFetch('/audit')
            .then(res => res.ok ? res.json() : [])
            .then(data => setEvents(Array.isArray(data) ? data : []))
            .catch(err => console.error(err));
    }, []);

    const filtered = filter === 'all' ? events : events.filter((e) => e[2] === filter);
    return <section className="view-content"><ViewTitle title="Audit Log" subtitle="Immutable event stream from the graph gateway." action={<div className="filter-row"><select value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Filter audit events"><option value="all">All event types</option><option value="traversal_complete">Completed traversals</option><option value="permission_denied">Denied events</option><option value="propose_fact">Proposed facts</option></select></div>} /><div className="panel audit-panel"><div className="audit-top"><span>STREAM / GRAPH_EVENTS</span><span><StatusDot /> tailing live</span></div>{filtered.map((event, i) => <div className={`audit-row ${event[4] === 'blocked' ? 'blocked' : ''}`} key={i}><code className="audit-time">{event[0]}</code><code className="audit-agent">{event[1]}</code><span className={`event-badge ${event[2]}`}>{event[2]}</span><ScopeChip name={event[3]} /><span className="audit-result">{event[4] === 'blocked' ? <><X size={13} /> blocked</> : event[4] === 'cascade' ? <><RefreshCw size={13} /> cascade complete</> : <><Check size={13} /> allowed</>}</span></div>)}</div></section>
}

function McpView() {
    const [agent, setAgent] = useState('campus-bot');
    const [copied, setCopied] = useState(false);
    const [status, setStatus] = useState('idle');
    const [fetchedAgents, setFetchedAgents] = useState<any[]>([]);

    useEffect(() => {
        apiFetch('/agents')
            .then(res => res.ok ? res.json() : {})
            .then(data => {
                const arr = Object.values(data || {}).filter((a: any) => a && a.id).map((a: any) => ({ id: a.id }));
                setFetchedAgents(arr);
                if (arr.length > 0 && !arr.find((a: any) => a.id === agent)) setAgent(arr[0].id);
            })
            .catch(err => console.warn('Could not fetch agents:', err));
    }, []);

    const config = `{"mcpServers":{"kryptodb":{"command":"uv","args":["run","kryptodb-mcp"],"env":{"AGENT_ID":"${agent}","KRYPTODB_API_KEY":"<paste-api-key>","KRYPTODB_URL":"${API_BASE}"}}}}`;
    const copy = () => { navigator.clipboard?.writeText(config); setCopied(true); setTimeout(() => setCopied(false), 1500) };
    const test = async () => { setStatus('connecting'); try { const response = await apiFetch('/health'); setStatus(response.ok ? 'connected' : 'error'); } catch { setStatus('error') } };
    return <section className="view-content mcp-view"><ViewTitle title="MCP Setup" subtitle="Connect Claude Desktop, Cursor, or any MCP-compatible assistant." /><div className="mcp-grid"><div className="panel setup-panel"><div className="step"><span className="step-num">1</span><div><strong>Choose an agent identity</strong><span>Requests will be scoped to this identity and API key.</span></div></div><div><select className="agent-select" style={{ width: '100%', padding: '8px 12px', background: '#141722', border: '1px solid rgba(255,255,255,0.15)', borderRadius: '6px', color: '#fff' }} value={agent} onChange={(e) => { setAgent(e.target.value); if (typeof window !== 'undefined') { window.localStorage.setItem('kryptodb_active_agent', e.target.value); window.dispatchEvent(new Event('kryptodb_active_agent_changed')); } }} aria-label="Choose agent identity">{fetchedAgents.map((a) => <option key={a.id} value={a.id} style={{ background: '#141722', color: '#fff' }}>{a.id}</option>)}</select></div><div className="step"><span className="step-num">2</span><div><strong>Copy the connection config</strong><span>Replace the placeholder with a real API key created by an administrator.</span></div></div><div className="config-code"><Button onClick={copy} className="copy-button">{copied ? <Check size={14} /> : <Clipboard size={14} />} {copied ? 'Copied' : 'Copy JSON'}</Button><pre>{config}</pre></div><div className="step"><span className="step-num">3</span><div><strong>Verify the gateway</strong><span>Checks backend storage health; MCP sessions still require an active partition.</span></div></div><Button onClick={test} className="primary test-button"><Play size={14} /> Test gateway</Button>{status !== 'idle' && <div className={`connection-status ${status}`}><StatusDot status={status === 'connected' ? 'active' : status === 'error' ? 'revoked' : 'pending'} /> {status === 'connected' ? 'Gateway healthy' : status === 'error' ? 'Gateway unavailable' : 'Checking gateway…'}</div>}</div><div className="mcp-aside"><div className="gradient-card"><Terminal size={20} /><span className="eyebrow">MCP transport</span><strong>Authenticated, scoped, observable.</strong><p>Tool calls use KRYPTODB_API_KEY. Activate partitions with session_activate_tool before querying knowledge.</p></div><div className="quick-links"><span className="eyebrow">Supported clients</span><div><span>Claude Desktop</span><span>Cursor</span><span>Windsurf</span></div></div></div></div></section>
}

function CommandPalette({ onClose, onNavigate, onNodeSelect }: { onClose: () => void; onNavigate: (view: View) => void, onNodeSelect: (n: any) => void }) {
    const [query, setQuery] = useState('');
    const [nodes, setNodes] = useState<any[]>([]);

    useEffect(() => {
        apiFetch('/graph/snapshot?agent_id=admin')
            .then(res => res.ok ? res.json() : { nodes: [], edges: [] })
            .then(data => {
                if (!data.nodes || !data.edges) return;
                const nodeMap = new Map();
                data.nodes.forEach((n: any) => nodeMap.set(n.id, n));
                data.edges.forEach((e: any) => {
                    if (!nodeMap.has(e.source)) nodeMap.set(e.source, { id: e.source, type: "Unknown", scope: e.scope, is_encrypted: false });
                    if (!nodeMap.has(e.target)) nodeMap.set(e.target, { id: e.target, type: "Unknown", scope: e.scope, is_encrypted: false });
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
        subtitle: `${n.is_encrypted ? 'Encrypted' : 'Plaintext'} Node • ${n.type || 'Entity'} • ${n.scope}`,
        shortcut: '↵',
        action: () => {
            onNavigate('Graph Explorer');
            onNodeSelect(n);
        }
    }));

    const actions = [...baseActions, ...nodeActions];
    const shown = actions.filter((a) => `${a.name} ${a.subtitle}`.toLowerCase().includes(query.toLowerCase()));

    return <div className="palette-backdrop" onClick={onClose}><div className="palette" onClick={(e) => e.stopPropagation()}><div className="palette-input"><Search size={18} /><input autoFocus value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search commands, agents, scopes…" onKeyDown={(e) => e.key === 'Escape' && onClose()} /><Kbd>esc</Kbd></div><div className="palette-label">Actions</div><div style={{ maxHeight: '300px', overflowY: 'auto' }}>{shown.map((item) => <button className="palette-item" key={item.name} onClick={() => { item.action(); onClose() }}><span className="palette-icon"><item.icon size={16} /></span><span><strong>{item.name}</strong><small>{item.subtitle}</small></span><Kbd>{item.shortcut}</Kbd><ChevronRight size={14} /></button>)}{shown.length === 0 && <div className="no-results">No commands found</div>}</div><div className="palette-footer"><span><Kbd>↑↓</Kbd> Navigate</span><span><Kbd>↵</Kbd> Select</span><span><Kbd>esc</Kbd> Close</span></div></div></div>
}


export default function Page() {
    const [view, setView] = useState<View>('Graph Explorer');
    const [palette, setPalette] = useState(false);
    const [mobileNav, setMobileNav] = useState(false);
    const [selectedNode, setSelectedNode] = useState<any>(null);
    const [activityOpen, setActivityOpen] = useState(true);

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

    return (
        <main className="app-shell">
            <div className="bg-glow"></div>

            <aside className={`sidebar ${mobileNav ? 'mobile-open' : ''}`}>
                <div className="brand">
                    <div className="brand-mark">
                        <img src="/icon.png" style={{ width: 17, height: 17, objectFit: 'contain' }} alt="KryptoDB Logo" />
                    </div>
                    <span>Krypto<span>DB</span></span>
                    <Button className="mobile-close" onClick={() => setMobileNav(false)}><X size={15} /></Button>
                </div>
                <div className="workspace">
                    <span className="eyebrow">Workspace</span>
                    <div className="workspace-value"><span className="workspace-dot" /> KryptoDB / prod</div>
                </div>
                <nav>
                    <span className="nav-label">Observe</span>
                    {nav.slice(0, 2).map(({ label, icon: Icon }) => (
                        <button key={label} className={view === label ? 'active' : ''} onClick={() => { setView(label); setMobileNav(false) }}>
                            <Icon size={16} />{label}{label === 'Graph Explorer' && <span className="nav-pulse" />}
                        </button>
                    ))}
                    <span className="nav-label">Configure</span>
                    {nav.slice(2).map(({ label, icon: Icon }) => (
                        <button key={label} className={view === label ? 'active' : ''} onClick={() => { setView(label); setMobileNav(false) }}>
                            <Icon size={16} />{label}
                        </button>
                    ))}
                </nav>
                <div className="sidebar-bottom">
                    <div className="security-note">
                        <Shield size={15} />
                        <div><strong>Encrypted by default</strong><span>AES-256-GCM active</span></div>
                    </div>
                </div>
            </aside>

            <div className="main-area">
                <header className="topbar">
                    <Button className="menu-button" onClick={() => setMobileNav(true)}><Menu size={17} /></Button>
                    <button className="global-search" onClick={() => setPalette(true)}>
                        <Search size={15} /><span>Search anything…</span><Kbd>⌘ K</Kbd>
                    </button>
                    <div className="top-actions">
                        <div className="stream-status"><StatusDot /> <span>Live</span></div>
                        <Button
                            title="Agent Activity"
                            onClick={() => setActivityOpen(true)}
                            className={`agent-activity-trigger ${activityOpen ? 'primary' : ''}`}
                        >
                            <Bot size={16} /> Agent Activity <span className="trigger-badge">●2</span>
                        </Button>
                    </div>
                </header>
                {view === 'Graph Explorer' && <GraphExplorer selected={selectedNode} onSelect={setSelectedNode} />}
                {view === 'Agents & Access' && <AgentsView />}
                {view === 'Config / Ontology' && <ConfigView />}
                {view === 'Audit Log' && <AuditView />}
                {view === 'MCP Setup' && <McpView />}
            </div>

            {palette && <CommandPalette onClose={() => setPalette(false)} onNavigate={setView} onNodeSelect={setSelectedNode} />}
            {activityOpen && <AgentActivityModal onClose={() => setActivityOpen(false)} onSelectNode={setSelectedNode} />}
        </main>
    )
}
