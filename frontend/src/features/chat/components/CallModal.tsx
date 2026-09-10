"use client";

import React, { useEffect, useRef, useState } from "react";
import type { DailyCall } from "@daily-co/daily-js";
import { Mic, MicOff, Phone, PhoneOff, Video, VideoOff } from "lucide-react";
import type { Conversation } from "../types";
import type { LanguageCode } from "../types";
import { DEFAULT_CALL_RING_TIMEOUT_SECONDS } from "../constants";
import { callText } from "../i18n";

export type CallPhase = "outgoing" | "incoming" | "active";

export interface ActiveCall {
  id: string;
  conversationId: string;
  callerId: string;
  calleeId: string;
  type: "voice" | "video";
  phase: CallPhase;
  roomUrl?: string;
  joinToken?: string;
}

interface CallModalProps {
  call: ActiveCall | null;
  conversation: Conversation | null;
  onAccept: () => void;
  onReject: () => void;
  onEnd: () => void;
  onMediaError: (message: string) => void;
  language: LanguageCode;
}

function MediaTrack({ track, muted = false }: { track: MediaStreamTrack | null; muted?: boolean }) {
  const element = useRef<HTMLVideoElement | HTMLAudioElement>(null);

  useEffect(() => {
    const mediaElement = element.current;
    if (!mediaElement || !track) return;
    mediaElement.srcObject = new MediaStream([track]);
    void mediaElement.play().catch(() => undefined);
    return () => { mediaElement.srcObject = null; };
  }, [track]);

  if (!track) return null;
  return track.kind === "video"
    ? <video ref={element as React.RefObject<HTMLVideoElement>} autoPlay playsInline muted={muted} className="h-full w-full object-cover" />
    : <audio ref={element as React.RefObject<HTMLAudioElement>} autoPlay />;
}

