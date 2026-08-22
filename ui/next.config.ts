import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  async rewrites() {
    // BACKEND_URL is resolved by the Next.js container. The public URL may
    // intentionally point at localhost for browser-side WebSocket traffic.
    const backendUrl = process.env.BACKEND_URL || process.env.NEXT_PUBLIC_KRYPTODB_URL || "http://localhost:8000";
    return [
      {
        source: "/agent/:path*",
        destination: `${backendUrl}/agent/:path*`,
      },
      {
        source: "/chat/:path*",
        destination: `${backendUrl}/chat/:path*`,
      },
      {
        source: "/admin/:path*",
        destination: `${backendUrl}/admin/:path*`,
      },
      {
        source: "/health",
        destination: `${backendUrl}/health`,
      },
    ];
  },
};

export default nextConfig;
