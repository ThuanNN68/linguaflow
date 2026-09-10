import type { Metadata } from "next";
import { ForgotPasswordScreen } from "@/features/auth/screens/ForgotPasswordScreen";

export const metadata: Metadata = {
  title: "Reset password — LinguaFlow",
  description: "Request password reset instructions for your LinguaFlow account.",
};

export default function ForgotPasswordPage() {
  return <ForgotPasswordScreen />;
}
