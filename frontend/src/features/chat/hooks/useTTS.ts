import { useState, useCallback, useEffect, useRef } from "react";

type AudioFallback = () => Promise<Blob>;

export function useTTS() {
  const [isSpeaking, setIsSpeaking] = useState(false);
  const [currentMessageId, setCurrentMessageId] = useState<string | null>(null);

  // Track component unmount to stop speech
  const isMounted = useRef(true);
  const audio = useRef<HTMLAudioElement | null>(null);
  const audioUrl = useRef<string | null>(null);
  const generation = useRef(0);

  const stop = useCallback(() => {
    generation.current += 1;
    window.speechSynthesis?.cancel();
    audio.current?.pause();
    audio.current = null;
    if (audioUrl.current) URL.revokeObjectURL(audioUrl.current);
    audioUrl.current = null;
    setIsSpeaking(false);
    setCurrentMessageId(null);
  }, []);

  useEffect(() => {
    isMounted.current = true;
    return () => {
      isMounted.current = false;
      window.speechSynthesis?.cancel();
      audio.current?.pause();
      if (audioUrl.current) URL.revokeObjectURL(audioUrl.current);
    };
  }, []);

  const playFallback = useCallback(async (
    fallback: AudioFallback,
    messageId: string | undefined,
    expectedGeneration: number,
  ) => {
    try {
      const blob = await fallback();
      if (!isMounted.current || generation.current !== expectedGeneration) return;
      const url = URL.createObjectURL(blob);
      const player = new Audio(url);
      audio.current = player;
      audioUrl.current = url;
      player.onplay = () => {
        if (!isMounted.current) return;
        setIsSpeaking(true);
        setCurrentMessageId(messageId ?? null);
      };
      player.onended = player.onerror = () => {
        if (audioUrl.current === url) URL.revokeObjectURL(url);
        audioUrl.current = null;
        audio.current = null;
        if (isMounted.current) {
          setIsSpeaking(false);
          setCurrentMessageId(null);
        }
      };
      await player.play();
    } catch (error) {
      console.error("Server-side speech synthesis failed", error);
      if (isMounted.current && generation.current === expectedGeneration) {
        setIsSpeaking(false);
        setCurrentMessageId(null);
      }
    }
  }, []);

  const speak = useCallback((
    text: string,
    language: string,
    messageId?: string,
    fallback?: AudioFallback,
  ) => {
    stop();
    const expectedGeneration = generation.current;
    if (!window.speechSynthesis) {
      if (fallback) void playFallback(fallback, messageId, expectedGeneration);
      return;
    }

    const utterance = new SpeechSynthesisUtterance(text);

    // Convert backend language code if needed to BCP 47 (e.g. "en" to "en-US")
    utterance.lang = language;

    // Try to find a voice that matches the language
    const voices = window.speechSynthesis.getVoices();
    const matchingVoice = voices.find(v => v.lang.startsWith(language));
    if (matchingVoice) {
        utterance.voice = matchingVoice;
    }

    utterance.onstart = () => {
      if (isMounted.current) {
        setIsSpeaking(true);
        if (messageId) {
          setCurrentMessageId(messageId);
        }
      }
    };

    utterance.onend = () => {
      if (isMounted.current) {
        setIsSpeaking(false);
        setCurrentMessageId(null);
      }
    };

    utterance.onerror = () => {
      if (generation.current !== expectedGeneration) return;
      if (isMounted.current) {
        setIsSpeaking(false);
        setCurrentMessageId(null);
      }
      if (fallback) void playFallback(fallback, messageId, expectedGeneration);
    };

    window.speechSynthesis.speak(utterance);
  }, [playFallback, stop]);

  return {
    speak,
    stop,
    isSpeaking,
    currentMessageId,
  };
}
