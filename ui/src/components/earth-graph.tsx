"use client"

import { Canvas, useFrame } from "@react-three/fiber"
import { OrbitControls, Stars } from "@react-three/drei"
import { useMemo, useRef } from "react"
import * as THREE from "three"

// 1. Generate random points on a sphere
function getRandomSpherePoint(radius: number) {
  const u = Math.random()
  const v = Math.random()
  const theta = 2 * Math.PI * u
  const phi = Math.acos(2 * v - 1)

  return new THREE.Vector3(
    radius * Math.sin(phi) * Math.cos(theta),
    radius * Math.sin(phi) * Math.sin(theta),
    radius * Math.cos(phi)
  )
}

function KnowledgeGraph() {
  const group = useRef<THREE.Group>(null)

  // 2. Generate 52 random nodes on the surface
  const NODE_COUNT = 52
  const RADIUS = 1.03
  const points = useMemo(() => {
    return Array.from({ length: NODE_COUNT }, () => getRandomSpherePoint(RADIUS))
  }, [])

  // 3. Connect nodes to their nearest neighbors with jumping arcs
  const edges = useMemo(() => {
    const pairs: [number, number][] = []

    for (let i = 0; i < points.length; i++) {
      // Find the distances to all other points
      const distances = points.map((p, idx) => ({
        idx,
        dist: p.distanceTo(points[i])
      }))

      // Sort by distance to find the closest neighbors
      distances.sort((a, b) => a.dist - b.dist)

      // Connect to the 2 nearest neighbors (skipping index 0 which is itself)
      pairs.push([i, distances[1].idx])
      pairs.push([i, distances[2].idx])
    }

    return pairs.map(([a, b]) => {
      const p1 = points[a]
      const p2 = points[b]

      // Calculate the midpoint
      const mid = new THREE.Vector3().addVectors(p1, p2).multiplyScalar(0.5)
      const distance = p1.distanceTo(p2)

      // Push the midpoint outward based on the distance between the nodes to create a "jump" arc
      const arcHeight = RADIUS + (distance * 0.4)
      mid.normalize().multiplyScalar(arcHeight)

      // Create a smooth quadratic bezier curve
      const curve = new THREE.QuadraticBezierCurve3(p1, mid, p2)
      return curve.getPoints(24)
    })
  }, [points])

  useFrame((_, delta) => {
    if (group.current) group.current.rotation.y += delta * 0.075
  })

  return (
    <group ref={group}>
      {/* Core Sphere */}
      <mesh>
        <sphereGeometry args={[1, 64, 64]} />
        <meshStandardMaterial color="#0d3534" emissive="#0a4b48" emissiveIntensity={0.55} roughness={0.78} metalness={0.18} />
      </mesh>

      {/* Atmosphere/Glow */}
      <mesh scale={1.012}>
        <sphereGeometry args={[1, 64, 64]} />
        <meshBasicMaterial color="#2dd4bf" transparent opacity={0.08} wireframe />
      </mesh>

      {/* Jumping Edges */}
      {edges.map((edge, index) => (
        <line key={index}>
          <bufferGeometry>
            <bufferAttribute
              attach="attributes-position"
              args={[new Float32Array(edge.flatMap((point) => point.toArray())), 3]}
              count={edge.length}
            />
          </bufferGeometry>
          <lineBasicMaterial color="#2dd4bf" transparent opacity={Math.random() * 0.5 + 0.2} />
        </line>
      ))}

      {/* Nodes */}
      {points.map((point, index) => (
        <group key={index} position={point}>
          <mesh>
            <sphereGeometry args={[0.02, 16, 16]} />
            <meshBasicMaterial color="#dffffb" />
          </mesh>
          <mesh scale={1.8}>
            <sphereGeometry args={[0.02, 16, 16]} />
            <meshBasicMaterial color="#2dd4bf" transparent opacity={0.3} />
          </mesh>
        </group>
      ))}
    </group>
  )
}

export default function EarthGraph() {
  return (
    <div className="earth-scene" style={{ width: '100vw', height: '100vh', background: '#000' }} aria-label="Interactive 3D Earth with a cryptographic knowledge graph">
      <Canvas camera={{ position: [0, 0.1, 2.8], fov: 45 }} dpr={[1, 1.6]} gl={{ antialias: true, alpha: true }}>
        <ambientLight intensity={1.2} />
        <directionalLight position={[3, 2, 4]} intensity={2.4} color="#b8fff8" />
        <pointLight position={[-3, -1, 2]} intensity={1.5} color="#2dd4bf" />
        <Stars radius={8} depth={3} count={1500} factor={1.5} saturation={0} fade speed={0.15} />
        <KnowledgeGraph />
        <OrbitControls enablePan={false} enableZoom={true} autoRotate autoRotateSpeed={0.45} minPolarAngle={Math.PI / 2.7} maxPolarAngle={Math.PI / 1.75} />
      </Canvas>
      <div className="earth-label" style={{ position: 'absolute', bottom: 40, left: 40, color: '#2dd4bf', fontFamily: 'monospace' }}>
        <span className="earth-pulse" />LIVE MEMORY TOPOLOGY <b>52 nodes online</b>
      </div>
    </div>
  )
}