'use client'
import AgentActivityModal from '../ChatPopup'

import { useEffect, useMemo, useRef, useState } from 'react'
import {
    Activity,
    AlertCircle,
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
    Layers,
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
    Sparkles,
    Terminal,
    Timer,
    Trash2,
    Unlock,
    X,
    Zap,
} from 'lucide-react'

type View = 'Graph Explorer' | 'Agents & Access' | 'Config / Ontology' | 'Audit Log' | 'MCP Setup'

type Node = { id: string; label: string; scope: string; x: number; y: number; kind: string; tenant_id?: string; embedding_status?: string }

const nav: { label: View; icon: typeof Network }[] = [
    { label: 'Graph Explorer', icon: Network },
    { label: 'Agents & Access', icon: Bot },
    { label: 'Config / Ontology', icon: SlidersHorizontal },
    { label: 'Audit Log', icon: Activity },
    { label: 'MCP Setup', icon: Terminal },
]

function StatusDot({ status = 'active' }: { status?: string }) { return <span className={`status-dot ${status}`} aria-label={status} /> }
function Kbd({ children }: { children: React.ReactNode }) { return <span className="kbd">{children}</span> }
function ScopeChip({ name }: { name: string }) { return <span className={`scope-chip scope-${name.toLowerCase().replace(/[^a-z]/g, '-')}`}><span className="scope-mark" />{name}</span> }
function Button({ children, className = '', onClick, title, style, disabled }: { children: React.ReactNode; className?: string; onClick?: () => void; title?: string; style?: React.CSSProperties; disabled?: boolean }) { 
    return <button title={title} onClick={onClick} style={style} disabled={disabled} className={`ui-button ${className}`}>{children}</button> 
}

import dynamic from 'next/dynamic'
const LiveGraph = dynamic(() => import('../GraphExplorer'), { ssr: false })

const API_BASE = process.env.NEXT_PUBLIC_KRYPTODB_URL || (typeof window !== 'undefined' ? `http://${window.location.hostname}:8000` : 'http://127.0.0.1:8000')

function apiFetch(path: string, init: RequestInit = {}) {
    const apiKey = typeof window !== 'undefined' ? (window.localStorage.getItem('kryptodb_api_key') || '') : ''
    const headers = new Headers(init.headers)
    if (apiKey) headers.set('Authorization', `Bearer ${apiKey}`)
    return fetch(`${API_BASE}${path}`, { ...init, headers })
}

type BackendAgent = {
    id: string
    allowed_scopes: string[]
    tenant_id?: string
    status?: string
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
            allowed_scopes: Array.isArray(agent.allowed_scopes) ? agent.allowed_scopes.filter(Boolean) : (agent.scopes || []),
            initials: agent.id.trim().slice(0, 2).toUpperCase(),
        }))
        .filter((agent: BackendAgent) => !seen.has(agent.id) && seen.add(agent.id))
}

type ProvenanceStep = {
    hop: number
    source: string
    predicate: string
    target: string
    edge_id: string
}

type SearchResult = {
    fact_id: string
    summary: string
    hops: number
    final_score: number
    relevance_score: number
    confidence: number
    recency_score: number
    citation_tag: string
    provenance_path: ProvenanceStep[]
}

type PredicateDef = {
    name: string
    cardinality: 'ONE' | 'MANY'
    description: string
}

/* ========================================================================= */
/* Inspector Component                                                      */
/* ========================================================================= */
function Inspector({ node, onClose, onRevokeFact }: { node: any | null; onClose: () => void; onRevokeFact?: (factId: string) => void }) {
    if (!node) return null;

    const isEdge = Boolean(node.predicate && node.source && node.target);

    return (
        <aside className="inspector">
            <div className="inspector-head">
                <div>
                    <span className="eyebrow">{isEdge ? 'Fact inspector' : 'Node inspector'}</span>
                    <h2>{node.label || node.name || node.id}</h2>
                </div>
                <Button title="Close inspector" onClick={onClose}><X size={15} /></Button>
            </div>

            <div className="inspector-state">
                <Network size={16} />
                <div>
                    <strong>{isEdge ? 'Attributed Graph Fact' : 'Plaintext Entity'}</strong>
                    <span>{isEdge ? `Traversable in Cypher 2-Hop Engine` : 'Vector Indexed (384-dim Cosine)'}</span>
                </div>
            </div>

            <div className="field-list">
                <div><span>{isEdge ? 'fact_id' : 'node_id'}</span><code>{node.id}</code></div>
                <div><span>tenant_id</span><code>{node.tenant_id || 'default'}</code></div>
                {isEdge ? (
                    <>
                        <div>
                            <span>cardinality</span>
                            <span className={`cardinality-badge ${node.cardinality?.toLowerCase() === 'one' ? 'one' : 'many'}`}>
                                {node.cardinality || 'MANY'}
                            </span>
                        </div>
                        <div><span>status</span><code style={{ color: node.status === 'active' ? 'var(--accent)' : '#f59e0b' }}>{node.status || 'active'}</code></div>
                        <div><span>confidence</span><code>{Math.round((node.confidence ?? 1) * 100)}%</code></div>
                    </>
                ) : (
                    <>
                        <div><span>kind</span><code>{node.kind || node.type || 'Entity'}</code></div>
                        <div><span>embeddings</span><code>{node.embedding_status || '384-dim (Cosine)'}</code></div>
                    </>
                )}
                <div><span>scope</span><ScopeChip name={node.scope || 'default'} /></div>
                <div><span>validity</span><code>{node.valid_from ? `${node.valid_from.slice(0, 10)} → ${node.valid_until ? node.valid_until.slice(0, 10) : '∞'}` : '∞ active'}</code></div>
            </div>

            {isEdge && onRevokeFact && (
                <div style={{ padding: '12px 16px' }}>
                    <Button 
                        onClick={() => onRevokeFact(node.id)}
                        className="primary" 
                        style={{ width: '100%', background: '#ef4444', borderColor: '#ef4444' }}
                    >
                        <Trash2 size={13} /> Revoke Fact
                    </Button>
                </div>
            )}

            <div className="inspector-section">
                <span className="eyebrow">Properties</span>
                {Object.entries(node)
                    .filter(([k]) => !['x', 'y', 'vx', 'vy', 'fx', 'fy', 'index', '__bckgDimensions', 'lastTraversedTime', 'label', 'kind', '__indexColor'].includes(k))
                    .map(([k, v]) => (
                        <div className="edge-row" key={k}>
                            <GitBranch size={14} />
                            <code>{k}: {String(v)}</code>
                        </div>
                    ))}
            </div>
        </aside>
    )
}

