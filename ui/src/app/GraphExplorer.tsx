"use client";

import React, { useEffect, useState, useRef, useCallback } from "react";
import dynamic from "next/dynamic";
import { Maximize2, Plus, RefreshCw, Filter, ChevronDown } from "lucide-react";

// Dynamically import ForceGraph2D to avoid SSR issues with canvas
const ForceGraph2D = dynamic(() => import("react-force-graph-2d"), { ssr: false });

const API_BASE = process.env.NEXT_PUBLIC_KRYPTODB_URL || (typeof window !== 'undefined' ? `http://${window.location.hostname}:8000` : "http://127.0.0.1:8000");
const WS_BASE = API_BASE.replace(/^http/, "ws") + "/ws";

interface NodeData {
  id: string;
  type: string;
  scope: string;
  x?: number;
  y?: number;
  fx?: number;
  fy?: number;
  lastTraversedTime?: number;
}

interface EdgeData {
  id: string;
  source: string;
  target: string;
  predicate: string;
  scope: string;
  trust_weight: number;
  historical?: boolean;
}

export default function LiveGraph({ selected, onSelect, hopDepth }: any) {
  const [agentId, setAgentId] = useState("admin");
  const [graphData, setGraphData] = useState<{ nodes: NodeData[]; links: EdgeData[] }>({ nodes: [], links: [] });
  const [activeScopes, setActiveScopes] = useState<string[]>([]);
  const [allScopes, setAllScopes] = useState<string[]>([]);
  const [isConnected, setIsConnected] = useState(false);
  
  const ws = useRef<WebSocket | null>(null);
  const fgRef = useRef<any>(null);
  const graphDataRef = useRef(graphData);
  const eventQueue = useRef<any[]>([]);
  const debounceTimer = useRef<any>(null);
  const [animTick, setAnimTick] = useState(0); 

  useEffect(() => {
    graphDataRef.current = graphData;
  }, [graphData]);

  const configureGraphLayout = useCallback((graph: any) => {
    // Keep connected knowledge points compact. A very small charge prevents
    // exact overlaps without recreating the wide spacing of the default force.
    graph.d3Force('charge').strength(-8).distanceMax(90);
    graph.d3Force('link').distance(26).strength(1);
    graph.d3ReheatSimulation();
  }, []);

  useEffect(() => {
    // The force-graph ref is populated after render. Reapply the compact
    // layout whenever nodes or links arrive, including live graph updates.
    if (fgRef.current) configureGraphLayout(fgRef.current);
  }, [graphData.nodes.length, graphData.links.length, configureGraphLayout]);

  const loadSnapshot = async () => {
    try {
      const apiKey = window.localStorage.getItem('kryptodb_api_key');
      const res = await fetch(`${API_BASE}/graph/snapshot?agent_id=${agentId}`, {
        headers: apiKey ? { Authorization: `Bearer ${apiKey}` } : {},
      });
      if (res.ok) {
        const data = await res.json();
        const nodeMap = new Map();
        data.nodes.forEach((n: any) => nodeMap.set(n.id, n));
        data.edges.forEach((e: any) => {
          if (!nodeMap.has(e.source)) nodeMap.set(e.source, { id: e.source, type: "Unknown", scope: e.scope });
          if (!nodeMap.has(e.target)) nodeMap.set(e.target, { id: e.target, type: "Unknown", scope: e.scope });
        });
        setGraphData({ nodes: Array.from(nodeMap.values()), links: data.edges });
      }
    } catch (e) {
      console.error("Failed to fetch graph snapshot:", e);
    }
  };

  const processEventQueue = useCallback(() => {
    if (eventQueue.current.length === 0) return;
    const events = [...eventQueue.current];
    
    setGraphData(prev => {
      const newNodes = new Map(prev.nodes.map(n => [n.id, n]));
      const newLinks = new Map(prev.links.map(l => [l.id, l]));
      
      for (const ev of events) {
        if (ev.event_type === "traversal_step") {
           if (newNodes.has(ev.source_id)) {
              newNodes.set(ev.source_id, { ...newNodes.get(ev.source_id)!, lastTraversedTime: Date.now() });
           }
           if (newNodes.has(ev.target_id)) {
              newNodes.set(ev.target_id, { ...newNodes.get(ev.target_id)!, lastTraversedTime: Date.now() });
           }
        } else if (ev.event_type === "edge_created") {
           if (!newNodes.has(ev.source_id)) newNodes.set(ev.source_id, { id: ev.source_id, type: "Unknown", scope: ev.permission_scope });
           if (!newNodes.has(ev.target_id)) newNodes.set(ev.target_id, { id: ev.target_id, type: "Unknown", scope: ev.permission_scope });
           if (ev.edge_id && !newLinks.has(ev.edge_id)) {
             newLinks.set(ev.edge_id, {
               id: ev.edge_id,
               source: ev.source_id,
               target: ev.target_id,
               predicate: "FACT",
               scope: ev.permission_scope,
               trust_weight: ev.trust_weight || 0.5,
               historical: false as any
             });
           }
        }
      }
      
      eventQueue.current = [];
      return { nodes: Array.from(newNodes.values()), links: Array.from(newLinks.values()) };
    });
  }, []);

  const connectWebSocket = useCallback(() => {
    if (ws.current) ws.current.close();
    const apiKey = window.localStorage.getItem("kryptodb_api_key");
    // Pass the API key as a URL query param — WebSocket subprotocol strings
    // are restricted to RFC 7230 token characters and reject arbitrary key values.
    const url = new URL(`${WS_BASE}/graph-events`);
    url.searchParams.set("agent_id", agentId);
    if (apiKey) url.searchParams.set("api_key", apiKey);
    ws.current = new WebSocket(url.toString());
    ws.current.onopen = () => setIsConnected(true);
    ws.current.onclose = () => setIsConnected(false);
    
    ws.current.onmessage = (event) => {
      const data = JSON.parse(event.data);
      if (data.event_type === "traversal_step") {
         if (fgRef.current) {
            fgRef.current.emitParticle(
              fgRef.current.graphData().links.find((l: any) => l.source.id === data.source_id && l.target.id === data.target_id) || 
              { source: data.source_id, target: data.target_id }
            );
         }
         eventQueue.current.push(data);
         if (debounceTimer.current) clearTimeout(debounceTimer.current);
         debounceTimer.current = setTimeout(processEventQueue, 50);
         
         const id = setInterval(() => setAnimTick(t => t + 1), 33);
         setTimeout(() => clearInterval(id), 1500);
      } else if (["edge_created", "edge_closed", "cascade_step"].includes(data.event_type)) {
        eventQueue.current.push(data);
        if (debounceTimer.current) clearTimeout(debounceTimer.current);
        debounceTimer.current = setTimeout(processEventQueue, 150);
      }
    };
  }, [agentId, processEventQueue]);


  useEffect(() => {
    const apiKey = window.localStorage.getItem("kryptodb_api_key");
    if (!apiKey) return;
    loadSnapshot();
    connectWebSocket();
    return () => ws.current?.close();
  }, [connectWebSocket]);

  const getScopeColor = (scope: string) => {
    if (!scope) return "#888888";
    const colors = ["#ef4444", "#3b82f6", "#10b981", "#f59e0b", "#8b5cf6", "#ec4899"];
    const index = Math.abs(scope.split("").reduce((a, b) => a + b.charCodeAt(0), 0)) % colors.length;
    return colors[index];
  };

  const wrapperRef = useRef<HTMLDivElement>(null);
  const [dimensions, setDimensions] = useState({ width: 800, height: 400 });

  useEffect(() => {
    if (!wrapperRef.current) return;
    const observer = new ResizeObserver((entries) => {
      const { width, height } = entries[0].contentRect;
      if (width && height) {
        setDimensions(prev => {
          if (Math.abs(prev.width - width) > 2 || Math.abs(prev.height - height) > 2) {
            return { width: Math.floor(width), height: Math.floor(height) };
          }
          return prev;
        });
      }
    });
    observer.observe(wrapperRef.current);
    return () => observer.disconnect();
  }, []);

  return (
    <div className="graph-wrap">
      <div className="graph-toolbar">
          <button className="ui-button" title="Zoom in" onClick={() => fgRef.current?.zoom(fgRef.current.zoom() * 1.2, 400)}><Plus size={15} /></button>
          <button className="ui-button" title="Zoom out" onClick={() => fgRef.current?.zoom(fgRef.current.zoom() / 1.2, 400)}><span className="minus">−</span></button>
          <button className="ui-button" title="Fit graph" onClick={() => fgRef.current?.zoomToFit(400)}><Maximize2 size={15} /></button>
          <span className="toolbar-divider" />
          <button className="ui-button" title="Re-layout" onClick={() => fgRef.current?.d3ReheatSimulation()}><RefreshCw size={14} /> <span className="hide-sm">Relayout</span></button>
      </div>
      
      <div ref={wrapperRef} style={{ position: 'absolute', top: 38, bottom: 42, left: 0, right: 0, overflow: 'hidden', zIndex: 1 }}>
        <ForceGraph2D
          ref={fgRef}
          graphData={graphData}
          nodeRelSize={6}
          width={dimensions.width}
          height={dimensions.height}
          nodeCanvasObject={(node: any, ctx: CanvasRenderingContext2D, globalScale: number) => {
            if (node.x === undefined || node.y === undefined) return;
            ctx.beginPath();
            const timeSince = node.lastTraversedTime ? Date.now() - node.lastTraversedTime : Infinity;
            if (timeSince < 1500) {
              const progress = timeSince / 1500;
              const currentRadius = 6 + (6 * (1 - progress));
              ctx.arc(node.x, node.y, currentRadius, 0, 2 * Math.PI, false);
              ctx.fillStyle = `rgba(255, 255, 255, ${1 - progress})`; 
              ctx.fill();
              ctx.beginPath();
              ctx.arc(node.x, node.y, 6, 0, 2 * Math.PI, false);
              ctx.fillStyle = getScopeColor(node.scope);
              ctx.fill();
            } else {
              ctx.arc(node.x, node.y, 6, 0, 2 * Math.PI, false);
              ctx.fillStyle = getScopeColor(node.scope);
              ctx.fill();
            }
            // A subtle outline keeps tightly packed nodes distinguishable.
            ctx.beginPath();
            ctx.arc(node.x, node.y, 6, 0, 2 * Math.PI, false);
            ctx.strokeStyle = 'rgba(5, 8, 12, 0.9)';
            ctx.lineWidth = 1.25 / globalScale;
            ctx.stroke();
            if (selected && selected.id === node.id) {
              ctx.strokeStyle = '#ffffff';
              ctx.lineWidth = 2 / globalScale;
              ctx.stroke();
            }
          }}
          linkColor={(link: any) => link.historical ? '#334155' : getScopeColor(link.scope)}
          linkWidth={(link: any) => link.trust_weight ? link.trust_weight * 3 : 1}
          linkDirectionalParticleColor={(link: any) => '#ffffff'}
          linkDirectionalParticleWidth={4}
          linkDirectionalParticleSpeed={0.02}
          backgroundColor="transparent"
          onEngineStop={() => {
            if (graphDataRef.current) {
              graphDataRef.current.nodes.forEach(node => {
                node.fx = node.x;
                node.fy = node.y;
              });
            }
          }}
          onNodeClick={(node) => onSelect({ ...node, label: node.id, kind: node.type })}
          onBackgroundClick={() => onSelect(null)}
        />
      </div>

      <div className="graph-legend">
        {Array.from(new Set(graphData.nodes.map(n => n.scope || 'public'))).map((scope) => (
          <span key={scope} className={`scope-chip scope-${scope.toLowerCase().replace(/[^a-z]/g, '-')}`}>
            <span className="scope-mark" style={{ backgroundColor: getScopeColor(scope) }} />
            {scope}
          </span>
        ))}
      </div>
    </div>
  );
}
