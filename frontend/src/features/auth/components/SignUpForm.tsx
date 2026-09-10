"use client";

import React, { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { motion, AnimatePresence } from 'motion/react';
import { User, Mail, Lock, Eye, EyeOff, AlertCircle, Check, CheckCircle2, ArrowRight, Loader2, KeyRound, ArrowLeft } from 'lucide-react';
import { GoogleSignInButton } from './GoogleSignInButton';
import { AuthScreen, UserProfile } from '../types';
import { signUp, verifyRegisterOtp, resendRegisterOtp, signInWithGoogle } from '../api/auth-api';
import { saveSession } from '@/shared/lib/session';

const PENDING_REGISTRATION_STORAGE_KEY = 'linguaflow.pending-registration';

interface StoredPendingRegistration {
  pendingId: string;
  email: string;
}

function loadPendingRegistration(): StoredPendingRegistration | null {
  if (typeof window === 'undefined') return null;
  try {
    const parsed: unknown = JSON.parse(window.sessionStorage.getItem(PENDING_REGISTRATION_STORAGE_KEY) ?? 'null');
    if (
      typeof parsed === 'object' && parsed !== null
      && typeof (parsed as StoredPendingRegistration).pendingId === 'string'
      && typeof (parsed as StoredPendingRegistration).email === 'string'
    ) {
      return parsed as StoredPendingRegistration;
    }
  } catch {
    // A malformed stale browser value should never prevent registration.
  }
  return null;
}

function storePendingRegistration(registration: StoredPendingRegistration): void {
  window.sessionStorage.setItem(PENDING_REGISTRATION_STORAGE_KEY, JSON.stringify(registration));
}

function clearPendingRegistration(): void {
  window.sessionStorage.removeItem(PENDING_REGISTRATION_STORAGE_KEY);
}

interface SignUpFormProps {
  onNavigate: (screen: AuthScreen) => void;
  onSuccess: (user: Partial<UserProfile>) => void;
}

export const SignUpForm: React.FC<SignUpFormProps> = ({ onNavigate, onSuccess }) => {
  const [step, setStep] = useState<'form' | 'otp'>('form');
  const [pendingId, setPendingId] = useState('');
  const [otp, setOtp] = useState('');
  const [cooldown, setCooldown] = useState(60);
  const [resending, setResending] = useState(false);

  const [fullName, setFullName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirmPassword, setShowConfirmPassword] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [hasAcceptedTerms, setHasAcceptedTerms] = useState(false);

  // Field touched states for responsive inline validation
  const [touched, setTouched] = useState({
    fullName: false,
    email: false,
    password: false,
    confirmPassword: false,
  });

  const [formError, setFormError] = useState<string | null>(null);

  // A page refresh or development HMR must not discard the opaque identifier
  // that the resend/verify APIs use to locate the pending OTP record.
  useEffect(() => {
    const pending = loadPendingRegistration();
    if (!pending) return;
    // Defer the restoration until hydration has finished. This avoids a
    // server/client initial-render mismatch while still retaining OTP state
    // after a browser refresh or HMR update.
    queueMicrotask(() => {
      setPendingId(pending.pendingId);
      setEmail(pending.email);
      setStep('otp');
    });
  }, []);

  // Countdown timer for OTP resend
  useEffect(() => {
    if (step !== 'otp' || cooldown <= 0) return;
    const timer = setInterval(() => {
      setCooldown((prev) => (prev > 0 ? prev - 1 : 0));
    }, 1000);
    return () => clearInterval(timer);
  }, [step, cooldown]);

  // Validation logic
  const isEmailValid = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim());
  const isPasswordLengthOk = password.length >= 8;
  const hasLetterAndNumber = /[a-zA-Z]/.test(password) && /[0-9]/.test(password);
  const isPasswordStrong = isPasswordLengthOk && hasLetterAndNumber;
  const isPasswordMatch = confirmPassword.length > 0 && password === confirmPassword;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setTouched({
      fullName: true,
      email: true,
      password: true,
      confirmPassword: true,
    });

    if (!fullName.trim()) {
      setFormError('Please enter your full name.');
      return;
    }

    if (!isEmailValid) {
      setFormError('Please enter a valid email address.');
      return;
    }

    if (!isPasswordStrong) {
      setFormError('Password must be at least 8 characters and include both letters and numbers.');
      return;
    }

    if (password !== confirmPassword) {
      setFormError('Passwords do not match.');
      return;
    }

    if (!hasAcceptedTerms) {
      setFormError('Bạn cần đồng ý với Điều khoản dịch vụ và Chính sách riêng tư để tạo tài khoản.');
      return;
    }

    setFormError(null);
    setIsLoading(true);

    try {
      const pending = await signUp({
        fullName,
        email,
        password,
        preferredLanguage: 'vi',
      });
      setPendingId(pending.pending_id);
      storePendingRegistration({ pendingId: pending.pending_id, email: pending.email });
      setCooldown(pending.cooldown_seconds || 60);
      setStep('otp');
    } catch (error) {
      setFormError(error instanceof Error ? error.message : 'Unable to create your account. Please try again.');
    } finally {
      setIsLoading(false);
    }
  };

  const handleVerifyOtp = async (e: React.FormEvent) => {
    e.preventDefault();
    if (otp.length !== 6) {
      setFormError('Please enter the complete 6-digit verification code.');
      return;
    }

    setFormError(null);
    setIsLoading(true);

    try {
      const session = await verifyRegisterOtp(pendingId, otp);
      clearPendingRegistration();
      saveSession(session, true);
      onSuccess({
        name: session.user.display_name,
        email: session.user.email,
        preferredLanguage: session.user.preferred_language,
      });
      onNavigate('language-onboarding');
    } catch (error) {
      setFormError(error instanceof Error ? error.message : 'Invalid or expired verification code.');
    } finally {
      setIsLoading(false);
    }
  };

  const handleResendOtp = async () => {
    if (cooldown > 0 || resending) return;
    const activePendingId = pendingId || loadPendingRegistration()?.pendingId || '';
    if (!activePendingId) {
      setFormError('Your registration session is no longer available. Please return to the details form and register again.');
      return;
    }
    setResending(true);
    setFormError(null);
    try {
      const res = await resendRegisterOtp(activePendingId);
      setPendingId(res.pending_id);
      storePendingRegistration({ pendingId: res.pending_id, email });
      setCooldown(res.cooldown_seconds || 60);
      setOtp('');
    } catch (error) {
      setFormError(error instanceof Error ? error.message : 'Unable to resend verification code.');
    } finally {
      setResending(false);
    }
  };

  const handleGoogleSignUp = useCallback(async (credential: string) => {
    setFormError(null);
    setIsLoading(true);
    try {
      const session = await signInWithGoogle(credential);
      saveSession(session, true);
      onSuccess({ name: session.user.display_name, email: session.user.email, preferredLanguage: session.user.preferred_language });
      onNavigate('language-onboarding');
    } catch (error) {
      setFormError(error instanceof Error ? error.message : 'Unable to sign in with Google.');
    } finally {
      setIsLoading(false);
    }
  }, [onNavigate, onSuccess]);

  if (step === 'otp') {
    return (
      <motion.div
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: -8 }}
        transition={{ duration: 0.25 }}
        className="bg-white rounded-3xl border border-slate-200/90 p-7 sm:p-9 md:p-10 shadow-lg shadow-slate-200/40"
      >
        <button
          type="button"
          onClick={() => {
            clearPendingRegistration();
            setPendingId('');
            setStep('form');
            setFormError(null);
          }}
          className="inline-flex items-center gap-2 text-xs sm:text-sm font-semibold text-slate-500 hover:text-slate-800 transition-colors mb-5 cursor-pointer"
        >
          <ArrowLeft className="w-4 h-4" /> Back to details
        </button>

        <div className="mb-6">
          <h2 className="text-2xl sm:text-3xl font-extrabold text-slate-900 tracking-tight">Check your email</h2>
          <p className="text-sm sm:text-base text-slate-500 mt-1.5">
            We sent a 6-digit verification code to <span className="font-semibold text-slate-800">{email}</span>.
          </p>
        </div>

        <AnimatePresence>
          {formError && (
            <motion.div
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: 'auto' }}
              exit={{ opacity: 0, height: 0 }}
              className="mb-5 overflow-hidden"
            >
              <div className="flex items-center gap-2.5 p-3.5 rounded-xl bg-rose-50 border border-rose-200/80 text-rose-700 text-xs sm:text-sm font-medium">
                <AlertCircle className="w-4.5 h-4.5 shrink-0 text-rose-600" />
                <span>{formError}</span>
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        <form onSubmit={handleVerifyOtp} className="space-y-4.5">
          <div>
            <label htmlFor="otp-input" className="block text-xs sm:text-sm font-semibold text-slate-700 mb-1.5">
              Verification code
            </label>
            <div className="relative">
              <div className="absolute inset-y-0 left-0 pl-3.5 flex items-center pointer-events-none text-slate-400">
                <KeyRound className="w-4.5 h-4.5" />
              </div>
              <input
                id="otp-input"
                type="text"
                inputMode="numeric"
                autoComplete="one-time-code"
                maxLength={6}
                value={otp}
                onChange={(e) => {
                  const digits = e.target.value.replace(/\D/g, '').slice(0, 6);
                  setOtp(digits);
                  if (formError) setFormError(null);
                }}
                placeholder="000000"
                className="w-full h-14 pl-11 pr-3.5 text-center text-xl tracking-widest font-mono font-bold bg-slate-50/50 hover:bg-slate-50 focus:bg-white border border-slate-200 focus:border-[#08BCFD] rounded-xl outline-none focus:ring-3 focus:ring-[#08BCFD]/20 transition-all text-slate-900 placeholder:text-slate-300"
              />
            </div>
          </div>

          <button
            id="verify-otp-submit-button"
            type="submit"
            disabled={isLoading || otp.length !== 6}
            className="w-full h-12 mt-2 px-4 rounded-xl bg-[#08BCFD] hover:bg-[#0284C7] text-white text-sm sm:text-base font-semibold transition-all shadow-md shadow-[#08BCFD]/25 active:scale-[0.99] flex items-center justify-center gap-2 disabled:opacity-70 cursor-pointer"
          >
            {isLoading ? (
              <>
                <Loader2 className="w-4.5 h-4.5 animate-spin" />
                <span>Verifying...</span>
              </>
            ) : (
              <>
                <span>Complete registration</span>
                <ArrowRight className="w-4.5 h-4.5" />
              </>
            )}
          </button>

          <div className="pt-2 text-center">
            <button
              type="button"
              onClick={handleResendOtp}
              disabled={cooldown > 0 || resending}
              className="text-xs sm:text-sm font-semibold text-[#0284C7] hover:text-[#08BCFD] disabled:text-slate-400 transition-colors cursor-pointer disabled:cursor-not-allowed"
            >
              {cooldown > 0 ? `Resend code in ${cooldown}s` : resending ? 'Resending code...' : 'Resend verification code'}
            </button>
          </div>
        </form>
      </motion.div>
    );
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -8 }}
      transition={{ duration: 0.25 }}
      className="bg-white rounded-3xl border border-slate-200/90 p-7 sm:p-9 md:p-10 shadow-lg shadow-slate-200/40"
    >
      {/* Title & Subtitle */}
      <div className="mb-6">
        <h2 className="text-2xl sm:text-3xl font-extrabold text-slate-900 tracking-tight">Create your account</h2>
        <p className="text-sm sm:text-base text-slate-500 mt-1.5">Chat across 10+ languages with automatic translation.</p>
      </div>

      {/* Error Alert */}
      <AnimatePresence>
        {formError && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            className="mb-5 overflow-hidden"
          >
            <div className="flex items-center gap-2.5 p-3.5 rounded-xl bg-rose-50 border border-rose-200/80 text-rose-700 text-xs sm:text-sm font-medium">
              <AlertCircle className="w-4.5 h-4.5 shrink-0 text-rose-600" />
              <span>{formError}</span>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Continue with Google */}
      <GoogleSignInButton
        disabled={isLoading}
        onCredential={handleGoogleSignUp}
        onError={setFormError}
      />

      {/* Divider */}
      <div className="relative my-6">
        <div className="absolute inset-0 flex items-center">
          <div className="w-full border-t border-slate-200" />
        </div>
        <div className="relative flex justify-center text-xs">
          <span className="bg-white px-3 text-slate-400 font-medium">Or continue with email</span>
        </div>
      </div>

      {/* Form Fields */}
      <form onSubmit={handleSubmit} className="space-y-4.5">
        {/* Full Name */}
        <div>
          <label htmlFor="signup-name" className="block text-xs sm:text-sm font-semibold text-slate-700 mb-1.5">
            Full name
          </label>
          <div className="relative">
            <div className="absolute inset-y-0 left-0 pl-3.5 flex items-center pointer-events-none text-slate-400">
              <User className="w-4.5 h-4.5" />
            </div>
            <input
              id="signup-name"
              type="text"
              autoComplete="name"
              value={fullName}
              onChange={(e) => {
                setFullName(e.target.value);
                if (formError) setFormError(null);
              }}
              onBlur={() => setTouched({ ...touched, fullName: true })}
              placeholder="Your name"
              className="w-full h-12 pl-11 pr-3.5 text-sm sm:text-base bg-slate-50/50 hover:bg-slate-50 focus:bg-white border border-slate-200 focus:border-[#08BCFD] rounded-xl outline-none focus:ring-3 focus:ring-[#08BCFD]/20 transition-all text-slate-900 placeholder:text-slate-400"
            />
          </div>
          {touched.fullName && !fullName.trim() && (
            <p className="text-xs text-rose-500 mt-1.5 font-medium">Full name is required</p>
          )}
        </div>

        {/* Email */}
        <div>
          <label htmlFor="signup-email" className="block text-xs sm:text-sm font-semibold text-slate-700 mb-1.5">
            Email address
          </label>
          <div className="relative">
            <div className="absolute inset-y-0 left-0 pl-3.5 flex items-center pointer-events-none text-slate-400">
              <Mail className="w-4.5 h-4.5" />
            </div>
            <input
              id="signup-email"
              type="email"
              autoComplete="email"
              value={email}
              onChange={(e) => {
                setEmail(e.target.value);
                if (formError) setFormError(null);
              }}
              onBlur={() => setTouched({ ...touched, email: true })}
              placeholder="you@example.com"
              className={`w-full h-12 pl-11 pr-3.5 text-sm sm:text-base bg-slate-50/50 hover:bg-slate-50 focus:bg-white border rounded-xl outline-none transition-all text-slate-900 placeholder:text-slate-400 ${
                touched.email && !isEmailValid && email
                  ? 'border-rose-300 focus:border-rose-500 focus:ring-3 focus:ring-rose-500/15'
                  : 'border-slate-200 focus:border-[#08BCFD] focus:ring-3 focus:ring-[#08BCFD]/20'
              }`}
            />
          </div>
          {touched.email && email.length > 0 && !isEmailValid && (
            <p className="text-xs text-rose-500 mt-1.5 font-medium flex items-center gap-1">
              <AlertCircle className="w-3.5 h-3.5" /> Please enter a valid email format
            </p>
          )}
        </div>

        {/* Password */}
        <div>
          <label htmlFor="signup-password" className="block text-xs sm:text-sm font-semibold text-slate-700 mb-1.5">
            Password
          </label>
          <div className="relative">
            <div className="absolute inset-y-0 left-0 pl-3.5 flex items-center pointer-events-none text-slate-400">
              <Lock className="w-4.5 h-4.5" />
            </div>
            <input
              id="signup-password"
              type={showPassword ? 'text' : 'password'}
              autoComplete="new-password"
              value={password}
              onChange={(e) => {
                setPassword(e.target.value);
                if (formError) setFormError(null);
              }}
              onBlur={() => setTouched({ ...touched, password: true })}
              placeholder="Enter your password"
              className="w-full h-12 pl-11 pr-11 text-sm sm:text-base bg-slate-50/50 hover:bg-slate-50 focus:bg-white border border-slate-200 focus:border-[#08BCFD] rounded-xl outline-none focus:ring-3 focus:ring-[#08BCFD]/20 transition-all text-slate-900 placeholder:text-slate-400"
            />
            <button
              id="toggle-signup-password-visibility-button"
              type="button"
              onClick={() => setShowPassword(!showPassword)}
              className="absolute inset-y-0 right-0 pr-3.5 flex items-center text-slate-400 hover:text-[#08BCFD] transition-colors cursor-pointer"
            >
              {showPassword ? <EyeOff className="w-4.5 h-4.5" /> : <Eye className="w-4.5 h-4.5" />}
            </button>
          </div>
          {/* Real-time inline password strength indicators */}
          {password.length > 0 && (
            <div className="mt-2 space-y-1 bg-slate-50 p-2.5 rounded-xl border border-slate-100">
              <div className="flex items-center gap-1.5 text-xs">
                {isPasswordLengthOk ? (
                  <span className="text-emerald-600 flex items-center gap-1 font-semibold">
                    <CheckCircle2 className="w-3.5 h-3.5" /> At least 8 characters
                  </span>
                ) : (
                  <span className="text-slate-400 flex items-center gap-1">
                    • Minimum 8 characters ({password.length}/8)
                  </span>
                )}
              </div>
              <div className="flex items-center gap-1.5 text-xs">
                {hasLetterAndNumber ? (
                  <span className="text-emerald-600 flex items-center gap-1 font-semibold">
                    <CheckCircle2 className="w-3.5 h-3.5" /> Contains letters & numbers
                  </span>
                ) : (
                  <span className="text-slate-400 flex items-center gap-1">
                    • Must include letters and numbers
                  </span>
                )}
              </div>
            </div>
          )}
        </div>

        {/* Confirm Password */}
        <div>
          <label htmlFor="signup-confirm-password" className="block text-xs sm:text-sm font-semibold text-slate-700 mb-1.5">
            Confirm password
          </label>
          <div className="relative">
            <div className="absolute inset-y-0 left-0 pl-3.5 flex items-center pointer-events-none text-slate-400">
              <Lock className="w-4.5 h-4.5" />
            </div>
            <input
              id="signup-confirm-password"
              type={showConfirmPassword ? 'text' : 'password'}
              autoComplete="new-password"
              value={confirmPassword}
              onChange={(e) => {
                setConfirmPassword(e.target.value);
                if (formError) setFormError(null);
              }}
              onBlur={() => setTouched({ ...touched, confirmPassword: true })}
              placeholder="Re-enter your password"
              className={`w-full h-12 pl-11 pr-11 text-sm sm:text-base bg-slate-50/50 hover:bg-slate-50 focus:bg-white border rounded-xl outline-none transition-all text-slate-900 placeholder:text-slate-400 ${
                touched.confirmPassword && confirmPassword && !isPasswordMatch
                  ? 'border-rose-300 focus:border-rose-500 focus:ring-3 focus:ring-rose-500/15'
                  : 'border-slate-200 focus:border-[#08BCFD] focus:ring-3 focus:ring-[#08BCFD]/20'
              }`}
            />
            <button
              id="toggle-signup-confirm-password-visibility-button"
              type="button"
              onClick={() => setShowConfirmPassword(!showConfirmPassword)}
              className="absolute inset-y-0 right-0 pr-3.5 flex items-center text-slate-400 hover:text-[#08BCFD] transition-colors cursor-pointer"
            >
              {showConfirmPassword ? <EyeOff className="w-4.5 h-4.5" /> : <Eye className="w-4.5 h-4.5" />}
            </button>
          </div>
          {touched.confirmPassword && confirmPassword.length > 0 && (
            <div className="mt-1.5">
              {isPasswordMatch ? (
                <span className="text-emerald-600 text-xs font-semibold flex items-center gap-1">
                  <CheckCircle2 className="w-3.5 h-3.5" /> Passwords match
                </span>
              ) : (
                <span className="text-rose-500 text-xs font-semibold flex items-center gap-1">
                  <AlertCircle className="w-3.5 h-3.5" /> Passwords do not match
                </span>
              )}
            </div>
          )}
        </div>

        {/* Terms and Conditions Checkbox */}
        <label className="flex items-start gap-2.5 cursor-pointer select-none pt-1">
          <input
            id="signup-accept-terms"
            type="checkbox"
            checked={hasAcceptedTerms}
            onChange={(event) => {
              setHasAcceptedTerms(event.target.checked);
              if (formError) setFormError(null);
            }}
            className="peer sr-only"
          />
          <span aria-hidden="true" className="mt-0.5 flex h-4.5 w-4.5 shrink-0 items-center justify-center rounded border border-slate-300 bg-white text-transparent transition-colors peer-checked:border-[#08BCFD] peer-checked:bg-[#08BCFD] peer-checked:text-white peer-focus-visible:ring-2 peer-focus-visible:ring-[#08BCFD]/35">
            <Check className="h-3.5 w-3.5" strokeWidth={3} />
          </span>
          <span className="text-xs sm:text-sm text-slate-600 leading-relaxed">
            Tôi đồng ý với{' '}
            <Link href="/terms" target="_blank" className="font-semibold text-[#0284C7] hover:text-[#08BCFD] hover:underline">Điều khoản dịch vụ</Link>{' '}
            và{' '}
            <Link href="/privacy" target="_blank" className="font-semibold text-[#0284C7] hover:text-[#08BCFD] hover:underline">Chính sách riêng tư</Link>.
          </span>
        </label>

        {/* Primary Submit Button */}
        <button
          id="signup-submit-button"
          type="submit"
          disabled={isLoading}
          className="w-full h-12 mt-3 px-4 rounded-xl bg-[#08BCFD] hover:bg-[#0284C7] text-white text-sm sm:text-base font-semibold transition-all shadow-md shadow-[#08BCFD]/25 active:scale-[0.99] flex items-center justify-center gap-2 disabled:opacity-70 cursor-pointer"
        >
          {isLoading ? (
            <>
              <Loader2 className="w-4.5 h-4.5 animate-spin" />
              <span>Sending verification code...</span>
            </>
          ) : (
            <>
              <span>Create account</span>
              <ArrowRight className="w-4.5 h-4.5" />
            </>
          )}
        </button>
      </form>

      {/* Bottom Switcher */}
      <div className="mt-6 text-center text-xs sm:text-sm text-slate-500">
        Already have an account?{' '}
        <button
          id="navigate-signin-button"
          type="button"
          onClick={() => onNavigate('signin')}
          className="font-semibold text-[#0284C7] hover:text-[#08BCFD] transition-colors hover:underline ml-1 cursor-pointer"
        >
          Sign in
        </button>
      </div>
    </motion.div>
  );
};
