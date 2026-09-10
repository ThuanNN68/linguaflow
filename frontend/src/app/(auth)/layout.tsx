import React from "react";
import { AuthLayout as AuthPageLayout } from "@/features/auth/components/AuthLayout";
import "@/features/auth/styles/auth.css";

export default function AuthRouteLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="min-h-screen bg-slate-50 text-slate-900 font-sans antialiased">
      <AuthPageLayout>{children}</AuthPageLayout>
    </div>
  );
}
