"use client";

import React, { useCallback, useState } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import { Mail, Lock, Eye, EyeOff, AlertCircle, ArrowRight, Check, Loader2 } from 'lucide-react';
import { GoogleSignInButton } from './GoogleSignInButton';
import { AuthScreen, UserProfile } from '../types';
import { signIn, signInWithGoogle } from '../api/auth-api';
import { saveSession } from '@/shared/lib/session';

interface SignInFormProps {
  onNavigate: (screen: AuthScreen) => void;
  onSuccess: (user: UserProfile) => void;
}

export const SignInForm: React.FC<SignInFormProps> = ({ onNavigate, onSuccess }) => {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  // An unchecked "Remember me" control must be visibly neutral; persistence
  // is an explicit choice rather than the default sign-in behaviour.
  const [rememberMe, setRememberMe] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErrorMessage(null);

    // Validation
    if (!email.trim()) {
      setErrorMessage('Please enter your email address.');
      return;
    }

    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    if (!emailRegex.test(email.trim())) {
      setErrorMessage('Please enter a valid email address.');
      return;
    }

    if (!password) {
      setErrorMessage('Please enter your password.');
      return;
    }

    if (password.length < 6) {
      setErrorMessage('Password must be at least 6 characters.');
      return;
    }

    setIsLoading(true);

    try {
      const session = await signIn(email, password, rememberMe);
      saveSession(session, rememberMe);
      onSuccess({
        name: session.user.display_name,
        email: session.user.email,
        preferredLanguage: session.user.preferred_language,
        role: session.user.role,
      });
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Unable to sign in. Please try again.');
    } finally {
      setIsLoading(false);
    }
  };

  const handleGoogleSignIn = useCallback(async (credential: string) => {
    setErrorMessage(null);
    setIsLoading(true);
    try {
      const session = await signInWithGoogle(credential, rememberMe);
      saveSession(session, rememberMe);
      onSuccess({ name: session.user.display_name, email: session.user.email, preferredLanguage: session.user.preferred_language, role: session.user.role });
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Unable to sign in with Google.');
    } finally {
      setIsLoading(false);
    }
  }, [onSuccess, rememberMe]);

  return (
    <motion.div
      initial={false}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -8 }}
      transition={{ duration: 0.25 }}
      className="bg-white rounded-3xl border border-slate-200/90 p-7 sm:p-9 md:p-10 shadow-lg shadow-slate-200/40"
    >
      {/* Title & Subtitle */}
      <div className="mb-6">
        <h2 className="text-2xl sm:text-3xl font-extrabold text-slate-900 tracking-tight">Welcome back</h2>
        <p className="text-sm sm:text-base text-slate-500 mt-1.5">Sign in to continue to LinguaFlow.</p>
      </div>

      {/* Error Alert */}
      <AnimatePresence>
        {errorMessage && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            className="mb-5 overflow-hidden"
          >
            <div className="flex items-center gap-2.5 p-3.5 rounded-xl bg-rose-50 border border-rose-200/80 text-rose-700 text-xs sm:text-sm font-medium">
              <AlertCircle className="w-4.5 h-4.5 shrink-0 text-rose-600" />
              <span>{errorMessage}</span>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Continue with Google */}
      <GoogleSignInButton
        disabled={isLoading}
        onCredential={handleGoogleSignIn}
        onError={setErrorMessage}
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
      <form action="/api/auth/login" method="post" onSubmit={handleSubmit} className="space-y-4.5">
        {/* Email Field */}
        <div>
          <label htmlFor="signin-email" className="block text-xs sm:text-sm font-semibold text-slate-700 mb-1.5">
            Email address
          </label>
          <div className="relative">
            <div className="absolute inset-y-0 left-0 pl-3.5 flex items-center pointer-events-none text-slate-400">
              <Mail className="w-4.5 h-4.5" />
            </div>
            <input
              id="signin-email"
              name="email"
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

        {/* Password Field */}
        <div>
          <div className="flex items-center justify-between mb-1.5">
            <label htmlFor="signin-password" className="block text-xs sm:text-sm font-semibold text-slate-700">
              Password
            </label>
          </div>
          <div className="relative">
            <div className="absolute inset-y-0 left-0 pl-3.5 flex items-center pointer-events-none text-slate-400">
              <Lock className="w-4.5 h-4.5" />
            </div>
            <input
              id="signin-password"
              name="password"
              type={showPassword ? 'text' : 'password'}
              autoComplete="current-password"
              value={password}
              onChange={(e) => {
                setPassword(e.target.value);
                if (errorMessage) setErrorMessage(null);
              }}
              placeholder="Enter your password"
              className="w-full h-12 pl-11 pr-11 text-sm sm:text-base bg-slate-50/50 hover:bg-slate-50 focus:bg-white border border-slate-200 focus:border-[#08BCFD] rounded-xl outline-none focus:ring-3 focus:ring-[#08BCFD]/20 transition-all text-slate-900 placeholder:text-slate-400"
            />
            <button
              id="toggle-password-visibility-button"
              type="button"
              onClick={() => setShowPassword(!showPassword)}
              className="absolute inset-y-0 right-0 pr-3.5 flex items-center text-slate-400 hover:text-[#08BCFD] transition-colors cursor-pointer"
              aria-label={showPassword ? 'Hide password' : 'Show password'}
            >
              {showPassword ? <EyeOff className="w-4.5 h-4.5" /> : <Eye className="w-4.5 h-4.5" />}
            </button>
          </div>
        </div>

        {/* Options: Remember me & Forgot password */}
        <div className="flex items-center justify-between pt-0.5">
          <label className="flex items-center gap-2 cursor-pointer select-none">
            <input
              id="remember-me-checkbox"
              name="remember"
              type="checkbox"
              checked={rememberMe}
              onChange={(e) => setRememberMe(e.target.checked)}
              className="peer sr-only"
            />
            <span aria-hidden="true" className="flex h-4 w-4 shrink-0 items-center justify-center rounded border border-slate-300 bg-white text-transparent transition-colors peer-checked:border-[#08BCFD] peer-checked:bg-[#08BCFD] peer-checked:text-white peer-focus-visible:ring-2 peer-focus-visible:ring-[#08BCFD]/35">
              <Check className="h-3 w-3" strokeWidth={3} />
            </span>
            <span className="text-xs sm:text-sm text-slate-600 font-medium">Remember me</span>
          </label>
          <button
            id="forgot-password-link"
            type="button"
            onClick={() => onNavigate('forgot-password')}
            className="text-xs sm:text-sm font-semibold text-[#0284C7] hover:text-[#08BCFD] transition-colors hover:underline cursor-pointer"
          >
            Forgot password?
          </button>
        </div>

        {/* Primary Submit Button */}
        <button
          id="signin-submit-button"
          type="submit"
          disabled={isLoading}
          className="w-full h-12 mt-2 px-4 rounded-xl bg-[#08BCFD] hover:bg-[#0284C7] text-white text-sm sm:text-base font-semibold transition-all shadow-md shadow-[#08BCFD]/25 active:scale-[0.99] flex items-center justify-center gap-2 disabled:opacity-70 cursor-pointer"
        >
          {isLoading ? (
            <>
              <Loader2 className="w-4.5 h-4.5 animate-spin" />
              <span>Signing in…</span>
            </>
          ) : (
            <>
              <span>Sign in</span>
              <ArrowRight className="w-4.5 h-4.5" />
            </>
          )}
        </button>
      </form>

      {/* Bottom Switcher */}
      <div className="mt-6 text-center text-xs sm:text-sm text-slate-500">
        Don't have an account?{' '}
        <button
          id="navigate-signup-button"
          type="button"
          onClick={() => onNavigate('signup')}
          className="font-semibold text-[#0284C7] hover:text-[#08BCFD] transition-colors hover:underline ml-1 cursor-pointer"
        >
          Sign up
        </button>
      </div>
    </motion.div>
  );
};