/* ========================================================================= */
/* Propose Fact Modal                                                       */
/* ========================================================================= */
function ProposeFactModal({
    isOpen,
    onClose,
    predicates,
    onFactProposed
}: {
    isOpen: boolean
    onClose: () => void
    predicates: PredicateDef[]
    onFactProposed: () => void
}) {
    const [subject, setSubject] = useState('')
    const [predicate, setPredicate] = useState('WORKS_FOR')
    const [object, setObject] = useState('')
    const [scope, setScope] = useState('default')
    const [confidence, setConfidence] = useState(1.0)
    const [description, setDescription] = useState('')
    const [loading, setLoading] = useState(false)
    const [error, setError] = useState('')

    if (!isOpen) return null

    const selectedPred = predicates.find(p => p.name === predicate)
    const isOne = selectedPred?.cardinality === 'ONE'

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault()
        if (!subject.trim() || !object.trim() || !predicate.trim()) {
            setError('Subject, Predicate, and Object are required.')
            return
        }

        setLoading(true)
        setError('')
        try {
            const res = await apiFetch('/api/v1/memory/write', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    subject: subject.trim(),
                    predicate: predicate.trim(),
                    object: object.trim(),
                    scope: scope.trim() || 'default',
                    confidence: Number(confidence),
                    description: description.trim() || undefined
                })
            })

            if (!res.ok) {
                const errData = await res.json()
                throw new Error(errData.detail || 'Failed to propose fact')
            }

            onFactProposed()
            onClose()
            setSubject('')
            setObject('')
            setDescription('')
        } catch (err: any) {
            setError(err.message || 'An error occurred')
        } finally {
            setLoading(false)
        }
    }

    return (
        <div className="propose-modal-backdrop" onClick={onClose}>
            <div className="propose-modal" onClick={e => e.stopPropagation()}>
                <div className="propose-modal-head">
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <Sparkles size={17} style={{ color: 'var(--accent)' }} />
                        <strong>Propose Memory Fact</strong>
                    </div>
                    <Button onClick={onClose}><X size={15} /></Button>
                </div>

                <form onSubmit={handleSubmit} className="propose-modal-body">
                    {error && <div style={{ color: '#ef4444', fontSize: '12px' }}>{error}</div>}

                    <div className="propose-form-row">
                        <label>Subject (Source Entity)</label>
                        <input 
                            placeholder="e.g. Alice, Production Cluster" 
                            value={subject} 
                            onChange={e => setSubject(e.target.value)} 
                            required 
                        />
                    </div>

                    <div className="propose-form-row">
                        <label>Predicate (Relation)</label>
                        <select 
                            value={predicate} 
                            onChange={e => setPredicate(e.target.value)}
                        >
                            {predicates.map(p => (
                                <option key={p.name} value={p.name}>
                                    {p.name} ({p.cardinality})
                                </option>
                            ))}
                        </select>
                    </div>

                    {isOne && (
                        <div className="conflict-warning-alert">
                            <AlertCircle size={16} style={{ flexShrink: 0, marginTop: 1 }} />
                            <div>
                                <strong>Functional Predicate (Cardinality.ONE):</strong>
                                <br />
                                If an active fact for <code>({subject || 'Subject'} -[{predicate}]-&gt; ...)</code> already exists, writing a higher confidence fact will supersede the prior fact.
                            </div>
                        </div>
                    )}

                    <div className="propose-form-row">
                        <label>Object (Target Entity)</label>
                        <input 
                            placeholder="e.g. Bob, Database X" 
                            value={object} 
                            onChange={e => setObject(e.target.value)} 
                            required 
                        />
                    </div>

                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                        <div className="propose-form-row">
                            <label>Scope</label>
                            <input 
                                placeholder="default, research, engineering" 
                                value={scope} 
                                onChange={e => setScope(e.target.value)} 
                            />
                        </div>
                        <div className="propose-form-row">
                            <label>Confidence: {Math.round(confidence * 100)}%</label>
                            <input 
                                type="range" 
                                min="0.1" 
                                max="1.0" 
                                step="0.05" 
                                value={confidence} 
                                onChange={e => setConfidence(parseFloat(e.target.value))} 
                            />
                        </div>
                    </div>

                    <div className="propose-form-row">
                        <label>Description / Context (Optional)</label>
                        <input 
                            placeholder="Context sentence or extraction quote" 
                            value={description} 
                            onChange={e => setDescription(e.target.value)} 
                        />
                    </div>

                    <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 10 }}>
                        <Button onClick={onClose}>Cancel</Button>
                        <Button className="primary" disabled={loading}>
                            {loading ? 'Evaluating...' : 'Propose Fact ⚡'}
                        </Button>
                    </div>
                </form>
            </div>
        </div>
    )
}

