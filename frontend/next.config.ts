import type { NextConfig } from "next";

function validatePublicApiUrl(phase: string): string {
  const value = process.env.NEXT_PUBLIC_API_URL?.trim() ?? "";
  if (phase === "phase-production-build" && !value) {
    throw new Error("NEXT_PUBLIC_API_URL is required for a production build");
  }
  if (!value) return "";

  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    throw new Error("NEXT_PUBLIC_API_URL must be an absolute http(s) URL");
  }
  if (!['http:', 'https:'].includes(parsed.protocol)) {
    throw new Error("NEXT_PUBLIC_API_URL must use http or https");
  }
  return value.replace(/\/$/, "");
}

export default function createNextConfig(phase: string): NextConfig {
  const apiUrl = validatePublicApiUrl(phase);
  process.env.NEXT_PUBLIC_API_URL = apiUrl;

  return {
  // The standalone bundle is required by the Docker frontend image. Vercel
  // provides its own Next.js runtime and expects the default build output.
  ...(process.env.VERCEL ? {} : { output: "standalone" }),
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        // The local FastAPI server listens on 8000. Production sets
        // NEXT_PUBLIC_API_URL, so browser requests go straight to Railway.
        destination: "http://127.0.0.1:8000/api/:path*",
      },
    ];
  },
  };
}
