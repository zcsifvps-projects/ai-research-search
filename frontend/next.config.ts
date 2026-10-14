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

// Content-Security-Policy. Production builds only (dev tooling needs eval/websockets).
//   CSP_MODE=enforce (default) | report-only (log violations in the browser console, block nothing) | off
// 'unsafe-inline' for scripts is required by Next.js's inline hydration scripts unless a per-request nonce
// is added; everything else (no foreign scripts, no plugins, no framing, API calls only to our own API) is locked down.
function contentSecurityPolicy(): { key: string; value: string } | null {
  const mode = (process.env.CSP_MODE || "enforce").toLowerCase();
  if (process.env.NODE_ENV !== "production" || mode === "off") return null;
  const apiOrigin = new URL(process.env.NEXT_PUBLIC_API_URL as string).origin;
  const appIsHttps = (process.env.NEXT_PUBLIC_APP_URL as string).startsWith("https://");
  const directives = [
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    `connect-src 'self' ${apiOrigin}`,
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
    ...(appIsHttps ? ["upgrade-insecure-requests"] : []),
  ];
  return {
    key: mode === "report-only" ? "Content-Security-Policy-Report-Only" : "Content-Security-Policy",
    value: directives.join("; "),
  };
}

const csp = contentSecurityPolicy();

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
          ...(csp ? [csp] : []),
        ],
      },
    ];
  },
};

export default nextConfig;
