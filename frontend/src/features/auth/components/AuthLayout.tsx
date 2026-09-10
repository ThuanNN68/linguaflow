"use client";

import React from 'react';
import Image from 'next/image';
import { motion } from 'motion/react';
import { Sparkles, Globe, ArrowRightLeft } from 'lucide-react';

interface AuthLayoutProps {
  children: React.ReactNode;
}

const LinguaFlowLogoIcon: React.FC<{ className?: string }> = ({ className = "w-14 h-14" }) => (
  <Image
    src="/brand/brand-mark.svg"
    alt="LinguaFlow Logo"
    width={56}
    height={56}
    className={`${className} object-contain shrink-0 select-none shadow-md shadow-[#08BCFD]/25 rounded-2xl`}
  />
);

export const AuthLayout: React.FC<AuthLayoutProps> = ({ children }) => {
  return (
    <div id="auth-app-shell" className="min-h-screen w-full bg-slate-50 text-slate-900 flex flex-col md:flex-row antialiased selection:bg-[#08BCFD] selection:text-white">
      {/* LEFT COLUMN (Desktop only: >= 768px - Perfectly balanced medium-light sky/slate tone) */}
      <div className="hidden md:flex md:w-1/2 lg:w-[48%] xl:w-[45%] bg-gradient-to-br from-sky-200/50 via-slate-200/60 to-cyan-100/80 border-r border-slate-200/90 p-8 lg:p-12 xl:p-16 flex-col justify-between relative overflow-hidden">
        {/* Ambient subtle glow shapes */}
        <div className="absolute -top-24 -left-24 w-96 h-96 bg-[#08BCFD]/15 rounded-full blur-3xl pointer-events-none" />
        <div className="absolute -bottom-24 -right-24 w-96 h-96 bg-sky-200/40 rounded-full blur-3xl pointer-events-none" />

        {/* Brand header with enlarged, sharp, seamless logo */}
        <div className="relative z-10">
          <div className="inline-flex items-center gap-3.5 mb-8 select-none">
            <LinguaFlowLogoIcon className="w-14 h-14" />
            <span className="font-black tracking-tight text-3xl select-none leading-none">
              <span className="text-[#061831]">Lingua</span><span className="text-[#08BCFD]">flow</span>
            </span>
          </div>

          <div className="max-w-md">
            <h1 className="text-3xl lg:text-4xl font-extrabold text-slate-900 tracking-tight leading-tight mb-4">
              Chat beyond <br />
              <span className="text-transparent bg-clip-text bg-gradient-to-r from-[#0284C7] to-[#08BCFD]">
                language barriers.
              </span>
            </h1>
            <p className="text-slate-600 text-sm lg:text-base leading-relaxed font-normal">
              Connect with anyone around the world with instant, natural auto-translation directly in your chat stream.
            </p>
          </div>
        </div>

        {/* Visual Multilingual Messaging Illustration (Distinct white & pastel cards popping on deeper background) */}
        <div className="relative z-10 my-8 py-2">
          <div className="relative max-w-sm mx-auto space-y-3.5">
            {/* Bubble 1: Vietnamese (Clean white card with strong contrast) */}
            <motion.div
              initial={false}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.5, delay: 0.1 }}
              className="flex items-start gap-3 bg-white p-4 rounded-2xl border border-slate-200/90 shadow-md shadow-slate-300/40"
            >
              <div className="w-9 h-9 rounded-xl bg-amber-50 border border-amber-200/80 flex items-center justify-center text-sm font-semibold text-amber-800 shrink-0">
                🇻🇳
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center justify-between gap-2 mb-0.5">
                  <span className="text-xs font-bold text-slate-800">Minh Anh</span>
                  <span className="text-[10px] text-slate-400 font-mono">10:42 AM</span>
                </div>
                <p className="text-sm font-medium text-slate-900">Xin chào 👋</p>
                <div className="mt-2 flex items-center gap-1.5 text-[11px] text-[#0284C7] font-medium bg-sky-50 border border-sky-100 px-2.5 py-1 rounded-lg w-fit">
                  <Sparkles className="w-3.5 h-3.5 text-[#08BCFD]" />
                  <span>Translated: "Hello there 👋"</span>
                </div>
              </div>
            </motion.div>

            {/* Bubble 2: English (Self / Reply - Soft Light Pastel Cyan card) */}
            <motion.div
              initial={false}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.5, delay: 0.25 }}
              className="flex items-start gap-3 bg-[#EAF6FF] text-slate-800 p-4 rounded-2xl border border-[#BAE6FD] shadow-md shadow-sky-200/40 ml-6"
            >
              <div className="w-9 h-9 rounded-xl bg-white border border-[#BAE6FD] flex items-center justify-center text-sm font-semibold text-sky-800 shrink-0">
                🇺🇸
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center justify-between gap-2 mb-0.5">
                  <span className="text-xs font-bold text-[#0284C7]">Alex</span>
                  <span className="text-[10px] text-slate-400 font-mono">10:43 AM</span>
                </div>
                <p className="text-sm font-medium text-slate-900">Hello! Great to connect with you.</p>
                <div className="mt-2 flex items-center gap-1.5 text-[11px] text-[#0284C7] font-medium bg-white/95 border border-[#BAE6FD]/80 px-2.5 py-1 rounded-lg w-fit">
                  <ArrowRightLeft className="w-3.5 h-3.5 text-[#08BCFD]" />
                  <span>Auto-translating for group</span>
                </div>
              </div>
            </motion.div>

            {/* Bubble 3: Japanese (Clean white card) */}
            <motion.div
              initial={false}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.5, delay: 0.4 }}
              className="flex items-start gap-3 bg-white p-4 rounded-2xl border border-slate-200/90 shadow-md shadow-slate-300/40"
            >
              <div className="w-9 h-9 rounded-xl bg-rose-50 border border-rose-200/80 flex items-center justify-center text-sm font-semibold text-rose-800 shrink-0">
                🇯🇵
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center justify-between gap-2 mb-0.5">
                  <span className="text-xs font-bold text-slate-800">Yuki Tanaka</span>
                  <span className="text-[10px] text-slate-400 font-mono">10:44 AM</span>
                </div>
                <p className="text-sm font-medium text-slate-900">こんにちは！よろしくお願いします 👋</p>
                <div className="mt-2 flex items-center gap-1.5 text-[11px] text-[#0284C7] font-medium bg-sky-50 border border-sky-100 px-2.5 py-1 rounded-lg w-fit">
                  <Sparkles className="w-3.5 h-3.5 text-[#08BCFD]" />
                  <span>Translated: "Hello! Nice to meet you 👋"</span>
                </div>
              </div>
            </motion.div>
          </div>
        </div>

        {/* Footer info badge */}
        <div className="relative z-10 flex items-center gap-4 text-xs text-slate-600 font-medium">
          <div className="flex items-center gap-1.5">
            <Globe className="w-4 h-4 text-[#08BCFD]" />
            <span>Auto Language Detection</span>
          </div>
          <span className="text-slate-400">•</span>
          <span>Real-time AI Translation</span>
          <span className="text-slate-400">•</span>
          <span>Secure Messaging</span>
        </div>
      </div>

      {/* RIGHT COLUMN (Authentication View Container) */}
      <div className="flex-1 flex flex-col justify-center items-center p-5 sm:p-8 md:p-10 lg:p-12 min-h-screen relative overflow-y-auto bg-slate-50">
        {/* Mobile Header (< 768px) */}
        <div className="md:hidden w-full max-w-[480px] mb-6 pt-4 flex items-center justify-between">
          <div className="inline-flex items-center gap-2.5 select-none">
            <LinguaFlowLogoIcon className="w-11 h-11" />
            <span className="font-black tracking-tight text-2xl select-none leading-none">
              <span className="text-[#061831]">Lingua</span><span className="text-[#08BCFD]">flow</span>
            </span>
          </div>
          <span className="text-xs font-semibold text-[#0284C7] bg-sky-50 border border-sky-100 px-2.5 py-1 rounded-full">
            Multilingual
          </span>
        </div>

        {/* Central form wrapper (centered vertically, spacious width 480px) */}
        <div className="w-full max-w-[480px] my-auto">
          {children}
        </div>

        {/* Mobile footer hint */}
        <div className="md:hidden w-full max-w-[480px] mt-6 pb-4 text-center text-xs text-slate-400">
          LinguaFlow • Multilingual Messaging Platform
        </div>
      </div>
    </div>
  );
};
