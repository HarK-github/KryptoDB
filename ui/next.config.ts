import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  async rewrites() {
    // BACKEND_URL is resolved by the Next.js container. The public URL may
    // intentionally point at localhost for browser-side WebSocket traffic.
    const backendUrl = process.env.BACKEND_URL || process.env.NEXT_PUBLIC_KRYPTODB_URL || "http://localhost:8000";
    return [
      { source: "/api/v1/:path*", destination: `${backendUrl}/api/v1/:path*` },
      { source: "/v1/:path*", destination: `${backendUrl}/v1/:path*` },
      { source: "/graph/:path*", destination: `${backendUrl}/graph/:path*` },
      { source: "/agent/:path*", destination: `${backendUrl}/agent/:path*` },
      { source: "/chat/:path*", destination: `${backendUrl}/chat/:path*` },
      { source: "/admin/:path*", destination: `${backendUrl}/admin/:path*` },
      { source: "/config/:path*", destination: `${backendUrl}/config/:path*` },
      { source: "/memory/:path*", destination: `${backendUrl}/memory/:path*` },
      { source: "/agents", destination: `${backendUrl}/agents` },
      { source: "/audit", destination: `${backendUrl}/audit` },
      { source: "/ontology", destination: `${backendUrl}/ontology` },
      { source: "/models", destination: `${backendUrl}/models` },
      { source: "/health", destination: `${backendUrl}/health` },
      { source: "/healthz", destination: `${backendUrl}/healthz` },
      { source: "/ready", destination: `${backendUrl}/ready` },
    ];
  },
};

export default nextConfig;
