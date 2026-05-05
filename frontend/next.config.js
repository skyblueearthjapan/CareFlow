/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: 'standalone',
  experimental: {
    typedRoutes: true,
  },
  // Stable build id derived from GIT_SHA when available (used by SW cache key
  // automation in Phase 9; for now CACHE_VERSION in public/sw.js is bumped manually).
  generateBuildId: async () => process.env.GIT_SHA || 'dev',
  // App Router is enabled by default in Next 15; appDir flag is no longer required.
  //
  // /api/v1/* を backend にプロキシする (Tunnel 経由 + container 間直結の双方向対応)。
  // - container 間 (frontend → backend): BACKEND_API_BASE_URL=http://backend:8000
  // - Tunnel 経由は Cloudflared の path rule (^/api/v1/.*) で backend に直行するため、
  //   この rewrite が呼ばれるのは frontend container 内部からのケース (server side)。
  async rewrites() {
    const backend = process.env.BACKEND_API_BASE_URL || 'http://backend:8000';
    return [
      {
        source: '/api/v1/:path*',
        destination: `${backend}/api/v1/:path*`,
      },
    ];
  },
};

module.exports = nextConfig;
