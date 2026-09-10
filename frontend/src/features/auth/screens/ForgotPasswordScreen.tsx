"use client";

import { useRouter } from "next/navigation";
import { ForgotPasswordForm } from "../components/ForgotPasswordForm";

export function ForgotPasswordScreen() {
  const router = useRouter();
  return <ForgotPasswordForm onNavigate={() => router.push("/login")} />;
}
