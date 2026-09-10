"use client";

import React, { useState } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import { Mail, ArrowLeft, CheckCircle2, AlertCircle, Loader2, Send } from 'lucide-react';
import { AuthScreen } from '../types';
import { requestPasswordReset } from '../api/auth-api';

interface ForgotPasswordFormProps {
  onNavigate: (screen: AuthScreen) => void;
}

export const ForgotPasswordForm: React.FC<ForgotPasswordFormProps> = ({ onNavigate }) => {
  const [email, setEmail] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [isSubmitted, setIsSubmitted] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErrorMessage(null);

    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    if (!email.trim() || !emailRegex.test(email.trim())) {
      setErrorMessage('Please enter a valid email address.');
      return;
    }

    setIsLoading(true);

    try {
      await requestPasswordReset(email);
      setIsSubmitted(true);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Unable to request a password reset.');
    } finally {
      setIsLoading(false);
    }
  };

  const handleResend = async () => {
    setIsLoading(true);
    try {
      await requestPasswordReset(email);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Unable to resend reset instructions.');
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -8 }}
      transition={{ duration: 0.25 }}
      className="bg-white rounded-3xl border border-slate-200/90 p-7 sm:p-9 md:p-10 shadow-lg shadow-slate-200/40"
    >
      <AnimatePresence mode="wait">
        {!isSubmitted ? (
          <motion.div
            key="request-form"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
          >
            {/* Title & Subtitle */}
            <div className="mb-6">
              <h2 className="text-2xl sm:text-3xl font-extrabold text-slate-900 tracking-tight">Reset your password</h2>
              <p className="text-sm sm:text-base text-slate-500 mt-1.5">
                Enter your email address and we'll send you instructions to reset your password.
              </p>
            </div>

            {/* Error Message */}
            {errorMessage && (
              <div className="mb-5 flex items-center gap-2.5 p-3.5 rounded-xl bg-rose-50 border border-rose-200/80 text-rose-700 text-xs sm:text-sm font-medium">
                <AlertCircle className="w-4.5 h-4.5 shrink-0 text-rose-600" />
                <span>{errorMessage}</span>
              </div>
            )}

            <form onSubmit={handleSubmit} className="space-y-4.5">
              {/* Email Input */}
              <div>
                <label htmlFor="reset-email" className="block text-xs sm:text-sm font-semibold text-slate-700 mb-1.5">
                  Email address
                </label>
                <div className="relative">
                  <div className="absolute inset-y-0 left-0 pl-3.5 flex items-center pointer-events-none text-slate-400">
                    <Mail className="w-4.5 h-4.5" />
                  </div>
                  <input
                    id="reset-email"
                    type="email"
                    autoComplete="email"
                    value={email}
                    onChange={(e) => {
                      setEmail(e.target.value);
                      if (errorMessage) setErrorMessage(null);
                    }}
                    placeholder="you@example.com"
                    className="w-full h-12 pl-11 pr-3.5 text-sm sm:text-base bg-slate-50/50 hover:bg-slate-50 focus:bg-white border border-slate-200 focus:border-[#08BCFD] rounded-xl outline-none focus:ring-3 focus:ring-[#08BCFD]/20 transition-all text-slate-900 placeholder:text-slate-400"
                  />
                </div>
              </div>

              {/* Submit Button */}
              <button
                id="send-reset-link-button"
                type="submit"
                disabled={isLoading}
                className="w-full h-12 mt-2 px-4 rounded-xl bg-[#08BCFD] hover:bg-[#0284C7] text-white text-sm sm:text-base font-semibold transition-all shadow-md shadow-[#08BCFD]/25 active:scale-[0.99] flex items-center justify-center gap-2 disabled:opacity-70 cursor-pointer"
              >
                {isLoading ? (
                  <>
                    <Loader2 className="w-4.5 h-4.5 animate-spin" />
                    <span>Sending reset link...</span>
                  </>
                ) : (
                  <>
                    <span>Send Reset Link</span>
                    <Send className="w-4.5 h-4.5" />
                  </>
                )}
              </button>
            </form>

            {/* Back to Sign In Link */}
            <div className="mt-6 text-center">
              <button
                id="back-to-signin-button"
                type="button"
                onClick={() => onNavigate('signin')}
                className="inline-flex items-center gap-2 text-xs sm:text-sm font-semibold text-slate-600 hover:text-[#08BCFD] transition-colors cursor-pointer"
              >
                <ArrowLeft className="w-4 h-4" />
                <span>Back to Sign In</span>
              </button>
            </div>
          </motion.div>
        ) : (
          /* Check your email state */
          <motion.div
            key="success-state"
            initial={{ opacity: 0, scale: 0.95 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.95 }}
            className="text-center py-2"
          >
            <div className="w-14 h-14 rounded-2xl bg-emerald-100 border border-emerald-200 text-emerald-600 mx-auto flex items-center justify-center mb-4 shadow-xs">
              <CheckCircle2 className="w-7 h-7" />
            </div>

            <h2 className="text-2xl sm:text-3xl font-extrabold text-slate-900 tracking-tight mb-2">
              Check your email
            </h2>
            <p className="text-sm sm:text-base text-slate-600 leading-relaxed max-w-sm mx-auto mb-6">
              If an account exists for{' '}
              <span className="font-semibold text-slate-900">{email}</span>, a password reset link will arrive shortly. Open the link in that email to set a new password.
            </p>

            <div className="space-y-3">
              <button
                id="return-signin-button"
                type="button"
                onClick={() => onNavigate('signin')}
                className="w-full h-12 px-4 rounded-xl bg-[#08BCFD] hover:bg-[#0284C7] text-white text-sm sm:text-base font-semibold transition-all shadow-md shadow-[#08BCFD]/25 active:scale-[0.99] flex items-center justify-center gap-2 cursor-pointer"
              >
                <ArrowLeft className="w-4.5 h-4.5" />
                <span>Return to Sign In</span>
              </button>

              <div className="pt-2 text-xs sm:text-sm text-slate-500">
                Didn't receive the email?{' '}
                <button
                  type="button"
                  onClick={handleResend}
                  disabled={isLoading}
                  className="font-semibold text-[#0284C7] hover:text-[#08BCFD] disabled:text-slate-400 transition-colors cursor-pointer"
                >
                  Click to resend
                </button>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
};
