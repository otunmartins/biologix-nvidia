/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // BIOLOGIX_API_URL is deliberately *not* declared under `env`: that inlines
  // a value at build time, so the same build could not be pointed at another
  // backend without recompiling. The variable is read at runtime in
  // src/lib/server.ts instead, which is server-only code — the console talks
  // to the Python API through its own route handlers, so the backend needs no
  // CORS entry for this port and its address never reaches the browser.
};

export default nextConfig;
