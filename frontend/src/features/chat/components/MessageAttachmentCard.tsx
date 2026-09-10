import React, { useEffect, useRef, useState } from 'react';
import { Download, FileText, Image as ImageIcon, LoaderCircle, Mic, Pause, Play } from 'lucide-react';
import { interactionText } from '../i18n';
import type { LanguageCode, MessageAttachment } from '../types';

interface MessageAttachmentCardProps {
  attachment: MessageAttachment;
  language: LanguageCode;
  onDownload: (attachment: MessageAttachment) => void;
  onLoadPreview: (attachment: MessageAttachment) => Promise<string>;
}

const formatPlaybackTime = (seconds: number) => {
  if (!Number.isFinite(seconds) || seconds < 0) return '0:00';
  const wholeSeconds = Math.floor(seconds);
  return `${Math.floor(wholeSeconds / 60)}:${String(wholeSeconds % 60).padStart(2, '0')}`;
};

const VoiceMessagePlayer: React.FC<{ src: string; language: LanguageCode }> = ({ src, language }) => {
  const audioRef = useRef<HTMLAudioElement>(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const progressPercent = duration > 0 ? Math.min((currentTime / duration) * 100, 100) : 0;

  const togglePlayback = () => {
    const audio = audioRef.current;
    if (!audio) return;
    if (audio.paused) {
      void audio.play().then(() => setIsPlaying(true)).catch(() => setIsPlaying(false));
      return;
    }
    audio.pause();
    setIsPlaying(false);
  };

  return (
    <div className="flex h-9 items-center gap-2 px-1">
      <audio
        ref={audioRef}
        className="hidden"
        preload="metadata"
        src={src}
        onLoadedMetadata={(event) => setDuration(event.currentTarget.duration)}
        onDurationChange={(event) => setDuration(event.currentTarget.duration)}
        onTimeUpdate={(event) => setCurrentTime(event.currentTarget.currentTime)}
        onPause={() => setIsPlaying(false)}
        onEnded={() => setIsPlaying(false)}
      />
      <button
        type="button"
        onClick={togglePlayback}
        aria-label={isPlaying ? `${interactionText(language, 'Voice message')}: pause` : `${interactionText(language, 'Voice message')}: play`}
        className="flex h-7 w-7 flex-none items-center justify-center rounded-full bg-[#2563EB] text-white transition-colors hover:bg-[#1D4ED8] dark:bg-[#8AB4F8] dark:text-[#202124]"
      >
        {isPlaying ? <Pause className="h-3.5 w-3.5 fill-current" /> : <Play className="ml-0.5 h-3.5 w-3.5 fill-current" />}
      </button>
      <div className="relative h-3 min-w-0 flex-1">
        <div className="absolute inset-x-0 top-1/2 h-1.5 -translate-y-1/2 overflow-hidden rounded-full bg-[#DDE1EA] dark:bg-[#4A5060]">
          <div className="h-full rounded-full bg-[#2563EB] transition-[width] duration-100 dark:bg-[#8AB4F8]" style={{ width: `${progressPercent}%` }} />
        </div>
        <input
          type="range"
          min="0"
          max={duration || 0}
          value={Math.min(currentTime, duration || 0)}
          step="0.1"
          onChange={(event) => {
            const audio = audioRef.current;
            if (!audio) return;
            const nextTime = Number(event.target.value);
            audio.currentTime = nextTime;
            setCurrentTime(nextTime);
          }}
          aria-label={`${interactionText(language, 'Voice message')}: progress`}
          className="absolute inset-0 h-full w-full cursor-pointer opacity-0"
        />
      </div>
      <span className="w-[4.5rem] flex-none text-right text-[11px] tabular-nums text-[#74798C] dark:text-[#AEB4C4]">
        {formatPlaybackTime(currentTime)} / {duration > 0 ? formatPlaybackTime(duration) : '--:--'}
      </span>
    </div>
  );
};

export const MessageAttachmentCard: React.FC<MessageAttachmentCardProps> = ({
  attachment,
  language,
  onDownload,
  onLoadPreview,
}) => {
  const [previewUrl, setPreviewUrl] = useState<string>();
  const [previewFailed, setPreviewFailed] = useState(false);
  const isImage = attachment.type === 'image';
  const isAudio = attachment.type === 'audio';
  const shouldLoadPreview = isImage || isAudio;

  useEffect(() => {
    if (!shouldLoadPreview) return;
    let active = true;
    let objectUrl: string | undefined;
    setPreviewUrl(undefined);
    setPreviewFailed(false);
    void onLoadPreview(attachment)
      .then((url) => {
        objectUrl = url;
        if (active) setPreviewUrl(url);
        else URL.revokeObjectURL(url);
      })
      .catch(() => {
        if (active) setPreviewFailed(true);
      });
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [attachment, onLoadPreview, shouldLoadPreview]);

  if (isAudio) {
    return (
      <article className="w-[min(18rem,68vw)] rounded-xl bg-white/70 p-1.5 text-[#1E2230] shadow-sm dark:bg-[#232630]/70 dark:text-[#F5F6FA]">
        {previewUrl ? (
          <VoiceMessagePlayer src={previewUrl} language={language} />
        ) : previewFailed ? (
          <div className="flex h-9 items-center justify-center">
            <Mic className="h-5 w-5 text-[#9DA3B4]" />
          </div>
        ) : (
          <div className="flex h-9 items-center justify-center">
            <LoaderCircle className="h-5 w-5 animate-spin text-[#2563EB]" />
          </div>
        )}
      </article>
    );
  }

  return (
    <article className="w-[min(21rem,72vw)] overflow-hidden rounded-xl border border-[#DDE1EA] bg-white text-[#1E2230] shadow-sm dark:border-[#34394A] dark:bg-[#232630] dark:text-[#F5F6FA]">
      {isImage && (
        <button
          type="button"
          onClick={() => onDownload(attachment)}
          aria-label={`${interactionText(language, 'Download file')}: ${attachment.name}`}
          className="relative flex h-48 w-full items-center justify-center overflow-hidden bg-[#F4F5F8] dark:bg-[#1C1F27]"
        >
          {previewUrl ? (
            <img src={previewUrl} alt={attachment.name} className="h-full w-full object-cover transition-transform duration-200 hover:scale-[1.02]" />
          ) : previewFailed ? (
            <ImageIcon className="h-10 w-10 text-[#9DA3B4]" />
          ) : (
            <LoaderCircle className="h-6 w-6 animate-spin text-[#2563EB]" />
          )}
        </button>
      )}

      <button
        type="button"
        onClick={() => onDownload(attachment)}
        aria-label={`${interactionText(language, 'Download file')}: ${attachment.name}`}
        className="group flex w-full items-center gap-3 p-3 text-left hover:bg-[#F7F8FC] dark:hover:bg-[#2A2E3D]"
      >
        <span className={`flex h-10 w-10 flex-none items-center justify-center rounded-lg ${isImage ? 'bg-violet-50 text-violet-600 dark:bg-violet-500/15 dark:text-violet-300' : isAudio ? 'bg-rose-50 text-rose-600 dark:bg-rose-500/15 dark:text-rose-300' : 'bg-[#EFF6FF] text-[#2563EB] dark:bg-[#2563EB]/20 dark:text-[#60A5FA]'}`}>
          {isImage ? <ImageIcon className="h-5 w-5" /> : <FileText className="h-5 w-5" />}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-semibold">
            {attachment.name}
          </span>
          <span className="block text-[11px] text-[#74798C] dark:text-[#9DA3B4]">{attachment.size}</span>
        </span>
        <span className="flex h-8 w-8 flex-none items-center justify-center rounded-lg text-[#74798C] group-hover:bg-[#EFF6FF] group-hover:text-[#2563EB] dark:group-hover:bg-[#2563EB]/20 dark:group-hover:text-[#60A5FA]">
          <Download className="h-4 w-4" />
        </span>
      </button>
    </article>
  );
};