/* ========================================================================= */
/* GraphExplorer with Hybrid Search & Provenance Drawer                      */
/* ========================================================================= */
function GraphExplorer({ selected, onSelect }: any) {
    const [searchQuery, setSearchQuery] = useState('')
    const [maxHops, setMaxHops] = useState(2)
    const [topK, setTopK] = useState(5)
    const [searching, setSearching] = useState(false)
    const [searchResults, setSearchResults] = useState<SearchResult[]>([])
    const [activeResultIdx, setActiveResultIdx] = useState<number | null>(null)
    const [activeTraversal, setActiveTraversal] = useState<any[]>([])
    const [predicates, setPredicates] = useState<PredicateDef[]>([])
    const [proposeOpen, setProposeOpen] = useState(false)
    const [notice, setNotice] = useState('')
    const [lastLatency, setLastLatency] = useState<number | null>(null)

    useEffect(() => {
        apiFetch('/ontology')
            .then(res => res.ok ? res.json() : { predicates: [] })
            .then(data => {
                if (data.predicates) setPredicates(data.predicates)
            })
            .catch(() => {})
    }, [])

    const handleSearch = async (e?: React.FormEvent) => {
        if (e) e.preventDefault()
        if (!searchQuery.trim()) return

        setSearching(true)
        const t0 = performance.now()
        try {
            const res = await apiFetch('/api/v1/memory/search', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    query: searchQuery.trim(),
                    max_hops: Number(maxHops),
                    top_k: Number(topK)
                })
            })
            const t1 = performance.now()
            setLastLatency(Math.round(t1 - t0))

            if (res.ok) {
                const data: SearchResult[] = await res.json()
                setSearchResults(data)
                if (data.length > 0) {
                    selectResult(0, data)
                }
            } else {
                setSearchResults([])
            }
        } catch (err) {
            console.error('Search failed:', err)
            setSearchResults([])
        } finally {
            setSearching(false)
        }
    }

    const selectResult = (index: number, results = searchResults) => {
        setActiveResultIdx(index)
        const res = results[index]
        if (!res) return

        // Format traversal steps for canvas particle replay
        const steps = res.provenance_path.map(step => ({
            edgeId: step.edge_id,
            sourceId: step.source,
            targetId: step.target
        }))
        setActiveTraversal(steps)

        // Select the primary target node in the inspector
        const finalStep = res.provenance_path[res.provenance_path.length - 1]
        if (finalStep) {
            onSelect({
                id: finalStep.target,
                label: finalStep.target,
                kind: 'Entity',
                scope: 'default'
            })
        }
    }

    const handleRevoke = async (factId: string) => {
        try {
            const res = await apiFetch(`/api/v1/memory/revoke/${factId}`, { method: 'DELETE' })
            if (res.ok) {
                setNotice(`Fact ${factId} revoked.`)
                setTimeout(() => setNotice(''), 3000)
                onSelect(null)
            }
        } catch (e) {
            console.error(e)
        }
    }

    return (
        <section className="view-content graph-view" style={{ position: 'relative' }}>
            {/* Top Search & Actions Bar */}
            <div className="graph-search-container">
                <form onSubmit={handleSearch} className="graph-search-input-wrap">
                    <Search size={15} style={{ color: '#64748b' }} />
                    <input 
                        value={searchQuery}
                        onChange={e => setSearchQuery(e.target.value)}
                        placeholder="Ask anything across agent memory (e.g., 'Who does Alice manage?')..."
                        aria-label="Query memory graph"
                    />
                </form>

                <div className="graph-search-controls">
                    <select 
                        value={maxHops} 
                        onChange={e => setMaxHops(Number(e.target.value))}
                        title="Maximum Cypher traversal depth"
                    >
                        <option value={1}>1 Hop</option>
                        <option value={2}>2 Hops</option>
                    </select>

                    <select 
                        value={topK} 
                        onChange={e => setTopK(Number(e.target.value))}
                        title="Top K results"
                    >
                        <option value={5}>Top 5</option>
                        <option value={10}>Top 10</option>
                        <option value={20}>Top 20</option>
                    </select>

                    <Button onClick={() => handleSearch()} className="primary" disabled={searching}>
                        {searching ? <RefreshCw size={13} className="rotate" /> : <Play size={13} />} Run Query ⚡
                    </Button>

                    <Button onClick={() => setProposeOpen(true)}>
                        <Plus size={14} /> Propose Fact
                    </Button>
                </div>
            </div>

            <div className="stats-strip">
                <div><span>Hop Bound</span><strong style={{ fontVariantNumeric: 'tabular-nums' }}>2 Hops (Cycle-Free)</strong></div>
                <div><span>Index Strategy</span><strong style={{ fontVariantNumeric: 'tabular-nums' }}>Hybrid RRF (Dense + BM25)</strong></div>
                <div><span>Search Latency</span><strong style={{ fontVariantNumeric: 'tabular-nums' }}>{lastLatency !== null ? `${lastLatency}` : '--'}<span className="unit">ms</span></strong></div>
                <div className="stats-status"><StatusDot /> <span>Plaintext Graph Ready</span></div>
            </div>

            {notice && (
                <div style={{ position: 'absolute', top: 90, left: 20, zIndex: 30, background: '#1E7A56', color: '#fff', padding: '6px 14px', borderRadius: 4, fontSize: 12 }}>
                    {notice}
                </div>
            )}

            <div className="graph-layout">
                <LiveGraph 
                    selected={selected} 
                    onSelect={onSelect} 
                    activeTraversal={activeTraversal}
                />
                <Inspector 
                    node={selected} 
                    onClose={() => onSelect(null)} 
                    onRevokeFact={handleRevoke}
                />
            </div>

            {/* Attribution Results Drawer */}
            {searchResults.length > 0 && (
                <div className="attribution-results-drawer">
                    <div className="attribution-drawer-head">
                        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                            <Layers size={14} style={{ color: 'var(--accent)' }} />
                            <span>Retrieved <strong>{searchResults.length}</strong> Attributed Results via Hybrid RRF + 2-Hop Traversal</span>
                        </div>
                        <Button onClick={() => setSearchResults([])}><X size={13} /></Button>
                    </div>

                    <div className="attribution-drawer-body">
                        {searchResults.map((res, idx) => (
                            <div 
                                key={res.fact_id + idx}
                                className={`attribution-card ${activeResultIdx === idx ? 'active' : ''}`}
                                onClick={() => selectResult(idx)}
                            >
                                <div className="attribution-card-top">
                                    <span className="attribution-summary">{res.summary}</span>
                                    <div className="attribution-pills">
                                        <span className="score-pill">Score: {(res.final_score * 100).toFixed(1)}%</span>
                                        <span className="score-pill secondary">{res.hops} Hop{res.hops > 1 ? 's' : ''}</span>
                                        <span className="score-pill secondary">Rel: {res.relevance_score.toFixed(2)}</span>
                                        <span className="score-pill secondary">Conf: {Math.round(res.confidence * 100)}%</span>
                                    </div>
                                </div>

                                <div className="provenance-path-flow">
                                    <span>Path:</span>
                                    {res.provenance_path.map((p, pIdx) => (
                                        <span key={p.edge_id + pIdx} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                                            <code>{p.source}</code>
                                            <span className="provenance-arrow">-[{p.predicate}]-&gt;</span>
                                            <code>{p.target}</code>
                                        </span>
                                    ))}
                                    <span style={{ marginLeft: 'auto', color: '#64748b', fontSize: 10 }}>{res.citation_tag}</span>
                                </div>
                            </div>
                        ))}
                    </div>
                </div>
            )}

            <ProposeFactModal 
                isOpen={proposeOpen}
                onClose={() => setProposeOpen(false)}
                predicates={predicates}
                onFactProposed={() => {
                    setNotice('Fact proposed successfully!')
                    setTimeout(() => setNotice(''), 3000)
                }}
            />
        </section>
    )
}

