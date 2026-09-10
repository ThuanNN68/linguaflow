"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { ResetPasswordForm } from "../components/ResetPasswordForm";

export function ResetPasswordScreen() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const token = searchParams.get("token")?.trim() ?? "";

  return <ResetPasswordForm token={token} onReturnToSignIn={() => router.push("/login")} />;
}
