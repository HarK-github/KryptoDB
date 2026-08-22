"use client";

/**
 * ARCHITECTURE MIGRATION NOTE
 * 
 * This component was migrated from `react-force-graph-2d` to `graphology` + `sigma.js`.
 * 
 * Key Principles:
 * 1. Graphology is the Single Source of Truth: Node/edge data is NEVER placed in React state.
 * 2. Sigma.js Renderer: Handles WebGL rendering. It is bound imperatively.
 * 3. Animation Loop (RAF): Replaces React re-renders. Completed traversal paths are
 *    replayed as staggered, interpolated dots on the overlay canvas.
 * 4. Reducers for Styling: `nodeReducer` and `edgeReducer` handle coloring and highlighting
 *    per-frame based on the traversal timestamps, without recreating objects.
 * 5. ReconnectingWebSocket: Handles disconnects gracefully without destroying graph state.
 */

import React, { useEffect, useState, useRef, useCallback } from "react";
import Graph from "graphology";
import forceAtlas2 from "graphology-layout-forceatlas2";
import Sigma from "sigma";
import ReconnectingWebSocket from "reconnecting-websocket";
import { Maximize2, Plus, RefreshCw, Filter, ChevronDown } from "lucide-react";

const API_BASE = process.env.NEXT_PUBLIC_KRYPTODB_URL || (typeof window !== 'undefined' ? `http://${window.location.hostname}:8000` : "http://127.0.0.1:8000");
const WS_BASE = API_BASE.replace(/^http/, "ws") + "/ws";

const getScopeColor = (scope: string) => {
  if (!scope) return "#888888";
  const colors = ["#ef4444", "#3b82f6", "#10b981", "#f59e0b", "#8b5cf6", "#ec4899"];
  const index = Math.abs(scope.split("").reduce((a, b) => a + b.charCodeAt(0), 0)) % colors.length;
  return colors[index];
};

