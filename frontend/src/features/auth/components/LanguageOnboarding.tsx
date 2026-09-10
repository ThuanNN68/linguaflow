"use client";

import React, { useState } from 'react';
import { motion } from 'motion/react';
import { Check, ArrowRight, Languages, Sparkles } from 'lucide-react';
import { SUPPORTED_LANGUAGES } from '../constants/languages';
import { LanguageOption } from '../types';

interface LanguageOnboardingProps {
  initialLanguage?: string;
  onSelectLanguage: (languageId: string) => void;
  onContinue: () => void;
}

export const LanguageOnboarding: React.FC<LanguageOnboardingProps> = ({
  initialLanguage = 'vietnamese',
  onSelectLanguage,
  onContinue,
}) => {
  const [selectedLang, setSelectedLang] = useState<string>(initialLanguage);

  const handleSelect = (lang: LanguageOption) => {
    setSelectedLang(lang.id);
    onSelectLanguage(lang.id);
  };

  const currentSelectedObj = SUPPORTED_LANGUAGES.find((l) => l.id === selectedLang);

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
        <div className="w-12 h-12 rounded-2xl bg-sky-50 border border-sky-100 flex items-center justify-center text-[#0284C7] mb-3.5 shadow-2xs">
          <Languages className="w-6 h-6" />
        </div>
        <h2 className="text-2xl sm:text-3xl font-extrabold text-slate-900 tracking-tight">
          What language do you prefer?
        </h2>
        <p className="text-sm sm:text-base text-slate-500 mt-1.5 leading-relaxed">
          We'll automatically translate conversations into this language in real-time.
        </p>
      </div>

      {/* Language Selection Grid (10 languages) */}
      <div className="grid grid-cols-2 gap-3 max-h-[320px] sm:max-h-[360px] overflow-y-auto pr-1 py-1 custom-scrollbar">
        {SUPPORTED_LANGUAGES.map((lang) => {
          const isSelected = selectedLang === lang.id;
          return (
            <button
              id={`language-option-${lang.id}`}
              key={lang.id}
              type="button"
              onClick={() => handleSelect(lang)}
              className={`relative text-left p-3.5 rounded-2xl border transition-all cursor-pointer flex flex-col justify-between ${
                isSelected
                  ? 'border-[#08BCFD] bg-sky-50/70 shadow-xs ring-2 ring-[#08BCFD]/20'
                  : 'border-slate-200 hover:border-slate-300 hover:bg-slate-50/70 bg-white'
              }`}
            >
              <div className="flex items-center justify-between w-full mb-1.5">
                <span className="text-2xl" role="img" aria-label={lang.name}>
                  {lang.flag}
                </span>
                {isSelected && (
                  <div className="w-5 h-5 rounded-full bg-[#08BCFD] text-white flex items-center justify-center">
                    <Check className="w-3.5 h-3.5 stroke-[3]" />
                  </div>
                )}
              </div>
              <div>
                <p className={`text-sm sm:text-base font-bold tracking-tight ${isSelected ? 'text-[#061831]' : 'text-slate-800'}`}>
                  {lang.nativeName}
                </p>
                <p className={`text-xs ${isSelected ? 'text-[#0284C7] font-medium' : 'text-slate-400'}`}>
                  {lang.name}
                </p>
              </div>
            </button>
          );
        })}
      </div>

      {/* Real-time sample preview pill */}
      {currentSelectedObj && (
        <div className="mt-5 p-3 rounded-xl bg-slate-50 border border-slate-200/80 flex items-center justify-between text-xs sm:text-sm">
          <div className="flex items-center gap-2 text-slate-600 truncate mr-2">
            <Sparkles className="w-4 h-4 text-[#08BCFD] shrink-0" />
            <span className="truncate">
              Greeting preview: <span className="font-semibold text-slate-900">{currentSelectedObj.sampleGreeting}</span>
            </span>
          </div>
          <span className="text-[11px] font-semibold uppercase tracking-wider text-[#0284C7] bg-sky-100/70 px-2.5 py-1 rounded-md shrink-0">
            {currentSelectedObj.code.toUpperCase()}
          </span>
        </div>
      )}

      {/* Primary Continue Button */}
      <button
        id="onboarding-continue-button"
        type="button"
        onClick={onContinue}
        className="w-full h-12 mt-6 px-4 rounded-xl bg-[#08BCFD] hover:bg-[#0284C7] text-white text-sm sm:text-base font-semibold transition-all shadow-md shadow-[#08BCFD]/25 active:scale-[0.99] flex items-center justify-center gap-2 cursor-pointer"
      >
        <span>Continue to LinguaFlow</span>
        <ArrowRight className="w-4.5 h-4.5" />
      </button>
    </motion.div>
  );
};
