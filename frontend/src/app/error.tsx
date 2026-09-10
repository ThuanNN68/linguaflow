"use client";

import { useEffect, useState } from "react";
import { detectInterfaceLanguage, errorBoundaryText } from "@/shared/i18n/shell";

export default function RouteError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  const [copy, setCopy] = useState(() => errorBoundaryText("en", "route"));

  useEffect(() => {
    console.error("LinguaFlow route error", error);
    const frame = requestAnimationFrame(() => {
      setCopy(errorBoundaryText(detectInterfaceLanguage(), "route"));
    });
    return () => cancelAnimationFrame(frame);
  }, [error]);

  return (
    <main className="error-boundary" role="alert">
      <div className="error-boundary__card">
        <p className="error-boundary__eyebrow">{copy.eyebrow}</p>
        <h1>{copy.title}</h1>
        <p>{copy.body}</p>
        <button type="button" onClick={reset}>{copy.action}</button>
      </div>
    </main>
  );
}