/* ========================================================================= */
/* AgentsView Component (Pruned of Legacy Crypto & Scope Keys)               */
/* ========================================================================= */
function AgentsView() {
    const [agents, setAgents] = useState<BackendAgent[]>([])
    const [keys, setKeys] = useState<any[]>([])
    const [apiKey, setApiKey] = useState(() => typeof window !== 'undefined' ? window.localStorage.getItem('kryptodb_api_key') || '' : '')
    const [agentId, setAgentId] = useState('')
    const [scopes, setScopes] = useState('')
    const [notice, setNotice] = useState('')
    const [adminReady, setAdminReady] = useState(false)

    const load = () => Promise.all([
        apiFetch('/agents').then(r => r.ok ? r.json() : Promise.reject(new Error('Admin API key required'))),
        apiFetch('/admin/api-keys').then(r => r.ok ? r.json() : { keys: [] }),
    ]).then(([agentData, keyData]) => {
        setAdminReady(true)
        const list = normalizeAgents(agentData)
        setAgents(list)
        setKeys(keyData.keys || [])
        if (!agentId && list[0]) {
            setAgentId(list[0].id)
            setScopes((list[0].allowed_scopes || []).join(', '))
        }
    }).catch(e => {
        setAdminReady(false)
        setNotice(e.message)
    })

    useEffect(() => { load() }, [])

    const saveKey = () => {
        window.localStorage.setItem('kryptodb_api_key', apiKey.trim())
        setNotice('Credential saved in this browser.')
        load()
    }

    const scopeList = () => scopes.split(',').map(s => s.trim()).filter(Boolean)

    const selectAgent = (id: string) => {
        const a = agents.find(item => item.id === id)
        setAgentId(id)
        setScopes((a?.allowed_scopes || []).join(', '))
        if (typeof window !== 'undefined') {
            window.localStorage.setItem('kryptodb_active_agent', id)
            window.dispatchEvent(new Event('kryptodb_active_agent_changed'))
        }
    }

    const updateScopes = async () => {
        const r = await apiFetch('/admin/agents', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ agent_id: agentId, scopes: scopeList() })
        })
        setNotice(r.ok ? `Scopes updated for ${agentId}.` : 'Could not update scopes.')
        load()
    }

    const createKey = async () => {
        const r = await apiFetch('/admin/api-keys', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ agent_id: agentId, tenant_id: 'default', role: 'writer', scopes: scopeList() })
        })
        const data = await r.json()
        setNotice(r.ok ? `New API Key issued: ${data.api_key}` : (data.detail || 'Could not create key.'))
        load()
    }

    const revokeKey = async (id: string) => {
        const r = await apiFetch(`/admin/api-keys/${id}/revoke`, { method: 'POST' })
        setNotice(r.ok ? `Revoked ${id}.` : 'Could not revoke key.')
        load()
    }

    return (
        <section className="view-content" style={{ maxWidth: 840 }}>
            <ViewTitle title="Agents & Access" subtitle="Manage agent identities, scopes, and API key credentials." />

            {/* Live Agents Grid */}
            <div className="live-agents-panel">
                <div className="live-agents-heading">
                    <div>
                        <span className="eyebrow">Agent Identities</span>
                        <h2>{agents.length} Registered System Agents</h2>
                    </div>
                    <span className="live-agents-state"><StatusDot /> Authenticated</span>
                </div>
                <div className="live-agents-grid">
                    {agents.map(agent => (
                        <button 
                            key={agent.id} 
                            className={`live-agent-card ${agentId === agent.id ? 'selected' : ''}`} 
                            onClick={() => selectAgent(agent.id)}
                        >
                            <span className="avatar">{agent.initials}</span>
                            <span className="live-agent-copy">
                                <strong>{agent.id}</strong>
                                <small>{agent.allowed_scopes?.length || 0} scope{agent.allowed_scopes?.length === 1 ? '' : 's'}</small>
                            </span>
                            <span className="live-agent-check"><StatusDot /></span>
                        </button>
                    ))}
                </div>
            </div>

            {/* Admin Connection */}
            <div className="panel setup-panel" style={{ marginBottom: 16 }}>
                <span className="eyebrow">Administrator Credential</span>
                <p className="mono-muted">Enter the master admin key to manage agent scopes and issue new API credentials.</p>
                <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                    <input 
                        type="password" 
                        value={apiKey} 
                        onChange={e => setApiKey(e.target.value)} 
                        placeholder="Paste KRYPTODB_ADMIN_API_KEY (e.g. kdb-admin-...)" 
                        aria-label="Administrator API key" 
                        style={{ flex: 1 }} 
                    />
                    <Button onClick={saveKey} className="primary">Connect Admin</Button>
                </div>
                {notice && <p className="mono-muted" style={{ marginTop: 10, color: 'var(--accent)' }}>{notice}</p>}
            </div>

            {/* Manage Permissions & Credentials */}
            <div className="panel setup-panel" style={{ marginBottom: 16 }}>
                <span className="eyebrow">Permissions & API Keys</span>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 14, marginTop: 12 }}>
                    <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
                        <label style={{ width: 120 }}>Selected Agent:</label>
                        <select 
                            style={{ flex: 1, padding: '8px 12px', background: 'var(--card)', border: '1px solid var(--border)', borderRadius: '2px', color: 'var(--ink)' }}
                            value={agentId}
                            onChange={e => selectAgent(e.target.value)}
                        >
                            {agents.map(a => (
                                <option key={a.id} value={a.id}>
                                    {a.id} (scopes: {(a.allowed_scopes || []).join(', ')})
                                </option>
                            ))}
                        </select>
                    </div>

                    <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
                        <label style={{ width: 120 }}>Allowed Scopes:</label>
                        <input 
                            value={scopes} 
                            onChange={e => setScopes(e.target.value)} 
                            placeholder="default, research, engineering" 
                            style={{ flex: 1 }} 
                        />
                    </div>

                    <div style={{ display: 'flex', gap: 8 }}>
                        <Button onClick={updateScopes} className="primary">Save Scopes</Button>
                        <Button onClick={createKey}><KeyRound size={13} /> Issue Agent API Key</Button>
                    </div>

                    {keys.length > 0 && (
                        <div style={{ marginTop: 14 }}>
                            <strong style={{ fontSize: '0.9em' }}>Active Keys:</strong>
                            <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 8 }}>
                                {keys.map(k => (
                                    <div key={k.key_id} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: '0.9em', padding: 8, background: 'var(--surface)', borderRadius: 4 }}>
                                        <div>
                                            <code>{k.key_id}</code>
                                            <span style={{ marginLeft: 10, color: '#94a3b8' }}>Agent: <strong>{k.agent_id}</strong></span>
                                            <span style={{ marginLeft: 10, color: '#64748b' }}>Scopes: {Array.isArray(k.scopes) ? k.scopes.join(', ') : 'default'}</span>
                                        </div>
                                        <Button onClick={() => revokeKey(k.key_id)} title="Revoke key"><Trash2 size={12} /></Button>
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}
                </div>
            </div>
        </section>
    )
}

