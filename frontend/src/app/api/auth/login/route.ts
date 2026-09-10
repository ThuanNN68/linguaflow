import { NextRequest, NextResponse } from "next/server";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

export async function POST(request: NextRequest) {
  const form = await request.formData();
  const email = String(form.get("email") ?? "").trim();
  const password = String(form.get("password") ?? "");
  const remember = form.get("remember") === "on";
  const login = await fetch(`${API_URL}/api/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password, remember }),
  });
  if (!login.ok) return NextResponse.redirect(new URL("/login?error=invalid-credentials", request.url), 303);
  const session = await login.json() as { access_token: string; refresh_token: string; user?: { role?: string } };
  const response = NextResponse.redirect(new URL(session.user?.role?.toLowerCase() === "admin" ? "/admin" : "/chat", request.url), 303);
  const options = { path: "/", sameSite: "lax" as const, httpOnly: false, secure: false, maxAge: remember ? 60 * 60 * 24 * 30 : undefined };
  response.cookies.set("access_token", session.access_token, options);
  response.cookies.set("refresh_token", session.refresh_token, options);
  return response;
}
