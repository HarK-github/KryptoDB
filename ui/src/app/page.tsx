"use client"

import { useEffect, useState } from "react"
import EarthGraph from "@/components/earth-graph"
import "./landing.css"
const githubUrl = "https://github.com/HarK-github/KryptoDB"

function Mark() {
  return <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="2" y="2" width="8" height="8" rx="2" /><rect x="14" y="2" width="8" height="8" rx="2" /><rect x="2" y="14" width="8" height="8" rx="2" /><rect x="14" y="14" width="8" height="8" rx="2" /><path d="M10 6h4M6 10v4M18 10v4M10 18h4" /></svg>
}
function Arrow() { return <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true"><path d="M2 12 12 2M4 2h8v8" fill="none" stroke="currentColor" strokeWidth="1.35" strokeLinecap="round" /></svg> }
function Sparkle() { return <svg width="18" height="20" viewBox="0 0 18 20" aria-hidden="true"><path d="M9 1c.5 5.4 2.1 7.5 8 9-5.9 1.5-7.5 3.6-8 9-.5-5.4-2.1-7.5-8-9 5.9-1.5 7.5-3.6 8-9Z" fill="currentColor" /></svg> }
function GraphIcon() { return <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.3" aria-hidden="true"><circle cx="4" cy="10" r="2"/><circle cx="15.5" cy="4" r="2"/><circle cx="16" cy="16" r="2"/><path d="m5.8 9 7.7-4M5.8 11l8.4 4"/></svg> }
function LockIcon() { return <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.3" aria-hidden="true"><rect x="3.5" y="8.5" width="13" height="9" rx="2"/><path d="M6.5 8.5V6a3.5 3.5 0 0 1 7 0v2.5"/></svg> }
function UsersIcon() { return <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.3" aria-hidden="true"><circle cx="10" cy="6" r="2.7"/><path d="M4.5 17c.3-3 2-4.5 5.5-4.5s5.2 1.5 5.5 4.5M3 10.5c-1.1.5-1.7 1.5-1.8 3M17 10.5c1.1.5 1.7 1.5 1.8 3"/></svg> }

export default function KryptoDBLanding() {
  const [menuOpen, setMenuOpen] = useState(false)
  useEffect(() => { const close = (e: KeyboardEvent) => e.key === "Escape" && setMenuOpen(false); document.addEventListener("keydown", close); return () => document.removeEventListener("keydown", close) }, [])
  return <div className="landing-page">
    <div className="bg-glow" />
    <div className={`menu-backdrop ${menuOpen ? "open" : ""}`} onClick={() => setMenuOpen(false)} />
    <div className="page">
      <header className="landing-header">
        <a className="logo" href="#top"><Mark /><span>Krypto<span>DB</span></span></a>
        
        <a className="btn btn-solid header-cta" href="/playground">Get Started</a>
        <button className={`burger ${menuOpen ? "open" : ""}`} aria-label="Toggle navigation" aria-expanded={menuOpen} onClick={() => setMenuOpen(!menuOpen)}><i/><i/><i/></button>
      </header>
      
      <main className="hero" id="top">
        <div className="hero-copy">
          <div className="badge"><Sparkle />Multi-Agent Memory Access Control</div>
          <h1><span className="headline-line"><em>Memory</em> for agents.</span><span className="headline-line"><em>Boundaries</em> for trust.</span></h1>
          <p className="lede">Cryptographically partitioned memory for AI agents, with graph traversal, access boundaries, and an auditable MCP gateway.</p>
          <div className="hero-actions">
            <a className="btn btn-solid hero-btn" href="/playground">Enter the Playground <Arrow /></a>
            <a className="btn btn-ghost hero-btn" href={githubUrl} target="_blank" rel="noreferrer">View on GitHub <Arrow /></a>
          </div>
        </div>
        <div className="hero-earth"><EarthGraph /></div>
      </main>
      
      <footer className="stats">
        <div className="stat"><GraphIcon />Real-time MCP Streaming</div>
        <div className="stat"><LockIcon />100% Encrypted Envelopes</div>
        <div className="stat"><UsersIcon />Human-in-the-Loop Async Approvals</div>
      </footer>
    </div>
  </div>
}
