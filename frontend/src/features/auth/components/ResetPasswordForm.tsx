"use client";

import React, { useState } from "react";
import { AlertCircle, ArrowLeft, CheckCircle2, Eye, EyeOff, KeyRound, Loader2 } from "lucide-react";
import { resetPassword } from "../api/auth-api";

interface ResetPasswordFormProps {
  token: string;
  onReturnToSignIn: () => void;
}

export const ResetPasswordForm: React.FC<ResetPasswordFormProps> = ({ token, onReturnToSignIn }) => {
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [isComplete, setIsComplete] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setErrorMessage(null);

    if (token.length < 32) {
      setErrorMessage("This reset link is invalid. Please request a new password reset email.");
      return;
    }
    if (newPassword.length < 8) {
      setErrorMessage("Your new password must contain at least 8 characters.");
      return;
    }
    if (newPassword !== confirmPassword) {
      setErrorMessage("The passwords do not match.");
      return;
    }

    setIsLoading(true);
    try {
      await resetPassword(token, newPassword);
      setIsComplete(true);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "Unable to reset your password. Please request a new link.");
    } finally {
      setIsLoading(false);
    }
  };

  if (isComplete) {
    return (
      <div className="bg-white rounded-3xl border border-slate-200/90 p-7 sm:p-9 md:p-10 shadow-lg shadow-slate-200/40 text-center py-8">
        <div className="w-14 h-14 rounded-2xl bg-emerald-100 border border-emerald-200 text-emerald-600 mx-auto flex items-center justify-center mb-4 shadow-xs">
          <CheckCircle2 className="w-7 h-7" />
        </div>
        <h2 className="text-2xl sm:text-3xl font-extrabold text-slate-900 tracking-tight mb-2">Password updated</h2>
        <p className="text-sm sm:text-base text-slate-600 leading-relaxed mb-6">
          Your password has been changed. All previous sign-in sessions were signed out for your security.
        </p>
        <button
          type="button"
          onClick={onReturnToSignIn}
          className="w-full h-12 px-4 rounded-xl bg-[#08BCFD] hover:bg-[#0284C7] text-white text-sm sm:text-base font-semibold transition-all shadow-md shadow-[#08BCFD]/25 active:scale-[0.99] flex items-center justify-center gap-2 cursor-pointer"
        >
          <ArrowLeft className="w-4.5 h-4.5" />
          <span>Return to Sign In</span>
        </button>
      </div>
    );
  }

  return (
    <div className="bg-white rounded-3xl border border-slate-200/90 p-7 sm:p-9 md:p-10 shadow-lg shadow-slate-200/40">
      <div className="mb-6">
        <div className="w-12 h-12 rounded-2xl bg-sky-50 border border-sky-100 text-[#0284C7] flex items-center justify-center mb-4">
          <KeyRound className="w-6 h-6" />
        </div>
        <h2 className="text-2xl sm:text-3xl font-extrabold text-slate-900 tracking-tight">Choose a new password</h2>
        <p className="text-sm sm:text-base text-slate-500 mt-1.5">Use at least 8 characters. This reset link can only be used once.</p>
      </div>

      {errorMessage && (
        <div className="mb-5 flex items-center gap-2.5 p-3.5 rounded-xl bg-rose-50 border border-rose-200/80 text-rose-700 text-xs sm:text-sm font-medium">
          <AlertCircle className="w-4.5 h-4.5 shrink-0 text-rose-600" />
          <span>{errorMessage}</span>
        </div>
      )}

      <form onSubmit={submit} className="space-y-4.5">
        <div>
          <label htmlFor="new-password" className="block text-xs sm:text-sm font-semibold text-slate-700 mb-1.5">New password</label>
          <div className="relative">
            <input
              id="new-password"
              type={showPassword ? "text" : "password"}
              autoComplete="new-password"
              value={newPassword}
              onChange={(event) => setNewPassword(event.target.value)}
              className="w-full h-12 px-4 pr-11 text-sm sm:text-base bg-slate-50/50 hover:bg-slate-50 focus:bg-white border border-slate-200 focus:border-[#08BCFD] rounded-xl outline-none focus:ring-3 focus:ring-[#08BCFD]/20 transition-all text-slate-900"
            />
            <button
              type="button"
              onClick={() => setShowPassword((current) => !current)}
              className="absolute inset-y-0 right-0 px-3.5 text-slate-400 hover:text-[#08BCFD] cursor-pointer"
              aria-label={showPassword ? "Hide password" : "Show password"}
            >
              {showPassword ? <EyeOff className="w-4.5 h-4.5" /> : <Eye className="w-4.5 h-4.5" />}
            </button>
          </div>
        </div>

        <div>
          <label htmlFor="confirm-new-password" className="block text-xs sm:text-sm font-semibold text-slate-700 mb-1.5">Confirm new password</label>
          <input
            id="confirm-new-password"
            type={showPassword ? "text" : "password"}
            autoComplete="new-password"
            value={confirmPassword}
            onChange={(event) => setConfirmPassword(event.target.value)}
            className="w-full h-12 px-4 text-sm sm:text-base bg-slate-50/50 hover:bg-slate-50 focus:bg-white border border-slate-200 focus:border-[#08BCFD] rounded-xl outline-none focus:ring-3 focus:ring-[#08BCFD]/20 transition-all text-slate-900"
          />
        </div>

        <button
          type="submit"
          disabled={isLoading}
          className="w-full h-12 mt-2 px-4 rounded-xl bg-[#08BCFD] hover:bg-[#0284C7] text-white text-sm sm:text-base font-semibold transition-all shadow-md shadow-[#08BCFD]/25 active:scale-[0.99] flex items-center justify-center gap-2 disabled:opacity-70 cursor-pointer"
        >
          {isLoading ? <><Loader2 className="w-4.5 h-4.5 animate-spin" /><span>Updating password...</span></> : <span>Update password</span>}
        </button>
      </form>

      <div className="mt-6 text-center">
        <button
          type="button"
          onClick={onReturnToSignIn}
          className="inline-flex items-center gap-2 text-xs sm:text-sm font-semibold text-slate-600 hover:text-[#08BCFD] transition-colors cursor-pointer"
        >
          <ArrowLeft className="w-4 h-4" />
          <span>Back to Sign In</span>
        </button>
      </div>
    </div>
  );
};
