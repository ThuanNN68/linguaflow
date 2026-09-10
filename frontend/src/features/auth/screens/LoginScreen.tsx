"use client";

import { useRouter } from "next/navigation";
import { SignInForm } from "../components/SignInForm";
import type { AuthScreen } from "../types";

export function LoginScreen() {
  const router = useRouter();

  const navigate = (screen: AuthScreen) => {
    if (screen === "signup") router.push("/register");
    if (screen === "forgot-password") router.push("/forgot-password");
  };

  return (
    <SignInForm
      onNavigate={navigate}
      onSuccess={(user) => {
        router.replace(user.role?.toLowerCase() === "admin" ? "/admin" : "/chat");
      }}
    />
  );
}
