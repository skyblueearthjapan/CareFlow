/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: 'standalone',
  experimental: {
    typedRoutes: true,
  },
  // App Router is enabled by default in Next 15; appDir flag is no longer required.
};

module.exports = nextConfig;
