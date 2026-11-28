import React, { useState, useEffect, useRef } from "react";
import {
    Activity, ArrowDownToLine, ArrowUpRight, Bot, Check, ChevronDown, ChevronRight,
    CircleHelp, Clipboard, Command, Database, Download, FileCode2, Filter, Gauge,
    GitBranch, GripHorizontal, KeyRound, LayoutDashboard, Lock, LogOut, Maximize2, Menu,
    Move, Network, PanelRight, Play, Plus, RefreshCw, RotateCcw, Search, Send, Settings2,
    Shield, SlidersHorizontal, Terminal, Timer, Trash2, Unlock, X, Zap
} from 'lucide-react';

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

function ScopeChip({ name }: { name: string }) { return <span className={`scope-chip scope-${name.toLowerCase().replace(/[^a-z]/g, '-')}`}><span className="scope-mark" />{name}</span> }
function Button({ children, className = '', onClick, title, style }: { children: React.ReactNode; className?: string; onClick?: () => void; title?: string; style?: React.CSSProperties }) { return <button title={title} onClick={onClick} style={style} className={`ui-button ${className}`}>{children}</button> }

type ChatItem =
    | { id: string; role: 'user'; text: string }
    | { id: string; role: 'agent'; text: string; steps?: string[]; isThinkingCollapsed?: boolean }
    | { id: string; role: 'system'; type: 'recalled' | 'denied' | 'committed' | 'write_rejected'; count?: number; scope: string; reason?: string; nodeId?: string; fact?: string }

