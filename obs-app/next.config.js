/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',

  // Allow the orchestrator URL to be configured at runtime via env var
  async rewrites() {
    return [
      {
        source: '/api/orchestrator/:path*',
        destination: `${process.env.NEXT_PUBLIC_ORCHESTRATOR_URL || 'http://orchestrator:8000'}/:path*`,
      },
    ];
  },

  serverExternalPackages: [],
};

module.exports = nextConfig;
