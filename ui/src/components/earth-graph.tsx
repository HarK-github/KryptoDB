"use client"

import { useMemo } from "react"

export default function EarthGraph() {
  // Generate deterministic nodes on a 2D isometric projection of a network sphere
  const { nodes, edges } = useMemo(() => {
    const rawNodes = []
    const count = 42
    const centerX = 250
    const centerY = 250
    const radius = 170

    for (let i = 0; i < count; i++) {
      const theta = (i / count) * 2 * Math.PI
      const phi = Math.acos(-1 + (2 * i) / count)
      const x = centerX + radius * Math.sin(phi) * Math.cos(theta)
      const y = centerY + radius * Math.cos(phi) * 0.85
      const depth = Math.sin(phi) * Math.sin(theta)
      rawNodes.push({ id: i, x, y, depth, isMajor: i % 5 === 0 })
    }

    const rawEdges: { x1: number; y1: number; x2: number; y2: number; opacity: number }[] = []
    for (let i = 0; i < rawNodes.length; i++) {
      const target1 = (i + 1) % rawNodes.length
      const target2 = (i + 4) % rawNodes.length
      rawEdges.push({
        x1: rawNodes[i].x,
        y1: rawNodes[i].y,
        x2: rawNodes[target1].x,
        y2: rawNodes[target1].y,
        opacity: Math.max(0.15, (rawNodes[i].depth + 1) / 3)
      })
      if (i % 2 === 0) {
        rawEdges.push({
          x1: rawNodes[i].x,
          y1: rawNodes[i].y,
          x2: rawNodes[target2].x,
          y2: rawNodes[target2].y,
          opacity: Math.max(0.1, (rawNodes[i].depth + 1) / 4)
        })
      }
    }

    return { nodes: rawNodes, edges: rawEdges }
  }, [])

  return (
    <div
      className="earth-scene"
      style={{
        width: "100%",
        height: "100%",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        position: "relative",
        overflow: "hidden"
      }}
      aria-label="Cryptographic knowledge graph network visualization"
    >
      <svg
        viewBox="0 0 500 500"
        style={{ width: "min(90vw, 520px)", height: "min(90vw, 520px)" }}
        className="spinning-network"
      >
        <defs>
          <radialGradient id="sphereGlow" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="#0d3534" stopOpacity="0.8" />
            <stop offset="70%" stopColor="#0a4b48" stopOpacity="0.4" />
            <stop offset="100%" stopColor="#041212" stopOpacity="0" />
          </radialGradient>
          <radialGradient id="centerCore" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="#2dd4bf" stopOpacity="0.25" />
            <stop offset="100%" stopColor="#0a4b48" stopOpacity="0" />
          </radialGradient>
          <filter id="glow">
            <feGaussianBlur stdDeviation="3.5" result="coloredBlur" />
            <feMerge>
              <feMergeNode in="coloredBlur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>

        {/* Ambient background glow */}
        <circle cx="250" cy="250" r="210" fill="url(#sphereGlow)" />
        <circle cx="250" cy="250" r="140" fill="url(#centerCore)" />

        {/* Outer orbital rings */}
        <circle cx="250" cy="250" r="190" fill="none" stroke="#2dd4bf" strokeWidth="0.8" strokeDasharray="4 6" opacity="0.25" />
        <ellipse cx="250" cy="250" rx="195" ry="90" fill="none" stroke="#2dd4bf" strokeWidth="0.8" opacity="0.3" transform="rotate(-25 250 250)" />
        <ellipse cx="250" cy="250" rx="195" ry="90" fill="none" stroke="#2dd4bf" strokeWidth="0.6" strokeDasharray="3 7" opacity="0.2" transform="rotate(35 250 250)" />

        {/* Graph Edges */}
        <g stroke="#2dd4bf">
          {edges.map((edge, idx) => (
            <line
              key={idx}
              x1={edge.x1}
              y1={edge.y1}
              x2={edge.x2}
              y2={edge.y2}
              strokeWidth="0.9"
              strokeOpacity={edge.opacity}
            />
          ))}
        </g>

        {/* Graph Nodes */}
        {nodes.map((node) => (
          <g key={node.id} filter={node.isMajor ? "url(#glow)" : undefined}>
            <circle
              cx={node.x}
              cy={node.y}
              r={node.isMajor ? 4.5 : 2.5}
              fill={node.isMajor ? "#dffffb" : "#2dd4bf"}
              opacity={Math.max(0.35, (node.depth + 1.2) / 2)}
            />
            {node.isMajor && (
              <circle
                cx={node.x}
                cy={node.y}
                r={9}
                fill="none"
                stroke="#2dd4bf"
                strokeWidth="0.8"
                opacity="0.4"
              />
            )}
          </g>
        ))}
      </svg>

      <div
        className="earth-label"
        style={{
          position: "absolute",
          bottom: 24,
          left: 24,
          color: "#2dd4bf",
          fontFamily: "monospace",
          fontSize: "12px",
          display: "flex",
          alignItems: "center",
          gap: "8px",
          letterSpacing: "0.08em"
        }}
      >
        <span
          className="earth-pulse"
          style={{
            display: "inline-block",
            width: "8px",
            height: "8px",
            borderRadius: "50%",
            backgroundColor: "#2dd4bf",
            boxShadow: "0 0 8px #2dd4bf"
          }}
        />
        LIVE MEMORY TOPOLOGY <b>42 encrypted nodes active</b>
      </div>
    </div>
  )
}