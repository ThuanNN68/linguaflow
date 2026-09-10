// In local development this is intentionally empty: Next.js proxies /api to
// the backend so browser security policies cannot block a nonstandard port.
export const API_BASE = (process.env.NEXT_PUBLIC_API_URL ?? "").trim().replace(/\/$/, "");
