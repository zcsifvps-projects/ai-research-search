import type { NextConfig } from "next";

// NEXT_PUBLIC_* values are inlined at BUILD time. Fail the production build loudly instead of
// silently shipping a frontend that talks to localhost.
if (process.env.NODE_ENV === "production") {
  for (const name of ["NEXT_PUBLIC_API_URL", "NEXT_PUBLIC_APP_URL"]) {
    if (!process.env[name]) {
      throw new Error(
        `${name} must be set when building for production (Docker: pass it as a build arg before "npm run build").`,
      );
    }
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
