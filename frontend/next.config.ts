import type { NextConfig } from "next";

// NEXT_PUBLIC_* values are inlined at BUILD time and there is no fallback anywhere in the code.
// Fail immediately (dev, build and start) instead of silently shipping a frontend that talks to the wrong host.
for (const name of ["NEXT_PUBLIC_API_URL", "NEXT_PUBLIC_APP_URL"]) {
  const value = process.env[name];
  if (!value) {
    throw new Error(
      `${name} is not set. Copy frontend/.env.example to frontend/.env.local (dev) or set it before "npm run build" (production / Docker build arg).`,
    );
  }
  try {
    new URL(value);
  } catch {
    throw new Error(`${name} must be a full URL such as https://example.com (got "${value}").`);
  }
}

const nextConfig: NextConfig = {
  poweredByHeader: false,
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
        ],
      },
    ];
  },
};

export default nextConfig;