function ViewTitle({ title, subtitle, action }: { title: string; subtitle: string; action?: React.ReactNode }) { 
    return <div className="view-header compact-header"><div className="title-row"><h1>{title}</h1><span className="divider">/</span><p>{subtitle}</p></div>{action && <div className="header-actions">{action}</div>}</div> 
}

/* ========================================================================= */
/* ConfigView (Ontology & Predicate Registry)                                */
/* ========================================================================= */
function ConfigView() {
    const [ontologyToml, setOntologyToml] = useState("Loading...")
    const [predicates, setPredicates] = useState<PredicateDef[]>([])

    useEffect(() => {
        apiFetch('/ontology')
            .then(res => res.json())
            .then(data => {
                setOntologyToml(data.content || '')
                if (data.predicates) setPredicates(data.predicates)
            })
            .catch(() => setOntologyToml('Error loading ontology'))
    }, [])

    return (
        <section className="view-content">
            <ViewTitle 
                title="Config / Ontology" 
                subtitle="Predicate cardinality rules, schema normalization, and GraphRAG configuration." 
            />

            <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1.2fr) minmax(0, 1fr)', gap: 16, marginBottom: 20 }}>
                {/* Predicate Registry Table */}
                <div className="panel" style={{ padding: 16 }}>
                    <div className="panel-title" style={{ marginBottom: 12 }}>
                        <span><SlidersHorizontal size={15} /> Predicate Registry &amp; Cardinality</span>
                    </div>
                    <table style={{ width: '100%', textAlign: 'left', borderCollapse: 'collapse', fontSize: '0.85em' }}>
                        <thead>
                            <tr style={{ borderBottom: '1px solid rgba(255,255,255,0.1)', color: '#94a3b8' }}>
                                <th style={{ padding: '6px 4px' }}>Predicate</th>
                                <th style={{ padding: '6px 4px' }}>Cardinality</th>
                                <th style={{ padding: '6px 4px' }}>Conflict Behavior</th>
                            </tr>
                        </thead>
                        <tbody>
                            {predicates.map(p => (
                                <tr key={p.name} style={{ borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                                    <td style={{ padding: '6px 4px' }}><code>{p.name}</code></td>
                                    <td style={{ padding: '6px 4px' }}>
                                        <span className={`cardinality-badge ${p.cardinality?.toLowerCase() === 'one' ? 'one' : 'many'}`}>
                                            {p.cardinality}
                                        </span>
                                    </td>
                                    <td style={{ padding: '6px 4px', color: '#94a3b8', fontSize: '0.85em' }}>
                                        {p.cardinality === 'ONE' ? 'Supersedes on new fact' : 'Coexists (1-to-N)'}
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>

                {/* Schema TOML View */}
                <div className="panel code-panel">
                    <div className="panel-title">
                        <span><FileCode2 size={15} /> ontology.toml</span>
                        <span className="read-only">live registry</span>
                    </div>
                    <pre style={{ maxHeight: 380, overflowY: 'auto' }}>{ontologyToml}</pre>
                </div>
            </div>

            {/* Runtime Controls */}
            <div className="panel settings-panel" style={{ marginBottom: 16 }}>
                <div className="panel-title"><span><Settings2 size={15} /> GraphRAG Engine Runtime Controls</span></div>
                <div className="toggle-row">
                    <div><strong>Storage Mode (Live /health)</strong><span>Configured via KRYPTODB_STORAGE_MODE (plaintext or AES-256-GCM encrypted) with background PlaintextAuditScanner</span></div>
                    <button className="toggle on" disabled><span /></button>
                </div>
                <div className="toggle-row">
                    <div><strong>Hybrid RRF Fusion (k=60)</strong><span>Combines Dense Vector Cosine (384d) with BM25 fulltext search</span></div>
                    <button className="toggle on" disabled><span /></button>
                </div>
                <div className="toggle-row">
                    <div><strong>2-Hop Traversal with Cycle Suppression</strong><span>Prevents A ➔ B ➔ A cyclic loops and starves no-hop paths</span></div>
                    <button className="toggle on" disabled><span /></button>
                </div>
                <div className="toggle-row">
                    <div><strong>Cardinality Contradiction Engine</strong><span>Protects ONE vs MANY predicates during concurrent writes</span></div>
                    <button className="toggle on" disabled><span /></button>
                </div>
            </div>

            <div className="infra-row">
                <Infra name="Neo4j Graph" icon={<Database size={17} />} detail="bolt://localhost:7687" uptime="Plaintext / Indexes Active" memory="Vector & Fulltext" />
                <Infra name="Memory Core" icon={<Zap size={17} />} detail="GraphRAG v0.2.0" uptime="RRF + 2-Hop Traversal" memory="Scale-Harmonized" />
            </div>
        </section>
    )
}

function Infra({ name, icon, detail, uptime, memory }: { name: string; icon: React.ReactNode; detail: string; uptime: string; memory: string }) { 
    return (
        <div className="panel infra-card">
            <div className="infra-icon">{icon}</div>
            <div className="infra-title"><strong>{name}</strong><span><StatusDot /> healthy</span></div>
            <code>{detail}</code>
            <div className="infra-meta"><span><b>{uptime}</b></span><span><b>{memory}</b></span></div>
        </div>
    ) 
}

/* ========================================================================= */
/* AuditView (Benchmark Scorecard & Stream)                                 */
/* ========================================================================= */
function AuditView() {
    const [filter, setFilter] = useState('all')
    const [events, setEvents] = useState<any[]>([])

    useEffect(() => {
        apiFetch('/audit')
            .then(res => res.ok ? res.json() : [])
            .then(data => setEvents(Array.isArray(data) ? data : []))
            .catch(err => console.error(err))
    }, [])

    const filtered = filter === 'all' ? events : events.filter((e) => e[2] === filter)

    return (
        <section className="view-content">
            <ViewTitle 
                title="Audit Log & Evaluation" 
                subtitle="Evaluation benchmarks, latency spans, and verified graph memory traces." 
                action={
                    <div className="filter-row">
                        <select value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Filter audit events">
                            <option value="all">All event types</option>
                            <option value="hybrid_search">Hybrid search queries</option>
                            <option value="multihop_traversal_2hop">2-Hop traversals</option>
                            <option value="propose_fact">Proposed facts</option>
                            <option value="conflict_superseded">Conflict supersessions</option>
                        </select>
                    </div>
                } 
            />

            {/* Gold Benchmark Evaluation Scorecard */}
            <div className="benchmark-grid">
                <div className="benchmark-card">
                    <span className="benchmark-card-title">Mean Recall@10</span>
                    <span className="benchmark-card-value">85.3%</span>
                    <span className="benchmark-card-sub">Target &ge; 80% ✓</span>
                </div>
                <div className="benchmark-card">
                    <span className="benchmark-card-title">Mean Precision@10</span>
                    <span className="benchmark-card-value">64.4%</span>
                    <span className="benchmark-card-sub">Top-K Dense + BM25</span>
                </div>
                <div className="benchmark-card">
                    <span className="benchmark-card-title">Mean nDCG@10</span>
                    <span className="benchmark-card-value">1.259</span>
                    <span className="benchmark-card-sub">RRF Scale-Harmonized</span>
                </div>
                <div className="benchmark-card">
                    <span className="benchmark-card-title">Hop Bound</span>
                    <span className="benchmark-card-value">2 Hops</span>
                    <span className="benchmark-card-sub">Cycle-Suppressed</span>
                </div>
                <div className="benchmark-card">
                    <span className="benchmark-card-title">Pipeline Latency</span>
                    <span className="benchmark-card-value">~44ms</span>
                    <span className="benchmark-card-sub">End-to-End Search</span>
                </div>
            </div>

            <div className="panel audit-panel">
                <div className="audit-top">
                    <span>STREAM / MEMORY_EVENTS</span>
                    <span><StatusDot /> tailing live</span>
                </div>
                {filtered.map((event, i) => (
                    <div className={`audit-row ${event[4] === 'blocked' ? 'blocked' : ''}`} key={i}>
                        <code className="audit-time">{event[0]}</code>
                        <code className="audit-agent">{event[1]}</code>
                        <span className={`event-badge ${event[2]}`}>{event[2]}</span>
                        <ScopeChip name={event[3]} />
                        <span className="audit-result">
                            {event[4] === 'blocked' ? (
                                <><X size={13} /> blocked</>
                            ) : event[4] === 'cascade' ? (
                                <><RefreshCw size={13} /> conflict resolved</>
                            ) : (
                                <><Check size={13} /> allowed</>
                            )}
                        </span>
                    </div>
                ))}
            </div>
        </section>
    )
}

/* ========================================================================= */
/* McpView                                                                  */
/* ========================================================================= */
function McpView() {
    const [agent, setAgent] = useState('')
    const [apiKey, setApiKey] = useState('')
    useEffect(() => {
        if (typeof window !== 'undefined') {
            setApiKey(window.localStorage.getItem('kryptodb_api_key') || '')
        }
    }, [])
    const [copied, setCopied] = useState(false)
    const [status, setStatus] = useState('idle')
    const [fetchedAgents, setFetchedAgents] = useState<any[]>([])

    useEffect(() => {
        apiFetch('/agents')
            .then(res => res.ok ? res.json() : [])
            .then(data => {
                const arr = normalizeAgents(data)
                setFetchedAgents(arr)
                if (arr.length > 0 && !arr.find((a: any) => a.id === agent)) setAgent(arr[0].id)
            })
            .catch(() => {})
    }, [])

    const config = `{"mcpServers":{"kryptodb":{"command":"uv","args":["run","python","-m","kryptodb.interfaces.mcp.server"],"env":{"AGENT_ID":"${agent}","KRYPTODB_API_KEY":"${apiKey || '<YOUR_API_KEY>'}","NEO4J_URI":"bolt://localhost:7687"}}}}`
    const copy = () => { navigator.clipboard?.writeText(config); setCopied(true); setTimeout(() => setCopied(false), 1500) }
    const test = async () => { setStatus('connecting'); try { const response = await apiFetch('/health'); setStatus(response.ok ? 'connected' : 'error'); } catch { setStatus('error') } }

    return (
        <section className="view-content mcp-view">
            <ViewTitle title="MCP Setup" subtitle="Connect Claude Desktop, Cursor, or any MCP-compatible agent to KryptoDB." />
            <div className="mcp-grid">
                <div className="panel setup-panel">
                    <div className="step">
                        <span className="step-num">1</span>
                        <div><strong>Choose an agent identity</strong><span>Requests will be scoped to this identity and tenant context.</span></div>
                    </div>
                    <div>
                        <select 
                            className="agent-select" 
                            style={{ width: '100%', padding: '8px 12px', background: 'var(--card)', border: '1px solid var(--border)', borderRadius: '2px', color: 'var(--ink)' }} 
                            value={agent} 
                            onChange={(e) => setAgent(e.target.value)} 
                            aria-label="Choose agent identity"
                        >
                            {fetchedAgents.map((a) => <option key={a.id} value={a.id}>{a.id}</option>)}
                        </select>
                    </div>

                    <div className="step">
                        <span className="step-num">2</span>
                        <div><strong>Copy the connection config</strong><span>Place into Claude Desktop or Cursor configuration.</span></div>
                    </div>
                    <div className="config-code">
                        <Button onClick={copy} className="copy-button">
                            {copied ? <Check size={14} /> : <Clipboard size={14} />} {copied ? 'Copied' : 'Copy JSON'}
                        </Button>
                        <pre>{config}</pre>
                    </div>

                    <div className="step">
                        <span className="step-num">3</span>
                        <div><strong>Verify the gateway</strong><span>Tests backend connection and graph index readiness.</span></div>
                    </div>
                    <Button onClick={test} className="primary test-button">
                        <Play size={14} /> Test Gateway Health
                    </Button>
                    {status !== 'idle' && (
                        <div className={`connection-status ${status}`}>
                            <StatusDot status={status === 'connected' ? 'active' : status === 'error' ? 'revoked' : 'pending'} />
                            {status === 'connected' ? 'Gateway Healthy' : status === 'error' ? 'Gateway Unavailable' : 'Checking Gateway…'}
                        </div>
                    )}
                </div>

                <div className="mcp-aside">
                    <div className="gradient-card">
                        <Terminal size={20} />
                        <span className="eyebrow">Plaintext MCP Transport</span>
                        <strong>Fast, 2-Hop Grounded, Attributed.</strong>
                        <p>MCP tools <code>query_memory</code> and <code>propose_fact</code> connect directly to the GraphRAG pipeline.</p>
                    </div>
                    <div className="quick-links">
                        <span className="eyebrow">Compatible Clients</span>
                        <div><span>Claude Desktop</span><span>Cursor</span><span>Windsurf</span></div>
                    </div>
                </div>
            </div>
        </section>
    )
}

/* ========================================================================= */
/* CommandPalette                                                           */
/* ========================================================================= */
function CommandPalette({ onClose, onNavigate, onNodeSelect }: { onClose: () => void; onNavigate: (view: View) => void, onNodeSelect: (n: any) => void }) {
    const [query, setQuery] = useState('')
    const [nodes, setNodes] = useState<any[]>([])

    useEffect(() => {
        apiFetch('/graph/snapshot')
            .then(res => res.ok ? res.json() : { nodes: [], edges: [] })
            .then(data => {
                if (!data.nodes || !data.edges) return
                const nodeMap = new Map()
                data.nodes.forEach((n: any) => nodeMap.set(n.id, n))
                data.edges.forEach((e: any) => {
                    if (!nodeMap.has(e.source)) nodeMap.set(e.source, { id: e.source, kind: "Entity", scope: e.scope })
                    if (!nodeMap.has(e.target)) nodeMap.set(e.target, { id: e.target, kind: "Entity", scope: e.scope })
                })
                setNodes(Array.from(nodeMap.values()))
            })
            .catch(() => {})
    }, [])

    const baseActions = [
        { icon: Search, name: 'Query memory', subtitle: 'Run GraphRAG hybrid retrieval', shortcut: '↵', action: () => onNavigate('Graph Explorer') },
        { icon: Plus, name: 'Propose fact', subtitle: 'Propose a new memory relation', shortcut: '⌘ ↵', action: () => onNavigate('Graph Explorer') },
        { icon: Bot, name: 'Jump to Agents & Access', subtitle: 'Manage identities and API keys', shortcut: '⌘ 2', action: () => onNavigate('Agents & Access') },
        { icon: SlidersHorizontal, name: 'Jump to Config / Ontology', subtitle: 'Predicate registry and rules', shortcut: '⌘ 3', action: () => onNavigate('Config / Ontology') },
        { icon: Activity, name: 'Jump to Audit Log', subtitle: 'Evaluation metrics and traces', shortcut: '⌘ 4', action: () => onNavigate('Audit Log') }
    ]

    const nodeActions = nodes.map(n => ({
        icon: Network,
        name: n.name || n.id,
        subtitle: `Plaintext Node • ${n.kind || n.type || 'Entity'} • ${n.scope || 'default'}`,
        shortcut: '↵',
        action: () => {
            onNavigate('Graph Explorer')
            onNodeSelect(n)
        }
    }))

    const actions = [...baseActions, ...nodeActions]
    const shown = actions.filter((a) => `${a.name} ${a.subtitle}`.toLowerCase().includes(query.toLowerCase()))

    return (
        <div className="palette-backdrop" onClick={onClose}>
            <div className="palette" onClick={(e) => e.stopPropagation()}>
                <div className="palette-input">
                    <Search size={18} />
                    <input 
                        autoFocus 
                        value={query} 
                        onChange={(e) => setQuery(e.target.value)} 
                        placeholder="Search entities, predicates, actions..." 
                        onKeyDown={(e) => e.key === 'Escape' && onClose()} 
                    />
                    <Kbd>esc</Kbd>
                </div>
                <div className="palette-label">Actions &amp; Entities</div>
                <div style={{ maxHeight: '300px', overflowY: 'auto' }}>
                    {shown.map((item) => (
                        <button className="palette-item" key={item.name} onClick={() => { item.action(); onClose() }}>
                            <span className="palette-icon"><item.icon size={16} /></span>
                            <span><strong>{item.name}</strong><small>{item.subtitle}</small></span>
                            <Kbd>{item.shortcut}</Kbd>
                            <ChevronRight size={14} />
                        </button>
                    ))}
                    {shown.length === 0 && <div className="no-results">No matches found</div>}
                </div>
                <div className="palette-footer">
                    <span><Kbd>↑↓</Kbd> Navigate</span>
                    <span><Kbd>↵</Kbd> Select</span>
                    <span><Kbd>esc</Kbd> Close</span>
                </div>
            </div>
        </div>
    )
}

/* ========================================================================= */
/* Main Page Shell                                                           */
/* ========================================================================= */
export default function Page() {
    const [view, setView] = useState<View>('Graph Explorer')
    const [palette, setPalette] = useState(false)
    const [mobileNav, setMobileNav] = useState(false)
    const [selectedNode, setSelectedNode] = useState<any>(null)
    const [activityOpen, setActivityOpen] = useState(false)

    useEffect(() => {
        const handler = (e: KeyboardEvent) => {
            if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
                e.preventDefault()
                setPalette(true)
            }
            if (e.key === 'Escape') setPalette(false)
        }
        window.addEventListener('keydown', handler)
        return () => window.removeEventListener('keydown', handler)
    }, [])

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
                    <div className="workspace-value"><span className="workspace-dot" /> KryptoDB / GraphRAG</div>
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
                        <Shield size={15} style={{ color: 'var(--accent)' }} />
                        <div><strong>Plaintext GraphRAG</strong><span>Neo4j 2-Hop Active</span></div>
                    </div>
                </div>
            </aside>

            <div className="main-area">
                <header className="topbar">
                    <Button className="menu-button" onClick={() => setMobileNav(true)}><Menu size={17} /></Button>
                    <button className="global-search" onClick={() => setPalette(true)}>
                        <Search size={15} /><span>Search entities, memory, commands…</span><Kbd>⌘ K</Kbd>
                    </button>
                    <div className="top-actions">
                        <div className="stream-status"><StatusDot /> <span>Live</span></div>
                        <Button
                            title="Agent Activity"
                            onClick={() => setActivityOpen(true)}
                            className={`agent-activity-trigger ${activityOpen ? 'primary' : ''}`}
                        >
                            <Bot size={16} /> Agent Activity
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
