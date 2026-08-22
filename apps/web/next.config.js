/** @type {import('next').NextConfig} */
const nextConfig = {
  // Disabled so dev-mode StrictMode doesn't double-mount effects and open
  // two WebSocket connections to the control plane.
  reactStrictMode: false,
};

module.exports = nextConfig;