export const CallModal: React.FC<CallModalProps> = ({ call, conversation, onAccept, onReject, onEnd, onMediaError, language }) => {
  const callObject = useRef<DailyCall | null>(null);
  const [isMuted, setIsMuted] = useState(false);
  const [isVideoOff, setIsVideoOff] = useState(false);
  const [callSeconds, setCallSeconds] = useState(0);
  const [remoteVideo, setRemoteVideo] = useState<MediaStreamTrack | null>(null);
  const [remoteAudio, setRemoteAudio] = useState<MediaStreamTrack | null>(null);
  const [localVideo, setLocalVideo] = useState<MediaStreamTrack | null>(null);
  const callId = call?.id;
  const callPhase = call?.phase;

  const onEndRef = useRef(onEnd);
  const onRejectRef = useRef(onReject);

  useEffect(() => {
    onEndRef.current = onEnd;
    onRejectRef.current = onReject;
  }, [onEnd, onReject]);

  useEffect(() => {
    if (call?.phase !== "active") return;
    const interval = window.setInterval(() => setCallSeconds((seconds) => seconds + 1), 1000);
    return () => window.clearInterval(interval);
  }, [call?.phase]);

  useEffect(() => {
    if (!callId || (callPhase !== "outgoing" && callPhase !== "incoming")) return;
    const phase = callPhase;
    const timer = window.setTimeout(() => {
      if (phase === "outgoing") {
        onEndRef.current();
      } else if (phase === "incoming") {
        onRejectRef.current();
      }
    }, DEFAULT_CALL_RING_TIMEOUT_SECONDS * 1000);
    return () => window.clearTimeout(timer);
  }, [callId, callPhase]);

  useEffect(() => {
    if (!call || call.phase !== "active" || !call.roomUrl || !call.joinToken) return;
    let disposed = false;

    const connect = async () => {
      try {
        const { default: Daily } = await import("@daily-co/daily-js");
        if (disposed) return;
        const daily = Daily.createCallObject({ subscribeToTracksAutomatically: true });
        callObject.current = daily;
        daily.on("track-started", ({ participant, track, type }) => {
          if (participant?.local) {
            if (type === "video") setLocalVideo(track);
            return;
          }
          if (type === "video") setRemoteVideo(track);
          if (type === "audio") setRemoteAudio(track);
        });
        daily.on("track-stopped", ({ participant, type }) => {
          if (participant?.local && type === "video") setLocalVideo(null);
          if (!participant?.local && type === "video") setRemoteVideo(null);
          if (!participant?.local && type === "audio") setRemoteAudio(null);
        });
        daily.on("error", () => onMediaError(callText(language).connectionError));
        await daily.join({
          url: call.roomUrl,
          token: call.joinToken,
          startAudioOff: false,
          startVideoOff: call.type === "voice",
        });
        if (disposed) return;
        const localTrack = daily.participants().local?.tracks.video?.persistentTrack;
        if (localTrack) setLocalVideo(localTrack);
      } catch {
        if (!disposed) onMediaError(callText(language).connectionError);
      }
    };

    void connect();
    return () => {
      disposed = true;
      const daily = callObject.current;
      callObject.current = null;
      setRemoteVideo(null);
      setRemoteAudio(null);
      setLocalVideo(null);
      if (daily) void daily.leave().catch(() => undefined).finally(() => daily.destroy());
    };
  }, [call, language, onMediaError]);

  if (!call || !conversation) return null;

  const text = callText(language);
  const formatDuration = (seconds: number) => `${Math.floor(seconds / 60).toString().padStart(2, "0")}:${(seconds % 60).toString().padStart(2, "0")}`;
  const isIncoming = call.phase === "incoming";
  const isActive = call.phase === "active";
  const toggleMuted = () => {
    const nextMuted = !isMuted;
    callObject.current?.setLocalAudio(!nextMuted);
    setIsMuted(nextMuted);
  };
  const toggleVideo = () => {
    const nextVideoOff = !isVideoOff;
    callObject.current?.setLocalVideo(!nextVideoOff);
    setIsVideoOff(nextVideoOff);
  };

  return (
    <div id="call-modal-backdrop" className="fixed inset-0 z-[70] flex items-center justify-center bg-black/80 p-4 backdrop-blur-md">
      <div id="active-call-card" className="relative flex w-full max-w-lg flex-col items-center overflow-hidden rounded-3xl border border-[#2A2E3D] bg-[#1C1F27] p-8 text-center text-white shadow-2xl">
        <div className="relative mb-6 h-48 w-48 overflow-hidden rounded-3xl bg-[#232630] ring-4 ring-[#2563EB]/50">
          {isActive && call.type === "video" && remoteVideo ? <MediaTrack track={remoteVideo} /> : <img src={conversation.avatar} alt={conversation.name} className="h-full w-full object-cover" referrerPolicy="no-referrer" />}
          {isActive && call.type === "video" && localVideo && !isVideoOff && <div className="absolute bottom-3 right-3 h-20 w-28 overflow-hidden rounded-xl border-2 border-white/80 bg-black"><MediaTrack track={localVideo} muted /></div>}
        </div>
        <h3 className="text-xl font-bold">{conversation.name}</h3>
        <p className="mb-7 mt-1 text-sm text-[#9DA3B4]">{isIncoming ? (call.type === "video" ? text.incomingVideo : text.incomingVoice) : isActive ? formatDuration(callSeconds) : text.calling}</p>
        <MediaTrack track={remoteAudio} />
        {isIncoming ? (
          <div className="flex items-center gap-8">
            <button onClick={onReject} className="flex h-16 w-16 items-center justify-center rounded-full bg-rose-600 text-white shadow-lg shadow-rose-600/30" aria-label={text.decline}><PhoneOff className="h-7 w-7" /></button>
            <button onClick={onAccept} className="flex h-16 w-16 items-center justify-center rounded-full bg-emerald-500 text-white shadow-lg shadow-emerald-500/30" aria-label={text.answer}><Phone className="h-7 w-7" /></button>
          </div>
        ) : isActive ? (
          <div className="flex items-center gap-4">
            <button onClick={toggleMuted} className={`rounded-full p-4 ${isMuted ? "bg-rose-500" : "bg-[#2A2E3D] hover:bg-[#34394B]"}`} aria-label={isMuted ? text.microphoneOn : text.microphoneOff}>{isMuted ? <MicOff className="h-6 w-6" /> : <Mic className="h-6 w-6" />}</button>
            {call.type === "video" && <button onClick={toggleVideo} className={`rounded-full p-4 ${isVideoOff ? "bg-rose-500" : "bg-[#2A2E3D] hover:bg-[#34394B]"}`} aria-label={isVideoOff ? text.cameraOn : text.cameraOff}>{isVideoOff ? <VideoOff className="h-6 w-6" /> : <Video className="h-6 w-6" />}</button>}
            <button onClick={onEnd} className="rounded-full bg-rose-600 p-4 shadow-lg shadow-rose-600/30" aria-label={text.end}><PhoneOff className="h-6 w-6" /></button>
          </div>
        ) : <button onClick={onEnd} className="flex items-center gap-2 rounded-full bg-rose-600 px-6 py-3 font-semibold shadow-lg shadow-rose-600/30"><PhoneOff className="h-5 w-5" /> {text.cancel}</button>}
      </div>
    </div>
  );
};