export default function AgentActivityModal({ onClose, onSelectNode }: { onClose: () => void; onSelectNode?: (n: any) => void }) {
    const [pendingState, setPendingState] = useState<'pending' | 'approved' | 'rejected' | null>(null)
    const [pendingFact, setPendingFact] = useState<{ tuple: string; scope: string; subject?: string; predicate?: string; obj?: string; description?: string; quote?: string } | null>(null)
    const [inputMsg, setInputMsg] = useState('')
    const [isSending, setIsSending] = useState(false)
    const [threadId, setThreadId] = useState<string>('')
    const [currentScope, setCurrentScope] = useState('AUTO')
    const currentScopeRef = useRef(currentScope)
    useEffect(() => { currentScopeRef.current = currentScope }, [currentScope])
    const [availableScopes, setAvailableScopes] = useState<string[]>(['AUTO', 'research', 'PROJECT:synapse', 'PROJECT:kryptodb', 'CONFIDENTIAL:vault'])
    const [showScopePicker, setShowScopePicker] = useState(false)
    const [agentsData, setAgentsData] = useState<Record<string, BackendAgent>>({})
    const [agentsLoading, setAgentsLoading] = useState(true)
    const [agentsError, setAgentsError] = useState('')
    const [messages, setMessages] = useState<ChatItem[]>([])

    const [selectedAgent, setSelectedAgent] = useState('')

    useEffect(() => {
        if (typeof window !== 'undefined') {
            const savedAgent = window.localStorage.getItem('kryptodb_active_agent')
            if (savedAgent) setSelectedAgent(savedAgent)
        }
    }, [])
    const [availableAgents, setAvailableAgents] = useState<BackendAgent[]>([])
    const [showAgentPicker, setShowAgentPicker] = useState(false)

    const [selectedModel, setSelectedModel] = useState('gemini-2.5-flash')
    const [availableModels, setAvailableModels] = useState<string[]>([
        'gemini-2.5-flash',
        'gemini-1.5-flash',
        'gemini-2.0-flash',
        'llama3.2',
        'llama3.1',
        'mistral',
        'qwen2.5',
        'gpt-4o-mini',
        'deterministic-engine'
    ])
    const [showModelPicker, setShowModelPicker] = useState(false)
    const [ollamaUrl, setOllamaUrl] = useState('http://localhost:11434')

    const [wsStatus, setWsStatus] = useState<'connecting' | 'connected' | 'disconnected'>('connecting')
    const [wsRetryCount, setWsRetryCount] = useState(0)

    const wsRef = useRef<WebSocket | null>(null)
    const messagesEndRef = useRef<HTMLDivElement>(null)
    // Keep model output out of the transcript until the backend has completed
    // recall and reasoning. This prevents pre-tool text such as "I don't
    // know" from flashing before the memory query returns.
    const pendingAgentTextRef = useRef('')

    useEffect(() => {
        messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
    }, [messages])

    useEffect(() => {
        if (!threadId) return
        const apiKey = typeof window !== 'undefined' ? (window.localStorage.getItem('kryptodb_api_key') || '') : ''
        if (!apiKey) {
            setWsStatus('disconnected')
            return
        }
        setWsStatus('connecting')
        const wsUrl = `${API_BASE.replace(/^http/, 'ws')}/chat/${threadId}/stream?token=${encodeURIComponent(apiKey)}`
        const ws = new WebSocket(wsUrl)
        wsRef.current = ws

        ws.onopen = () => {
            console.log("WebSocket connected!")
            setWsStatus('connected')
            setIsSending(false)
        }
        ws.onerror = (err) => {
            console.error("WebSocket error:", err)
            setIsSending(false)
        }
        ws.onclose = () => {
            console.log("WebSocket connection closed.")
            if (wsRef.current === ws) wsRef.current = null
            setWsStatus('disconnected')
            setIsSending(false)
            // Auto reconnect after 3 seconds
            setTimeout(() => setWsRetryCount(c => c + 1), 3000)
        }
        
        ws.onmessage = (e) => {
            const data = JSON.parse(e.data)
            if (data.type === "denied") {
                setMessages(prev => [...prev, { id: String(Date.now()) + Math.random(), role: 'agent', text: `Access denied: ${data.reason || data.detail || 'Unauthorized'}` }])
                setIsSending(false)
                return
            }
            console.log("WebSocket message received:", data)
            if (data.type === 'agent_status') {
                setMessages(prev => {
                    const idx = prev.findIndex(m => m.id === 'streaming')
                    if (idx !== -1) {
                        const target = prev[idx]
                        if (target.role !== 'agent') return prev
                        const updated = { ...target, steps: [...(target.steps || []), data.status] }
                        const newArr = [...prev]
                        newArr[idx] = updated
                        return newArr
                    } else {
                        return [...prev, { id: 'streaming', role: 'agent', text: '', steps: [data.status], isThinkingCollapsed: false }]
                    }
                })
            } else if (data.type === 'agent_token') {
                let newText = pendingAgentTextRef.current
                
                let contentText = ''
                if (typeof data.content === 'string') {
                    contentText = data.content
                } else if (Array.isArray(data.content)) {
                    contentText = data.content.map((b: any) => b.text || '').join('')
                } else if (data.content) {
                    contentText = String(data.content)
                }

                if (data.is_chunk === false) {
                    if (contentText) {
                        newText = contentText
                    }
                } else {
                    newText += contentText
                }
                
                pendingAgentTextRef.current = newText
                
                setMessages(prev => {
                    const idx = prev.findIndex(m => m.id === 'streaming')
                    if (idx !== -1) {
                        const target = prev[idx]
                        if (target.role === 'agent') {
                            const updated: ChatItem = { ...target, text: newText }
                            const newArr = [...prev]
                            newArr[idx] = updated
                            return newArr
                        }
                    }
                    return [...prev, { id: 'streaming', role: 'agent', text: newText, steps: [], isThinkingCollapsed: false }]
                })
            } else if (data.type === 'recall_event') {
                setMessages(prev => [...prev, { id: String(Date.now()) + Math.random(), role: 'system', type: 'recalled', count: data.count, scope: data.scope, nodeId: 'n4' }])
            } else if (data.type === 'denial_event') {
                setMessages(prev => [...prev, { id: String(Date.now()) + Math.random(), role: 'system', type: 'denied', scope: data.scope, reason: data.reason, nodeId: 'n7' }])
            } else if (data.type === 'pending_approval') {
                setPendingState('pending')
                const pf = data.fact || {}
                setPendingFact({
                    subject: pf.subject || '',
                    predicate: pf.predicate || '',
                    obj: pf.obj || '',
                    description: pf.description || `${pf.subject || ''} ${pf.predicate || ''} ${pf.obj || ''}`.trim(),
                    quote: pf.quote || pf.quote_substring || '',
                    tuple: pf.fact_tuple ? `(${pf.fact_tuple.join(', ')})` : `(${pf.subject || 'Entity'}, ${pf.predicate || 'PREDICATE'}, ${pf.obj || 'Object'})`,
                    scope: pf.scope || currentScopeRef.current
                })
                setIsSending(false)
            } else if (data.type === 'error') {
                setIsSending(false)
                setMessages(prev => [...prev, {
                    id: String(Date.now()) + Math.random(),
                    role: 'agent',
                    text: data.message || 'The agent could not complete this request.'
                }])
            } else if (data.type === 'done') {
                setIsSending(false)
                const finalText = pendingAgentTextRef.current
                pendingAgentTextRef.current = ''
                
                setMessages(prev => {
                    const idx = prev.findIndex(m => m.id === 'streaming')
                    if (idx !== -1) {
                        const target = prev[idx]
                        const updated = { ...target, id: String(Date.now()) + Math.random(), text: finalText, isThinkingCollapsed: true }
                        const newArr = [...prev]
                        newArr[idx] = updated
                        return newArr
                    }
                    if (finalText) {
                        return [...prev, { id: String(Date.now()) + Math.random(), role: 'agent', text: finalText, isThinkingCollapsed: true }]
                    }
                    return prev
                })
            }
        }
        
        return () => ws.close()
    }, [threadId, wsRetryCount])

    // Dragging & Resizing States
    const [position, setPosition] = useState<{ x: number; y: number } | null>(null)
    const [size, setSize] = useState<{ width: number; height: number }>({ width: 440, height: 560 })
    const [isDragging, setIsDragging] = useState(false)
    const [isResizing, setIsResizing] = useState<string | null>(null)

    const dragStartRef = useRef<{ startX: number; startY: number; posX: number; posY: number } | null>(null)
    const resizeStartRef = useRef<{
        handle: string
        startX: number
        startY: number
        startWidth: number
        startHeight: number
        startXPos: number
        startYPos: number
    } | null>(null)

    useEffect(() => {
        if (typeof window !== 'undefined' && position === null) {
            const initWidth = Math.min(440, window.innerWidth - 32)
            const initHeight = Math.min(560, window.innerHeight - 80)
            setSize({ width: initWidth, height: initHeight })
            setPosition({
                x: Math.max(16, window.innerWidth - initWidth - 24),
                y: Math.max(16, window.innerHeight - initHeight - 24)
            })
        }
    }, [])

    const handleDragStart = (e: React.MouseEvent) => {
        if ((e.target as HTMLElement).closest('button, input, select, .model-picker-popover, .drag-ignore')) {
            return
        }
        e.preventDefault()
        setIsDragging(true)
        const currentX = position ? position.x : (window.innerWidth - size.width - 24)
        const currentY = position ? position.y : (window.innerHeight - size.height - 24)
        dragStartRef.current = {
            startX: e.clientX,
            startY: e.clientY,
            posX: currentX,
            posY: currentY
        }
    }

    useEffect(() => {
        if (!isDragging) return

        const handleMouseMove = (e: MouseEvent) => {
            if (!dragStartRef.current) return
            const dx = e.clientX - dragStartRef.current.startX
            const dy = e.clientY - dragStartRef.current.startY
            const newX = Math.max(8, Math.min(window.innerWidth - size.width - 8, dragStartRef.current.posX + dx))
            const newY = Math.max(8, Math.min(window.innerHeight - size.height - 8, dragStartRef.current.posY + dy))
            setPosition({ x: newX, y: newY })
        }

        const handleMouseUp = () => {
            setIsDragging(false)
            dragStartRef.current = null
        }

        window.addEventListener('mousemove', handleMouseMove)
        window.addEventListener('mouseup', handleMouseUp)
        return () => {
            window.removeEventListener('mousemove', handleMouseMove)
            window.removeEventListener('mouseup', handleMouseUp)
        }
    }, [isDragging, size])

    const handleResizeStart = (e: React.MouseEvent, handle: string) => {
        e.preventDefault()
        e.stopPropagation()
        setIsResizing(handle)
        const currentX = position ? position.x : (window.innerWidth - size.width - 24)
        const currentY = position ? position.y : (window.innerHeight - size.height - 24)
        resizeStartRef.current = {
            handle,
            startX: e.clientX,
            startY: e.clientY,
            startWidth: size.width,
            startHeight: size.height,
            startXPos: currentX,
            startYPos: currentY
        }
    }

    useEffect(() => {
        if (!isResizing) return

        const handleMouseMove = (e: MouseEvent) => {
            if (!resizeStartRef.current) return
            const { handle, startX, startY, startWidth, startHeight, startXPos, startYPos } = resizeStartRef.current
            const dx = e.clientX - startX
            const dy = e.clientY - startY

            const MIN_W = 300
            const MIN_H = 320
            const MAX_W = Math.max(MIN_W, window.innerWidth - 24)
            const MAX_H = Math.max(MIN_H, window.innerHeight - 24)

            let newW = startWidth
            let newH = startHeight
            let newX = startXPos
            let newY = startYPos

            if (handle.includes('e')) {
                newW = Math.min(MAX_W, Math.max(MIN_W, startWidth + dx))
            }
            if (handle.includes('s')) {
                newH = Math.min(MAX_H, Math.max(MIN_H, startHeight + dy))
            }
            if (handle.includes('w')) {
                const rawW = startWidth - dx
                newW = Math.min(MAX_W, Math.max(MIN_W, rawW))
                newX = startXPos + (startWidth - newW)
            }
            if (handle.includes('n')) {
                const rawH = startHeight - dy
                newH = Math.min(MAX_H, Math.max(MIN_H, rawH))
                newY = startYPos + (startHeight - newH)
            }

            setSize({ width: newW, height: newH })
            setPosition({ x: newX, y: newY })
        }

        const handleMouseUp = () => {
            setIsResizing(null)
            resizeStartRef.current = null
        }

        window.addEventListener('mousemove', handleMouseMove)
        window.addEventListener('mouseup', handleMouseUp)
        return () => {
            window.removeEventListener('mousemove', handleMouseMove)
            window.removeEventListener('mouseup', handleMouseUp)
        }
    }, [isResizing])

    const handleResetChat = () => {
        setThreadId('session_' + Math.random().toString(36).substring(2, 8))
        setMessages([])
        setPendingState(null)
        setPendingFact(null)
    }

    useEffect(() => {
        const syncAgent = () => {
            if (typeof window !== 'undefined') {
                const saved = window.localStorage.getItem('kryptodb_active_agent')
                if (saved) setSelectedAgent(saved)
            }
        }
        syncAgent()
        window.addEventListener('kryptodb_active_agent_changed', syncAgent)
        return () => window.removeEventListener('kryptodb_active_agent_changed', syncAgent)
    }, [])

    useEffect(() => {
        setAgentsLoading(true)
        setAgentsError('')
        apiFetch('/agents')
            .then(r => r.ok ? r.json() : null)
            .then(data => {
                const list = normalizeAgents(data)
                if (list.length > 0) {
                    setAgentsData(Object.fromEntries(list.map((agent: BackendAgent) => [agent.id, agent])))
                    setAvailableAgents(list)
                    if (!list.some((agent: BackendAgent) => agent.id === selectedAgent)) setSelectedAgent(list[0].id)
                    const rawScopes = (list.find((agent: BackendAgent) => agent.id === selectedAgent) || list[0]).allowed_scopes || []
                    const agentScopes = Array.from(new Set(['AUTO', ...rawScopes]))
                    if (agentScopes.length > 0) {
                        setAvailableScopes(agentScopes)
                        if (!agentScopes.includes(currentScope)) {
                            setCurrentScope('AUTO')
                        }
                    }
                } else {
                    setAvailableAgents([])
                    setAgentsError('No agent identities are registered with the gateway.')
                }
            })
            .catch(() => setAgentsError('Could not load live agents from the gateway.'))
            .finally(() => setAgentsLoading(false))
    }, [selectedAgent])

    const handleSelectAgent = (agentName: string) => {
        setSelectedAgent(agentName)
        if (typeof window !== 'undefined') {
            window.localStorage.setItem('kryptodb_active_agent', agentName)
            window.dispatchEvent(new Event('kryptodb_active_agent_changed'))
        }
        const rawScopes = agentsData[agentName]?.allowed_scopes || []
        const agentScopes = Array.from(new Set(['AUTO', ...rawScopes]))
        if (agentScopes.length > 0) {
            setAvailableScopes(agentScopes)
            if (!agentScopes.includes(currentScope)) {
                setCurrentScope('AUTO')
            }
        }
        setShowAgentPicker(false)
    }

    useEffect(() => {
        if (!threadId) {
            setThreadId('session_' + Math.random().toString(36).substring(2, 8))
            return
        }
        apiFetch(`/chat/${threadId}/history`)
            .then(r => r.ok ? r.json() : null)
            .then(data => {
                if (data && Array.isArray(data.messages) && data.messages.length > 0) {
                    const loaded: ChatItem[] = data.messages.map((m: any, idx: number) => ({
                        id: String(idx),
                        role: m.role === 'user' ? 'user' : 'agent',
                        text: m.content || m.text || ''
                    }))
                    setMessages(loaded)
                }
            })
            .catch(() => {})

        apiFetch('/models')
            .then(r => r.ok ? r.json() : null)
            .then(data => {
                if (data) {
                    if (data.active_model) setSelectedModel(data.active_model)
                    if (data.available_models && Array.isArray(data.available_models)) setAvailableModels(data.available_models)
                    if (data.ollama_url) setOllamaUrl(data.ollama_url)
                }
            })
            .catch(() => {})
    }, [threadId])

    const handleSelectModel = async (model: string) => {
        setSelectedModel(model)
        setShowModelPicker(false)
        try {
            await apiFetch('/config/model', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ model })
            })
        } catch (e) {
            console.warn('Model switch API call failed:', e)
        }
    }

    const handleApprove = async () => {
        setPendingState('approved')
        const factDesc = pendingFact ? (pendingFact.description || `${pendingFact.subject || ''} ${pendingFact.predicate || ''} ${pendingFact.obj || ''}`.trim()) : ''
        setMessages(prev => [
            ...prev,
            { id: String(Date.now()), role: 'system', type: 'committed', fact: factDesc, scope: pendingFact?.scope || currentScope, nodeId: 'n1' }
        ])
        if (threadId) {
            try {
                await apiFetch('/agent/approve', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ thread_id: threadId, approved: true })
                })
            } catch (e) {
                console.warn('Approve call failed:', e)
            }
        }
    }

    const handleReject = async () => {
        setPendingState('rejected')
        const factDesc = pendingFact ? (pendingFact.description || `${pendingFact.subject || ''} ${pendingFact.predicate || ''} ${pendingFact.obj || ''}`.trim()) : ''
        setMessages(prev => [
            ...prev,
            { id: String(Date.now()), role: 'system', type: 'write_rejected', fact: factDesc, scope: pendingFact?.scope || currentScope, reason: 'user_declined' }
        ])
        if (threadId) {
            try {
                await apiFetch('/agent/approve', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ thread_id: threadId, approved: false })
                })
            } catch (e) {
                console.warn('Reject call failed:', e)
            }
        }
    }

    const handleSendMessage = async () => {
        if (!inputMsg.trim()) return
        
        // If WebSocket is not ready, we can't send.
        if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
             setMessages(prev => [...prev, { id: String(Date.now()), role: 'agent', text: 'Error: WebSocket not connected. Reconnecting... please wait.' }])
             if (wsStatus === 'disconnected') setWsRetryCount(c => c + 1)
             return
        }
        
        const userText = inputMsg
        setInputMsg('')
        setIsSending(true)
        pendingAgentTextRef.current = ''

        const userMsgId = String(Date.now())
        setMessages(prev => [...prev, { id: userMsgId, role: 'user', text: userText }])

        try {
            wsRef.current.send(JSON.stringify({
                message: userText,
                agent_id: selectedAgent,
                user_scope: currentScope
            }))
        } catch (e) {
            console.error('WS send error', e)
            setIsSending(false)
        }
    }

    const handleSystemClick = (nodeId?: string) => {
        if (nodeId && onSelectNode) {
            onSelectNode({ id: nodeId, label: nodeId })
        }
    }

    return (
        <div
            className={`agent-activity-floating-popup ${isDragging ? 'is-dragging' : ''}`}
            style={{
                position: 'fixed',
                left: position ? `${position.x}px` : undefined,
                top: position ? `${position.y}px` : undefined,
                right: position ? 'auto' : '24px',
                bottom: position ? 'auto' : '24px',
                width: `${size.width}px`,
                height: `${size.height}px`,
            }}
        >
            {/* Resize Handles */}
            <div className="resize-handle resize-handle-nw" onMouseDown={(e) => handleResizeStart(e, 'nw')} />
            <div className="resize-handle resize-handle-ne" onMouseDown={(e) => handleResizeStart(e, 'ne')} />
            <div className="resize-handle resize-handle-sw" onMouseDown={(e) => handleResizeStart(e, 'sw')} />
            <div className="resize-handle resize-handle-se" onMouseDown={(e) => handleResizeStart(e, 'se')} />
            <div className="resize-handle resize-handle-n" onMouseDown={(e) => handleResizeStart(e, 'n')} />
            <div className="resize-handle resize-handle-s" onMouseDown={(e) => handleResizeStart(e, 's')} />
            <div className="resize-handle resize-handle-w" onMouseDown={(e) => handleResizeStart(e, 'w')} />
            <div className="resize-handle resize-handle-e" onMouseDown={(e) => handleResizeStart(e, 'e')} />

            {/* Header */}
            <div className="activity-header" onMouseDown={handleDragStart}>
                <div className="activity-header-top">
                    <div className="session-title-group">
                        <span className="drag-grip" title="Drag to move chat window">
                            <GripHorizontal size={14} />
                        </span>
                        <span className="live-badge" title={wsStatus === 'connected' ? 'Connected' : wsStatus === 'connecting' ? 'Connecting...' : 'Offline (Auto-reconnecting)'}>
                            <span className={`status-dot ${wsStatus === 'connected' ? 'active' : wsStatus === 'connecting' ? 'pending' : 'failed'}`} /> 
                            {wsStatus === 'connected' ? 'live' : wsStatus === 'connecting' ? 'connecting' : 'offline'}
                        </span>
                        <span className="session-id">{threadId}</span>
                    </div>
                    <div className="header-actions-group drag-ignore">
                        <button className="header-action-btn" onClick={handleResetChat} title="New conversation">
                            <RotateCcw size={13} />
                        </button>
                        <button className="header-action-btn" onClick={onClose} title="Minimize chat">
                            <X size={14} />
                        </button>
                    </div>
                </div>

                <div className="activity-header-toolbar drag-ignore">
                    <button
                        className="model-selector-badge"
                        onClick={() => { setShowScopePicker(!showScopePicker); setShowAgentPicker(false); setShowModelPicker(false); }}
                        title="Scope Routing Mode"
                    >
                        <Shield size={11} className="zap-icon" style={{ color: '#4facfe' }} />
                        <span>{currentScope === 'AUTO' ? 'Auto (LangGraph)' : currentScope}</span>
                        <ChevronDown size={10} />
                    </button>
                    <button
                        className="model-selector-badge"
                        onClick={() => { setShowAgentPicker(!showAgentPicker); setShowScopePicker(false); setShowModelPicker(false); }}
                        title="Select Agent Identity"
                    >
                        <Bot size={11} className="zap-icon" style={{ color: '#57dfae' }} />
                        <span>{selectedAgent}</span>
                        <ChevronDown size={10} />
                    </button>
                    <button
                        className="model-selector-badge"
                        onClick={() => { setShowModelPicker(!showModelPicker); setShowScopePicker(false); setShowAgentPicker(false); }}
                        title="Select LLM Model"
                    >
                        <Zap size={11} className="zap-icon" />
                        <span>{selectedModel}</span>
                        <ChevronDown size={10} />
                    </button>
                </div>
            </div>

            {/* Scope Picker Popover */}
            {showScopePicker && (
                <div className="model-picker-popover">
                    <div className="model-picker-header">
                        <div className="model-picker-title">
                            <Shield size={14} />
                            <span>Memory Scope Routing</span>
                        </div>
                        <button className="close-picker-btn" onClick={() => setShowScopePicker(false)}><X size={14} /></button>
                    </div>
                    <div className="model-picker-sub">
                        LangGraph autonomously selects and queries the relevant authorized scopes based on context.
                    </div>
                    <div className="model-picker-list">
                        {availableScopes.map(s => (
                            <button
                                key={s}
                                className={`model-option ${currentScope === s ? 'selected' : ''}`}
                                onClick={() => { setCurrentScope(s); setShowScopePicker(false); }}
                            >
                                <div className="model-info">
                                    <span className="model-name">{s === 'AUTO' ? 'Auto (Autonomous LangGraph Routing)' : s}</span>
                                    <span className="model-tag">{s === 'AUTO' ? 'LangGraph AI' : 'Scope'}</span>
                                </div>
                                {currentScope === s && <Check size={14} className="check-icon" />}
                            </button>
                        ))}
                    </div>
                </div>
            )}

            {/* Agent Picker Popover */}
            {showAgentPicker && (
                <div className="model-picker-popover">
                    <div className="model-picker-header">
                        <div className="model-picker-title">
                            <Bot size={14} />
                            <span>Select Agent Identity</span>
                        </div>
                        <button className="close-picker-btn" onClick={() => setShowAgentPicker(false)}><X size={14} /></button>
                    </div>
                    <div className="model-picker-sub">
                        Requests will be authorized and logged under this agent identity.
                    </div>
                    <div className="model-picker-list">
                        {agentsLoading && <div className="agent-picker-state">Loading live agents…</div>}
                        {!agentsLoading && agentsError && <div className="agent-picker-state error">{agentsError}</div>}
                        {!agentsLoading && !agentsError && availableAgents.map(a => (
                            <button
                                key={a.id}
                                className={`model-option ${selectedAgent === a.id ? 'selected' : ''}`}
                                onClick={() => handleSelectAgent(a.id)}
                            >
                                <div className="model-info">
                                    <span className="model-name">{a.id}</span>
                                    <span className="model-tag">{a.allowed_scopes.length} authorized scope{a.allowed_scopes.length === 1 ? '' : 's'}</span>
                                </div>
                                {selectedAgent === a.id && <Check size={14} className="check-icon" />}
                            </button>
                        ))}
                    </div>
                </div>
            )}

            {/* Model Selector Popover */}
            {showModelPicker && (
                <div className="model-picker-popover">
                    <div className="model-picker-header">
                        <div className="model-picker-title">
                            <SlidersHorizontal size={14} />
                            <span>Select LLM Model</span>
                        </div>
                        <button className="close-picker-btn" onClick={() => setShowModelPicker(false)}><X size={14} /></button>
                    </div>
                    <div className="model-picker-sub">
                        Ollama Engine URL: <code>{ollamaUrl}</code>
                    </div>
                    <div className="model-picker-list">
                        {availableModels.map(m => (
                            <button
                                key={m}
                                className={`model-option ${selectedModel === m ? 'selected' : ''}`}
                                onClick={() => handleSelectModel(m)}
                            >
                                <div className="model-info">
                                    <span className="model-name">{m}</span>
                                    <span className="model-tag">{m.startsWith('gemini') ? 'Google Gemini (Free)' : m.startsWith('gpt') ? 'OpenAI Cloud' : m === 'deterministic-engine' ? 'KryptoDB Core Engine' : 'Ollama Model'}</span>
                                </div>
                                {selectedModel === m && <Check size={14} className="check-icon" />}
                            </button>
                        ))}
                    </div>
                </div>
            )}

            {/* Body Transcript */}
            <div className="activity-body">
                {messages.length === 0 && !isSending && (
                    <div className="empty-chat-state">
                        <Bot size={28} className="empty-chat-icon" />
                        <p className="empty-chat-title">KryptoDB Agent Active</p>
                        <span className="empty-chat-sub">Ask a query to perform graph memory recall or propose memory facts.</span>
                    </div>
                )}

                {messages.map(msg => {
                    if (msg.role === 'user') {
                        return (
                            <div key={msg.id} className="chat-turn user">
                                <div className="chat-bubble user-bubble">
                                    {msg.text}
                                </div>
                            </div>
                        )
                    }

                    if (msg.role === 'agent') {
                        return (
                            <div key={msg.id} className="chat-turn agent">
                                {msg.steps && msg.steps.length > 0 && (
                                    <div style={{ marginBottom: '8px', fontSize: '0.85em', color: 'var(--muted-foreground)', background: 'var(--surface)', padding: '8px 12px', borderRadius: '2px', border: '1px solid var(--border)' }}>
                                        <div 
                                            style={{ cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '6px', userSelect: 'none' }}
                                            onClick={() => setMessages(prev => prev.map(m => m.id === msg.id && m.role === 'agent' ? { ...m, isThinkingCollapsed: !m.isThinkingCollapsed } : m))}
                                        >
                                            <ChevronDown size={12} style={{ transform: msg.isThinkingCollapsed ? 'rotate(-90deg)' : 'rotate(0deg)', transition: 'transform 0.2s' }} />
                                            <span style={{ fontWeight: 600 }}>Thinking Process</span>
                                        </div>
                                        {!msg.isThinkingCollapsed && (
                                            <div style={{ marginTop: '8px', paddingLeft: '18px', display: 'flex', flexDirection: 'column', gap: '6px', borderLeft: '1px solid rgba(255,255,255,0.1)', marginLeft: '5px' }}>
                                                {msg.steps.map((s, i) => <div key={i}>{s}</div>)}
                                            </div>
                                        )}
                                    </div>
                                )}
                                {msg.text && (
                                    <div className="chat-bubble agent-bubble">
                                        {msg.text}
                                    </div>
                                )}
                            </div>
                        )
                    }

                    if (msg.role === 'system') {
                        return (
                            <div
                                key={msg.id}
                                className={`inline-narration ${msg.type}`}
                                onClick={() => handleSystemClick(msg.nodeId)}
                                title="Click to highlight graph node"
                            >
                                <span className="narration-title">
                                    ↳ {msg.type === 'recalled'
                                        ? `recalled ${msg.count || 1} fact${(msg.count || 1) > 1 ? 's' : ''}`
                                        : msg.type === 'committed'
                                        ? `Fact committed: ${msg.fact || 'Memory updated'}`
                                        : msg.type === 'write_rejected'
                                        ? 'Write rejected by reviewer'
                                        : 'recall denied'}
                                </span>
                                <div className="narration-meta">
                                    <ScopeChip name={msg.scope} />
                                    {msg.reason && <span className="reason-badge">reason: {msg.reason}</span>}
                                </div>
                            </div>
                        )
                    }

                    return null
                })}

                {/* Inline Pending Approval Card */}
                {pendingState === 'pending' && pendingFact && (
                    <div className="pending-approval-card">
                        <div className="pending-card-header">
                            <span className="pulsing-dot" />
                            <span>pending approval</span>
                        </div>
                        <div className="pending-fact-statement" style={{ fontWeight: 600, fontSize: '13px', margin: '6px 0 2px 0' }}>
                            {pendingFact.description || pendingFact.tuple}
                        </div>
                        <div className="pending-fact-tuple" style={{ fontSize: '11px', color: '#94a3b8' }}>
                            Tuple: {pendingFact.tuple}
                        </div>
                        {pendingFact.quote && (
                            <div className="pending-quote" style={{ fontSize: '11px', fontStyle: 'italic', color: '#cbd5e1', margin: '4px 0', borderLeft: '2px solid #38bdf8', paddingLeft: '6px' }}>
                                Quote: "{pendingFact.quote}"
                            </div>
                        )}
                        <div className="pending-scope-row" style={{ marginTop: '4px' }}>
                            <span>scope:</span>
                            <ScopeChip name={pendingFact.scope} />
                        </div>
                        <div className="pending-actions">
                            <Button className="danger" onClick={handleReject}>Reject</Button>
                            <Button className="primary" onClick={handleApprove}>Approve & Write</Button>
                        </div>
                    </div>
                )}
                <div ref={messagesEndRef} />
            </div>

            {/* Bottom Input Bar */}
            <div className="chat-input-bar">
                <input
                    type="text"
                    value={inputMsg}
                    onChange={e => setInputMsg(e.target.value)}
                    onKeyDown={e => e.key === 'Enter' && handleSendMessage()}
                    placeholder="Ask about the graph..."
                />
                <button className="send-btn" onClick={handleSendMessage} aria-label="Send message">
                    {isSending ? <RefreshCw className="animate-spin" size={15} /> : <Send size={15} />}
                </button>
            </div>
        </div>
    )
}