export default function LiveGraph({ selected, onSelect }: any) {
  const [agentId] = useState("admin");
  const ws = useRef<ReconnectingWebSocket | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const overlayRef = useRef<HTMLCanvasElement>(null);
  const sigmaRef = useRef<Sigma | null>(null);
  const selectedRef = useRef(selected);
  const rafRef = useRef<number | null>(null);

  const graphRef = useRef(new Graph({ multi: true, allowSelfLoops: true }));
  const graph = graphRef.current;
  type TraversalStep = { edgeId: string; sourceId: string; targetId: string };
  const particlePath = useRef<TraversalStep[]>([]);
  const particleStartedAt = useRef<number | null>(null);
  const [scopes, setScopes] = useState<Set<string>>(new Set());
  
  useEffect(() => {
    selectedRef.current = selected;
    if (sigmaRef.current && !rafRef.current) sigmaRef.current.refresh();
  }, [selected]);

  // Handle Resize for Sigma and Overlay
  useEffect(() => {
    if (!containerRef.current || !overlayRef.current) return;
    const ro = new ResizeObserver(() => {
      if (overlayRef.current && containerRef.current) {
        overlayRef.current.width = containerRef.current.offsetWidth;
        overlayRef.current.height = containerRef.current.offsetHeight;
      }
      sigmaRef.current?.refresh();
    });
    ro.observe(containerRef.current);
    return () => ro.disconnect();
  }, []);

  const drawParticles = useCallback((now: number) => {
    if (!overlayRef.current || !sigmaRef.current) return;
    const ctx = overlayRef.current.getContext('2d');
    if (!ctx) return;
    
    ctx.clearRect(0, 0, overlayRef.current.width, overlayRef.current.height);
    
    const startedAt = particleStartedAt.current;
    if (startedAt === null) return;
    const hopDelay = 150;
    const duration = 360;
    const elapsed = now - startedAt;
    particlePath.current.forEach((step, index) => {
      const progress = Math.max(0, Math.min(1, (elapsed - index * hopDelay) / duration));
      if (elapsed < index * hopDelay || !graph.hasEdge(step.edgeId)) return;
      const source = graph.source(step.edgeId);
      const target = graph.target(step.edgeId);
      const sourceAttr = graph.getNodeAttributes(source);
      const targetAttr = graph.getNodeAttributes(target);
      const srcPos = sigmaRef.current?.graphToViewport({ x: sourceAttr.x, y: sourceAttr.y });
      const tgtPos = sigmaRef.current?.graphToViewport({ x: targetAttr.x, y: targetAttr.y });
      if (!srcPos || !tgtPos) return;
      const eased = progress * progress * (3 - 2 * progress);
      const x = srcPos.x + (tgtPos.x - srcPos.x) * eased;
      const y = srcPos.y + (tgtPos.y - srcPos.y) * eased;
      ctx.beginPath();
      ctx.arc(x, y, 4, 0, 2 * Math.PI);
      ctx.fillStyle = '#f8fafc';
      ctx.fill();
    });
  }, [graph]);

  const tick = useCallback(() => {
    const now = Date.now();
    const totalDuration = Math.max(0, (particlePath.current.length - 1) * 150 + 360);
    const hasActive = particleStartedAt.current !== null && now - particleStartedAt.current < totalDuration;
    if (hasActive) {
      drawParticles(now);
      rafRef.current = requestAnimationFrame(tick);
    } else {
      particlePath.current = [];
      particleStartedAt.current = null;
      drawParticles(now);
      rafRef.current = null;
    }
  }, [drawParticles]);

  const upsertNode = useCallback((id: string, attributes: any) => {
    const { type, ...safeAttributes } = attributes;
    const kind = type || attributes.kind || "Unknown";
    if (!graph.hasNode(id)) {
      graph.addNode(id, {
        ...safeAttributes,
        kind,
        x: Math.random() * 100,
        y: Math.random() * 100,
        size: 6,
        color: getScopeColor(safeAttributes.scope),
        label: id.length > 24 ? id.substring(0, 8) + '...' + id.substring(id.length - 6) : id
      });
    } else {
      graph.mergeNodeAttributes(id, { ...safeAttributes, kind });
    }
  }, [graph]);

  const upsertEdge = useCallback((id: string | undefined, source: string, target: string, attributes: any) => {
    if (!graph.hasNode(source)) upsertNode(source, { id: source, kind: "Unknown", scope: attributes.scope });
    if (!graph.hasNode(target)) upsertNode(target, { id: target, kind: "Unknown", scope: attributes.scope });
    
    if (id) {
      if (!graph.hasEdge(id)) {
        try { graph.addEdgeWithKey(id, source, target, attributes); } catch (e) {}
      } else {
        graph.mergeEdgeAttributes(id, attributes);
      }
    } else {
      try { graph.addEdge(source, target, attributes); } catch(e) {}
    }
  }, [graph, upsertNode]);

  const playTraversal = useCallback((path: TraversalStep[]) => {
    particlePath.current = path;
    particleStartedAt.current = Date.now();
    if (!rafRef.current) rafRef.current = requestAnimationFrame(tick);
  }, [tick]);

  const updateScopes = useCallback(() => {
    const s = new Set<string>();
    graph.forEachNode((_, attr) => {
      if (attr.scope) s.add(attr.scope);
    });
    setScopes(s);
  }, [graph]);

  const runLayout = useCallback(() => {
    if (graph.order > 0) {
      forceAtlas2.assign(graph, { iterations: 100, settings: forceAtlas2.inferSettings(graph) });
      if (!rafRef.current) sigmaRef.current?.refresh();
    }
  }, [graph]);

  // Sigma initialization
  useEffect(() => {
    if (!containerRef.current) return;

    const sigma = new Sigma(graph, containerRef.current, {
      renderEdgeLabels: false,
      defaultEdgeColor: "#888888",
      allowInvalidContainer: true,
      labelColor: { color: "#f8fafc" },
      labelBgColor: { color: "#0e1015" },
      defaultNodeHoverColor: "#0e1015",
    });
    sigmaRef.current = sigma;

    sigma.setSetting("nodeReducer", (node, data) => {
      const res = { ...data };
      const isSelected = selectedRef.current?.id === node;
      if (isSelected) {
        res.color = "#ffffff";
        res.size = (res.size || 6) * 1.2;
      }
      return res;
    });

    sigma.setSetting("edgeReducer", (edge, data) => {
      const res = { ...data };
      if (data.historical) {
        res.color = "#334155";
        res.size = 1;
      } else {
        res.color = getScopeColor(data.scope);
        res.size = data.trust_weight ? data.trust_weight * 3 : 1;
      }
      return res;
    });

    sigma.on("clickNode", (e) => {
      onSelect({ id: e.node, label: e.node, kind: graph.getNodeAttribute(e.node, "kind") });
    });
    sigma.on("clickStage", () => {
      onSelect(null);
    });

    return () => {
      sigma.kill();
      sigmaRef.current = null;
    };
  }, [graph, onSelect]);

  const loadSnapshot = async () => {
    try {
      const apiKey = typeof window !== 'undefined' ? (window.localStorage.getItem('kryptodb_api_key') || 'dev-admin-key') : 'dev-admin-key';
      const res = await fetch(`${API_BASE}/graph/snapshot?agent_id=${agentId}`, {
        headers: apiKey ? { Authorization: `Bearer ${apiKey}` } : {},
      });
      if (res.ok) {
        const data = await res.json();
        
        data.nodes.forEach((n: any) => upsertNode(n.id, n));
        data.edges.forEach((e: any) => upsertEdge(e.id, e.source, e.target, {
          predicate: "FACT",
          scope: e.scope,
          historical: false
        }));
        
        updateScopes();
        runLayout();
      }
    } catch (e) {
      console.error("Failed to fetch graph snapshot:", e);
    }
  };

  const connectWebSocket = useCallback(() => {
    if (ws.current) ws.current.close();
    const apiKey = typeof window !== 'undefined' ? (window.localStorage.getItem("kryptodb_api_key") || "dev-admin-key") : "dev-admin-key";
    const url = new URL(`${WS_BASE}/graph-events`);
    url.searchParams.set("agent_id", agentId);
    if (apiKey) url.searchParams.set("api_key", apiKey);
    
    ws.current = new ReconnectingWebSocket(url.toString());
    
    ws.current.onmessage = (event) => {
      const data = JSON.parse(event.data);
      
      if (data.event_type === "traversal_complete") {
         playTraversal(Array.isArray(data.traversal_path) ? data.traversal_path : []);
      } else if (data.event_type === "edge_created") {
         upsertEdge(data.edge_id, data.source_id, data.target_id, {
           predicate: "FACT",
           scope: data.permission_scope,
           trust_weight: data.trust_weight || 0.5,
           historical: false
         });
         updateScopes();
      } else if (data.event_type === "edge_closed" || data.event_type === "cascade_step") {
         if (data.edge_id && graph.hasEdge(data.edge_id)) {
           // Garbage collection: actually drop the dead edge to prevent unbounded memory growth
           const source = graph.source(data.edge_id);
           const target = graph.target(data.edge_id);
           graph.dropEdge(data.edge_id);
           
           // Clean up orphaned nodes
           if (graph.degree(source) === 0) graph.dropNode(source);
           if (graph.degree(target) === 0) graph.dropNode(target);
         }
      }
      
      if (!rafRef.current) sigmaRef.current?.refresh();
    };
  }, [agentId, graph, upsertEdge, playTraversal, updateScopes]);

  useEffect(() => {
    loadSnapshot();
    connectWebSocket();
    return () => ws.current?.close();
  }, [connectWebSocket]);

  // Toolbar actions
  const zoomIn = () => {
    const camera = sigmaRef.current?.getCamera();
    if (camera) camera.animate({ ratio: camera.ratio / 1.5 }, { duration: 300 });
  };
  const zoomOut = () => {
    const camera = sigmaRef.current?.getCamera();
    if (camera) camera.animate({ ratio: camera.ratio * 1.5 }, { duration: 300 });
  };
  const fitGraph = () => {
    const camera = sigmaRef.current?.getCamera();
    if (camera) camera.animate({ x: 0, y: 0, ratio: 1, angle: 0 }, { duration: 300 });
  };

  return (
    <div className="graph-wrap">
      <div className="graph-toolbar">
          <button className="ui-button" title="Zoom in" onClick={zoomIn}><Plus size={15} /></button>
          <button className="ui-button" title="Zoom out" onClick={zoomOut}><span className="minus">−</span></button>
          <button className="ui-button" title="Fit graph" onClick={fitGraph}><Maximize2 size={15} /></button>
          <span className="toolbar-divider" />
          <button className="ui-button" title="Re-layout" onClick={runLayout}><RefreshCw size={14} /> <span className="hide-sm">Relayout</span></button>
      </div>
      
      <div ref={containerRef} style={{ position: 'absolute', top: 38, bottom: 42, left: 0, right: 0, overflow: 'hidden', zIndex: 1 }} />
      <canvas ref={overlayRef} style={{ position: 'absolute', top: 38, bottom: 42, left: 0, right: 0, zIndex: 2, pointerEvents: 'none' }} />

      <div className="graph-legend">
        {Array.from(scopes).map((scope) => (
          <span key={scope} className={`scope-chip scope-${scope.toLowerCase().replace(/[^a-z]/g, '-')}`}>
            <span className="scope-mark" style={{ backgroundColor: getScopeColor(scope) }} />
            {scope}
          </span>
        ))}
      </div>
    </div>
  );
}
