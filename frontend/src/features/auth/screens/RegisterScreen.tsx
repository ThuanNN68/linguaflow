"use client";

import { useState } from "react";
import { AnimatePresence } from "motion/react";
import { useRouter } from "next/navigation";
import { LanguageOnboarding } from "../components/LanguageOnboarding";
import { SignUpForm } from "../components/SignUpForm";
import { SUPPORTED_LANGUAGES } from "../constants/languages";
import { updateInterfaceLanguage, updatePreferredLanguage } from "../api/auth-api";
import { getAccessToken, updateStoredUser } from "@/shared/lib/session";
import type { AuthScreen, UserProfile } from "../types";

export function RegisterScreen() {
  const router = useRouter();
  const [screen, setScreen] = useState<AuthScreen>("signup");
  const [user, setUser] = useState<UserProfile>({
    name: "",
    email: "",
    preferredLanguage: "vietnamese",
  });

  const navigate = (next: AuthScreen) => {
    if (next === "signin") router.push("/login");
    else setScreen(next);
  };

  const handleRegistered = (registered: Partial<UserProfile>) => {
    setUser((current) => ({ ...current, ...registered }));
  };

  const handleContinue = async () => {
    const language = SUPPORTED_LANGUAGES.find((item) => item.id === user.preferredLanguage);
    const token = getAccessToken();
    if (language && token) {
      try {
        await updatePreferredLanguage(token, language.code);
        const updatedUser = await updateInterfaceLanguage(token, language.code);
        updateStoredUser(updatedUser);
      } catch {
        // Registration is complete even if the optional preference update is unavailable.
      }
    }
    router.push("/chat");
  };

  return (
    <AnimatePresence mode="wait">
      {screen === "language-onboarding" ? (
        <LanguageOnboarding
          key="language-onboarding-screen"
          initialLanguage={user.preferredLanguage}
          onSelectLanguage={(preferredLanguage) =>
            setUser((current) => ({ ...current, preferredLanguage }))
          }
          onContinue={handleContinue}
        />
      ) : (
        <SignUpForm
          key="signup-form"
          onNavigate={navigate}
          onSuccess={handleRegistered}
        />
      )}
    </AnimatePresence>
  );
}
