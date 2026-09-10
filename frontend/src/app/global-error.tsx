"use client";

import { useEffect, useState } from "react";
import { detectInterfaceLanguage, errorBoundaryText } from "@/shared/i18n/shell";

export default function GlobalError({ reset }: { reset: () => void }) {
  const [language, setLanguage] = useState<ReturnType<typeof detectInterfaceLanguage>>("en");
  useEffect(() => {
    const frame = requestAnimationFrame(() => setLanguage(detectInterfaceLanguage()));
    return () => cancelAnimationFrame(frame);
  }, []);
  const copy = errorBoundaryText(language, "global");
  return (
    <html lang={language} dir={language === "ar" ? "rtl" : "ltr"}>
      <body>
        <main className="error-boundary" role="alert">
          <div className="error-boundary__card">
            <p className="error-boundary__eyebrow">{copy.eyebrow}</p>
            <h1>{copy.title}</h1>
            <p>{copy.body}</p>
            <button type="button" onClick={reset}>{copy.action}</button>
          </div>
        </main>
      </body>
    </html>
  );
}
