import React, { useCallback, useState, useRef, useEffect } from 'react';
import {
  Plus,
  Smile,
  SendHorizontal,
  X,
  Paperclip,
  Reply,
  Mic,
  CalendarPlus,
  CalendarSearch,
  FileText,
} from 'lucide-react';
import { LanguageCode, MessageMention, MessageReply, User } from '../types';
import { assistantName, conversationSummaryText, interactionText, tx } from '../i18n';
import { ASSISTANT_AVATAR_URL } from '../api/chat-api';
import {
  createVoiceRecordingFile,
  formatRecordingDuration,
  isMicrophonePermissionError,
  selectVoiceRecordingFormat,
  VoiceRecorderError,
  type VoiceRecorderErrorCode,
  type VoiceRecorderStage,
} from '../voice-recorder';

interface MessageComposerProps {
  recipientName: string;
  onSendMessage: (text: string, replyToMessageId?: string, mentions?: MessageMention[]) => void;
  replyTo: MessageReply | null;
  onCancelReply: () => void;
  onSendAttachment?: (file: File) => void;
  onSendVoice?: (
    file: File,
    replyToMessageId: string | undefined,
    onStage: (stage: Extract<VoiceRecorderStage, 'uploading' | 'sending'>) => void,
  ) => Promise<void>;
  onTyping?: (isTyping: boolean) => void;
  mentionCandidates: User[];
  language: LanguageCode;
  onCreateAppointment?: () => void;
  onScanAppointments?: (amount: number, unit: 'hours' | 'days' | 'weeks') => Promise<void>;
  onSummarizeConversation?: () => void;
  /** The assistant accepts text requests and calendar review, not media uploads. */
  assistantMode?: boolean;
}

const COMMON_EMOJIS = ['😊', '😂', '👍', '❤️', '🔥', '🎉', '🙌', '✨', '☕', '🍜', '🌍', '👏'];

const VOICE_ERROR_COPY: Record<VoiceRecorderErrorCode, string> = {
  microphone_unavailable: 'Microphone unavailable',
  microphone_permission_denied: 'Microphone permission denied',
  unsupported_recording_format: 'Unsupported recording format',
  recording_failed: 'Recording failed',
  empty_recording: 'Empty recording',
  voice_upload_failed: 'Could not upload voice message',
  voice_send_failed: 'Could not send voice message',
};

export const MessageComposer: React.FC<MessageComposerProps> = ({
  recipientName,
  onSendMessage,
  replyTo,
  onCancelReply,
  onSendAttachment,
  onSendVoice,
  onTyping,
  mentionCandidates,
  language,
  onCreateAppointment,
  onScanAppointments,
  onSummarizeConversation,
  assistantMode = false,
}) => {
  const [text, setText] = useState('');
  const [showEmojiPicker, setShowEmojiPicker] = useState(false);
  const [showAttachMenu, setShowAttachMenu] = useState(false);
  const [showAppointmentScan, setShowAppointmentScan] = useState(false);
  const [scanAmount, setScanAmount] = useState(7);
  const [scanBusy, setScanBusy] = useState(false);
  const [scanError, setScanError] = useState<string | null>(null);
  const [scanUnit, setScanUnit] = useState<'hours' | 'days' | 'weeks'>('days');
  const [activeMention, setActiveMention] = useState(0);
  const [cursorPosition, setCursorPosition] = useState(0);
  const [recorderStage, setRecorderStage] = useState<VoiceRecorderStage>('idle');
  const [recorderError, setRecorderError] = useState<VoiceRecorderErrorCode | null>(null);
  const [recordingSeconds, setRecordingSeconds] = useState(0);

  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const recordingChunksRef = useRef<Blob[]>([]);
  const recordingTimerRef = useRef<number | null>(null);
  const recordingStartedAtRef = useRef(0);
  const discardRecordingRef = useRef(false);
  const requestCancelledRef = useRef(false);
  const mountedRef = useRef(true);

  const clearRecordingTimer = useCallback(() => {
    if (recordingTimerRef.current !== null) {
      window.clearInterval(recordingTimerRef.current);
      recordingTimerRef.current = null;
    }
  }, []);

  const stopMicrophoneTracks = useCallback(() => {
    mediaStreamRef.current?.getTracks().forEach((track) => track.stop());
    mediaStreamRef.current = null;
  }, []);

  const failRecording = useCallback((code: VoiceRecorderErrorCode) => {
    discardRecordingRef.current = true;
    clearRecordingTimer();
    stopMicrophoneTracks();
    mediaRecorderRef.current = null;
    recordingChunksRef.current = [];
    if (!mountedRef.current) return;
    setRecorderError(code);
    setRecorderStage('error');
  }, [clearRecordingTimer, stopMicrophoneTracks]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      requestCancelledRef.current = true;
      discardRecordingRef.current = true;
      clearRecordingTimer();
      const recorder = mediaRecorderRef.current;
      if (recorder && recorder.state !== 'inactive') {
        try {
          recorder.stop();
        } catch {
          // Tracks are still stopped below even if the recorder already failed.
        }
      }
      stopMicrophoneTracks();
    };
  }, [clearRecordingTimer, stopMicrophoneTracks]);

  useEffect(() => {
    if (!recorderError) return;
    const timeout = window.setTimeout(() => {
      setRecorderError(null);
      setRecorderStage('idle');
    }, 3500);
    return () => window.clearTimeout(timeout);
  }, [recorderError]);

  // Auto-resize textarea
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      const scrollHeight = textareaRef.current.scrollHeight;
      textareaRef.current.style.height = `${Math.min(scrollHeight, 130)}px`;
    }
  }, [text]);

  const handleSend = () => {
    if (!text.trim()) return;
    setRecorderError(null);
    const mentions: MessageMention[] = [];
    for (const user of mentionCandidates) {
      if (new RegExp(`(^|\\s)@${user.username.replace(/[.*+?^${}()|[\\]\\]/g, '\\$&')}(?=\\s|$)`, 'i').test(text)) mentions.push({ type: 'user', userId: user.id });
    }
    if (/(^|\s)@assistant(?=\s|$)/i.test(text)) mentions.push({ type: 'assistant' });
    onSendMessage(text.trim(), replyTo?.id, mentions);
    setText('');
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
    }
  };

  const requestAppointmentScan = async () => {
    if (scanBusy) return;
    if (onScanAppointments) {
      setScanBusy(true);
      setScanError(null);
      try {
        await onScanAppointments(scanAmount, scanUnit);
        setShowAppointmentScan(false);
        setShowAttachMenu(false);
      } catch (error) {
        setScanError(error instanceof Error ? error.message : 'Không tìm được lịch hẹn. Vui lòng thử lại.');
      } finally {
        setScanBusy(false);
      }
      return;
    }
    const unitLabel = scanUnit === 'hours' ? 'giờ' : scanUnit === 'weeks' ? 'tuần' : 'ngày';
    const scanLabel = `${scanAmount} ${unitLabel}`;
    onSendMessage(
      `@assistant Hãy quét các tin nhắn trong ${scanLabel} gần đây để xác định các lịch hẹn hoặc cuộc họp có thể cần tạo. Hãy tạo từng đề xuất riêng để tôi xem xét và duyệt.`,
      replyTo?.id,
      [{ type: "assistant" }],
    );
    setShowAppointmentScan(false);
    setShowAttachMenu(false);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (mentionOptions.length && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
      e.preventDefault();
      setActiveMention((current) => (current + (e.key === 'ArrowDown' ? 1 : mentionOptions.length - 1)) % mentionOptions.length);
      return;
    }
    if (mentionOptions.length && (e.key === 'Tab' || (e.key === 'Enter' && !e.shiftKey))) {
      e.preventDefault();
      insertMention(mentionOptions[activeMention]);
      return;
    }
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const mentionMatch = text.slice(0, cursorPosition).match(/(^|\s)@([^\s@]*)$/);
  const mentionQuery = mentionMatch?.[2].toLocaleLowerCase() ?? '';
  const mentionOptions = mentionMatch ? [
    ...mentionCandidates.filter((user) => `${user.name} ${user.username}`.toLocaleLowerCase().includes(mentionQuery)).map((user) => ({ type: 'user' as const, user })),
    ...('trợ lý thông minh assistant ai'.includes(mentionQuery) ? [{ type: 'assistant' as const }] : []),
  ] : [];
  const insertMention = (option: typeof mentionOptions[number]) => {
    const cursor = textareaRef.current?.selectionStart ?? text.length;
    const before = text.slice(0, cursor);
    const start = before.lastIndexOf('@');
    const token = option.type === 'assistant' ? '@assistant ' : `@${option.user.username} `;
    setText(`${text.slice(0, start)}${token}${text.slice(cursor)}`);
    setCursorPosition(start + token.length);
    setActiveMention(0);
    requestAnimationFrame(() => textareaRef.current?.focus());
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file && onSendAttachment) {
      setRecorderError(null);
      onSendAttachment(file);
      setShowAttachMenu(false);
    }
    e.target.value = '';
  };

  const startRecording = async () => {
    if (!onSendVoice || recorderStage !== 'idle' && recorderStage !== 'error') return;
    const MediaRecorderConstructor = globalThis.MediaRecorder;
    if (!navigator.mediaDevices?.getUserMedia || !MediaRecorderConstructor?.isTypeSupported) {
      failRecording('microphone_unavailable');
      return;
    }

    const format = selectVoiceRecordingFormat(MediaRecorderConstructor);
    if (!format) {
      failRecording('unsupported_recording_format');
      return;
    }

    setRecorderError(null);
    setShowAttachMenu(false);
    setShowEmojiPicker(false);
    setRecorderStage('requesting_permission');
    requestCancelledRef.current = false;
    discardRecordingRef.current = false;
    onTyping?.(false);

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (requestCancelledRef.current || !mountedRef.current) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }

      mediaStreamRef.current = stream;
      recordingChunksRef.current = [];
      const recorder = new MediaRecorderConstructor(stream, { mimeType: format.mimeType });
      mediaRecorderRef.current = recorder;

      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) recordingChunksRef.current.push(event.data);
      };
      recorder.onerror = () => failRecording('recording_failed');
      recorder.onstop = () => {
        clearRecordingTimer();
        stopMicrophoneTracks();
        mediaRecorderRef.current = null;
        const chunks = recordingChunksRef.current;
        recordingChunksRef.current = [];

        if (discardRecordingRef.current || !mountedRef.current) {
          if (mountedRef.current) setRecorderStage('idle');
          return;
        }

        let file: File;
        try {
          file = createVoiceRecordingFile(chunks, recorder.mimeType);
        } catch (error) {
          failRecording(
            error instanceof VoiceRecorderError
              ? error.code
              : 'recording_failed',
          );
          return;
        }

        void (async () => {
          try {
            setRecorderStage('uploading');
            await onSendVoice(file, replyTo?.id, (stage) => {
              if (mountedRef.current) setRecorderStage(stage);
            });
            if (mountedRef.current) setRecorderStage('idle');
          } catch (error) {
            failRecording(
              error instanceof VoiceRecorderError
                ? error.code
                : 'voice_send_failed',
            );
          }
        })();
      };

      recorder.start();
      recordingStartedAtRef.current = Date.now();
      setRecordingSeconds(0);
      setRecorderStage('recording');
      recordingTimerRef.current = window.setInterval(() => {
        setRecordingSeconds(Math.floor((Date.now() - recordingStartedAtRef.current) / 1000));
      }, 250);
    } catch (error) {
      if (requestCancelledRef.current || !mountedRef.current) return;
      failRecording(
        isMicrophonePermissionError(error)
          ? 'microphone_permission_denied'
          : 'recording_failed',
      );
    }
  };

  const stopRecording = (discard: boolean) => {
    if (recorderStage === 'requesting_permission') {
      requestCancelledRef.current = true;
      setRecorderStage('idle');
      return;
    }

    const recorder = mediaRecorderRef.current;
    if (!recorder || recorder.state === 'inactive') {
      failRecording('recording_failed');
      return;
    }

    discardRecordingRef.current = discard;
    setRecorderStage('stopping');
    clearRecordingTimer();
    try {
      recorder.stop();
    } catch {
      failRecording('recording_failed');
      return;
    }
    stopMicrophoneTracks();
  };

  const isRecordingFlowActive = recorderStage !== 'idle' && recorderStage !== 'error';
  const recordingStatus = recorderStage === 'requesting_permission'
    ? interactionText(language, 'Requesting microphone permission…')
    : recorderStage === 'recording'
      ? interactionText(language, 'Recording…')
      : recorderStage === 'uploading'
        ? interactionText(language, 'Uploading voice message…')
        : recorderStage === 'sending'
          ? interactionText(language, 'Sending voice message…')
          : interactionText(language, 'Stopping recording…');

  return (
    <div
      id="message-composer-container"
      className="relative w-full px-4 sm:px-6 md:px-8 pb-4 pt-1 flex-shrink-0 transition-colors"
    >
      {/* Reply Banner */}
      {replyTo && (
        <div className="flex items-center justify-between px-4 py-2 mb-2 bg-[#EFF6FF] dark:bg-[#2563EB]/15 border-l-4 border-[#2563EB] rounded-r-xl text-xs">
          <div className="flex items-center gap-2 min-w-0">
            <Reply className="w-3.5 h-3.5 text-[#2563EB] flex-shrink-0" />
            <div className="min-w-0">
              <span className="font-semibold text-[#2563EB] block truncate">
                {interactionText(language, 'Replying to')} {replyTo.senderName}
              </span>
              <span className="text-[#74798C] dark:text-[#9DA3B4] truncate block">
                {replyTo.content}
              </span>
            </div>
          </div>
          <button
            onClick={onCancelReply}
            className="p-1 rounded-lg text-[#74798C] hover:text-[#1E2230] dark:hover:text-white"
            aria-label="Cancel reply"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      )}

      {/* Main Composer Box */}
      <div
        className={`relative flex items-end gap-2 p-2 sm:p-2.5 bg-white dark:bg-[#1C1F27] border rounded-2xl shadow-sm transition-all ${
          isRecordingFlowActive
            ? 'border-rose-300 dark:border-rose-500/40'
            : text.trim()
            ? 'border-[#2563EB]/40 shadow-[#2563EB]/5'
            : 'border-[#E8EAF0] dark:border-[#2A2E3D]'
        }`}
      >
        {isRecordingFlowActive ? (
          <div className="flex min-h-9 flex-1 items-center gap-3 px-1" aria-live="polite">
            <span className={`h-2.5 w-2.5 flex-none rounded-full bg-rose-500 ${recorderStage === 'recording' ? 'animate-pulse' : ''}`} />
            <span className="min-w-0 flex-1 truncate text-sm font-semibold text-[#4E5568] dark:text-[#E2E5F0]">
              {recordingStatus}
            </span>
            {recorderStage === 'recording' && (
              <span className="font-mono text-xs font-semibold tabular-nums text-rose-600 dark:text-rose-300">
                {formatRecordingDuration(recordingSeconds)}
              </span>
            )}
            <button
              type="button"
              onClick={() => stopRecording(true)}
              disabled={recorderStage === 'stopping' || recorderStage === 'uploading' || recorderStage === 'sending'}
              aria-label={interactionText(language, 'Cancel recording')}
              title={interactionText(language, 'Cancel recording')}
              className="flex h-9 w-9 items-center justify-center rounded-xl text-[#74798C] transition-colors hover:bg-[#F4F5F8] hover:text-rose-600 disabled:cursor-not-allowed disabled:opacity-40 dark:hover:bg-[#2A2E3D]"
            >
              <X className="h-4 w-4" />
            </button>
            <button
              type="button"
              onClick={() => stopRecording(false)}
              disabled={recorderStage !== 'recording'}
              aria-label={interactionText(language, 'Send voice message')}
              title={interactionText(language, 'Send voice message')}
              className="flex h-9 w-9 items-center justify-center rounded-xl bg-[#2563EB] text-white shadow-md shadow-[#2563EB]/20 transition-all hover:bg-[#1D4ED8] disabled:cursor-not-allowed disabled:opacity-40"
            >
              <SendHorizontal className="h-4 w-4" />
            </button>
          </div>
        ) : <>
        {/* Attachment Button */}
        <div className="relative">
          <button
            id="composer-attach-btn"
            onClick={() => setShowAttachMenu(!showAttachMenu)}
            aria-label="Add attachment"
            className="flex items-center justify-center w-9 h-9 rounded-xl text-[#74798C] dark:text-[#9DA3B4] hover:text-[#2563EB] hover:bg-[#EFF6FF] dark:hover:bg-[#2563EB]/15 transition-colors"
          >
            <Plus className="w-5 h-5" />
          </button>

          {/* Attachment Popup Menu */}
          {showAttachMenu && (
            <div className="absolute bottom-full left-0 mb-2 w-44 bg-white dark:bg-[#232630] border border-[#E8EAF0] dark:border-[#2A2E3D] rounded-xl shadow-xl p-1 z-30 animate-in fade-in zoom-in-95 duration-100">
              {!assistantMode && <>
                <button
                  type="button"
                  onClick={() => void startRecording()}
                  disabled={!onSendVoice}
                  aria-label={interactionText(language, 'Record voice message')}
                  className="w-full flex items-center gap-2.5 px-3 py-2 text-xs text-[#1E2230] dark:text-[#E2E5F0] rounded-lg hover:bg-[#F7F8FC] dark:hover:bg-[#2A2E3D] text-left disabled:cursor-not-allowed disabled:opacity-45"
                >
                  <Mic className="w-4 h-4 text-rose-500" />
                  <span>{interactionText(language, 'Voice message')}</span>
                </button>
                <button
                  onClick={() => fileInputRef.current?.click()}
                  className="w-full flex items-center gap-2.5 px-3 py-2 text-xs text-[#1E2230] dark:text-[#E2E5F0] rounded-lg hover:bg-[#F7F8FC] dark:hover:bg-[#2A2E3D] text-left"
                >
                  <Paperclip className="w-4 h-4 text-blue-500" />
                  <span>{interactionText(language, 'Document or File')}</span>
                </button>
              </>}
              {onCreateAppointment && (
                <button
                  type="button"
                  onClick={() => {
                    if (assistantMode) {
                      setShowAttachMenu(false);
                      setShowAppointmentScan(true);
                    } else {
                      setShowAttachMenu(false);
                      onCreateAppointment();
                    }
                  }}
                  className="w-full flex items-center gap-2.5 px-3 py-2 text-xs text-[#1E2230] dark:text-[#E2E5F0] rounded-lg hover:bg-[#F7F8FC] dark:hover:bg-[#2A2E3D] text-left"
                >
                  <CalendarPlus className="w-4 h-4 text-emerald-500" />
                  <span>{assistantMode ? 'Rà soát lịch hẹn' : tx(language, 'Create reminder')}</span>
                </button>
              )}
              {!assistantMode && onScanAppointments && (
                <button
                  type="button"
                  onClick={() => { setShowAttachMenu(false); setShowAppointmentScan(true); }}
                  className="w-full flex items-center gap-2.5 px-3 py-2 text-xs text-[#1E2230] dark:text-[#E2E5F0] rounded-lg hover:bg-[#F7F8FC] dark:hover:bg-[#2A2E3D] text-left"
                >
                  <CalendarSearch className="w-4 h-4 text-emerald-500" />
                  <span>{tx(language, 'Find appointments')}</span>
                </button>
              )}
              {!assistantMode && onSummarizeConversation && <button type="button" onClick={() => { setShowAttachMenu(false); onSummarizeConversation(); }} className="w-full flex items-center gap-2.5 px-3 py-2 text-xs text-[#1E2230] dark:text-[#E2E5F0] rounded-lg hover:bg-[#F7F8FC] dark:hover:bg-[#2A2E3D] text-left"><FileText className="w-4 h-4 text-violet-500" /><span>{conversationSummaryText(language).label}</span></button>}
              {!assistantMode && <input
                  ref={fileInputRef}
                  type="file"
                  className="hidden"
                  onChange={handleFileChange}
                />}
            </div>
          )}
        </div>

        {showAppointmentScan && (
          <div className="absolute bottom-full left-0 z-30 mb-2 w-64 max-w-[calc(100vw-2rem)] overflow-hidden rounded-xl border border-[#D8E5FF] bg-white shadow-[0_10px_22px_rgba(37,99,235,0.12)] dark:border-[#36588D] dark:bg-[#232630]">
            <div className="bg-gradient-to-b from-[#F6FAFF] to-white px-3 pb-2 pt-3 dark:from-[#202B3E] dark:to-[#232630]">
              <div className="flex items-start gap-2">
                <span className="grid h-7 w-7 flex-none place-items-center rounded-md bg-[#E8F1FF] text-[#2563EB] dark:bg-[#2563EB]/20 dark:text-[#93C5FD]"><CalendarSearch className="h-3.5 w-3.5" /></span>
                <div className="min-w-0">
                  <p className="text-xs font-bold leading-4 text-[#1E2230] dark:text-[#F5F6FA]">Rà soát lịch hẹn</p>
                  <p className="text-[10px] leading-3.5 text-[#74798C] dark:text-[#9DA3B4]">Tìm trong tối đa 100 tin nhắn mới nhất của chat này trong khoảng đã chọn.</p>
                </div>
              </div>
            </div>
            <div className="px-3 pb-3">
              <label htmlFor="appointment-scan-amount" className="mb-1 block text-[11px] font-semibold text-[#4B556B] dark:text-[#D5D9E4]">Chọn thời gian</label>
              <div className="flex h-9 w-fit overflow-hidden rounded-lg border border-[#D6DFEE] bg-[#FBFCFE] transition-colors focus-within:border-[#2563EB] focus-within:ring-2 focus-within:ring-[#2563EB]/15 dark:border-[#3A3F50] dark:bg-[#1B1D25]">
                  <input
                    id="appointment-scan-amount"
                    type="number"
                    min="1"
                    max={scanUnit === 'hours' ? 720 : scanUnit === 'weeks' ? 4 : 30}
                    value={scanAmount}
                    onChange={(event) => {
                      const maximum = scanUnit === 'hours' ? 720 : scanUnit === 'weeks' ? 4 : 30;
                      setScanAmount(Math.min(maximum, Math.max(1, Number(event.target.value)) || 1));
                    }}
                    aria-label="Số lượng thời gian cần rà soát"
                    className="w-28 flex-none bg-transparent px-3 text-sm font-bold text-[#1E2230] outline-none [color-scheme:light] [&::-webkit-inner-spin-button]:opacity-45 dark:[color-scheme:dark] dark:text-[#F5F6FA]"
                  />
                  <select
                    value={scanUnit}
                    onChange={(event) => {
                      const unit = event.target.value as 'hours' | 'days' | 'weeks';
                      const maximum = unit === 'hours' ? 720 : unit === 'weeks' ? 4 : 30;
                      setScanUnit(unit);
                      setScanAmount((current) => Math.min(current, maximum));
                    }}
                    aria-label="Đơn vị thời gian"
                    className="w-[4.25rem] border-l border-[#E4EAF4] bg-transparent px-1.5 text-xs font-semibold text-[#4B556B] outline-none dark:border-[#3A3F50] dark:text-[#D5D9E4]"
                  >
                    <option value="hours">Giờ</option>
                    <option value="days">Ngày</option>
                    <option value="weeks">Tuần</option>
                  </select>
                </div>
            </div>
            <div className="flex justify-end gap-1 border-t border-[#E8EDF5] bg-[#FCFDFF] px-3 py-2 dark:border-[#363B49] dark:bg-[#20232D]">
              <button type="button" onClick={() => setShowAppointmentScan(false)} className="rounded-md px-2 py-1.5 text-xs font-semibold text-[#74798C] hover:bg-[#F1F4F9] dark:hover:bg-[#2A2E3D]">Huỷ</button>
              <button type="button" disabled={scanBusy} onClick={() => void requestAppointmentScan()} className="rounded-md bg-[#2563EB] px-2.5 py-1.5 text-xs font-bold text-white shadow-sm shadow-[#2563EB]/25 hover:bg-[#1D4ED8] disabled:opacity-50">{scanBusy ? 'Đang tìm lịch…' : 'Tìm lịch hẹn'}</button>
            </div>
            {scanError && <p role="alert" className="px-3 pb-3 text-xs text-red-600 dark:text-red-300">{scanError}</p>}
          </div>
        )}

        {/* Text Input Textarea */}
        <div className="flex-1 min-w-0 py-1">
          <textarea
            ref={textareaRef}
            id="message-input-textarea"
            rows={1}
            value={text}
            onChange={(e) => {
              setText(e.target.value);
              setActiveMention(0);
              setCursorPosition(e.target.selectionStart);
              onTyping?.(e.target.value.trim().length > 0);
            }}
            onSelect={(event) => setCursorPosition(event.currentTarget.selectionStart)}
            onClick={(event) => setCursorPosition(event.currentTarget.selectionStart)}
            onBlur={() => onTyping?.(false)}
            onKeyDown={handleKeyDown}
            placeholder={`${interactionText(language, 'Message')} ${recipientName}...`}
            className="w-full bg-transparent resize-none text-sm text-[#1E2230] dark:text-[#F5F6FA] placeholder-[#8A8F9E] dark:placeholder-[#74798C] focus:outline-none max-h-32 py-0.5 px-1 leading-relaxed"
          />
          {mentionOptions.length > 0 && (
            <div className="absolute bottom-full left-0 mb-3 w-72 overflow-hidden rounded-xl border border-[#E8EAF0] bg-white p-1 shadow-xl dark:border-[#2A2E3D] dark:bg-[#232630] z-40">
              <p className="px-2.5 py-1.5 text-[11px] font-medium text-[#74798C] dark:text-[#9DA3B4]">Gợi ý tag</p>
              {mentionOptions.map((option, index) => option.type === 'assistant' ? (
                <button key="assistant" type="button" onMouseDown={(event) => event.preventDefault()} onClick={() => insertMention(option)} className={`flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-xs ${index === activeMention ? 'bg-[#EFF6FF] text-[#2563EB] dark:bg-[#2563EB]/20' : 'hover:bg-[#F7F8FC] dark:hover:bg-[#2A2E3D]'}`}><img src={ASSISTANT_AVATAR_URL} alt={assistantName(language)} className="h-7 w-7 rounded-full object-cover ring-1 ring-violet-200 dark:ring-violet-400/30" referrerPolicy="no-referrer" /><span><strong className="block text-[#2563EB] dark:text-[#60A5FA]">{assistantName(language)}</strong><span className="text-[10px] text-[#74798C] dark:text-[#9DA3B4]">Phản hồi ngay trong luồng</span></span></button>
              ) : (
                <button key={option.user.id} type="button" onMouseDown={(event) => event.preventDefault()} onClick={() => insertMention(option)} className={`flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-xs ${index === activeMention ? 'bg-[#EFF6FF] text-[#2563EB] dark:bg-[#2563EB]/20' : 'hover:bg-[#F7F8FC] dark:hover:bg-[#2A2E3D]'}`}><img src={option.user.avatar} alt="" className="h-7 w-7 rounded-full" /><span><strong className="block text-[#2563EB] dark:text-[#60A5FA]">{option.user.name}</strong><span className="text-[10px] text-[#74798C] dark:text-[#9DA3B4]">@{option.user.username}</span></span></button>
              ))}
            </div>
          )}
        </div>

        {/* Right Action Icons: Emoji and Send */}
        <div className="flex items-center gap-1 sm:gap-1.5 flex-shrink-0">
          {/* Emoji Picker Button */}
          <div className="relative">
            <button
              id="composer-emoji-btn"
              onClick={() => setShowEmojiPicker(!showEmojiPicker)}
              aria-label="Add emoji"
              className="flex items-center justify-center w-9 h-9 rounded-xl text-[#74798C] dark:text-[#9DA3B4] hover:text-[#1E2230] dark:hover:text-white hover:bg-[#F7F8FC] dark:hover:bg-[#232630] transition-colors"
            >
              <Smile className="w-5 h-5" />
            </button>

            {/* Quick Emoji Menu */}
            {showEmojiPicker && (
              <div className="absolute bottom-full right-0 mb-2 w-64 p-2 bg-white dark:bg-[#232630] border border-[#E8EAF0] dark:border-[#2A2E3D] rounded-2xl shadow-xl z-30 grid grid-cols-6 gap-1">
                {COMMON_EMOJIS.map((emoji) => (
                  <button
                    key={emoji}
                    onClick={() => {
                      setText((prev) => prev + emoji);
                      setShowEmojiPicker(false);
                    }}
                    className="w-9 h-9 flex items-center justify-center text-lg hover:scale-125 hover:bg-[#F7F8FC] dark:hover:bg-[#1C1F27] rounded-xl transition-all"
                  >
                    {emoji}
                  </button>
                ))}
              </div>
            )}
          </div>

          {onSendVoice && (
            <button
              type="button"
              onClick={() => void startRecording()}
              aria-label={interactionText(language, 'Record voice message')}
              title={interactionText(language, 'Record voice message')}
              className="flex h-9 w-9 items-center justify-center rounded-xl text-[#74798C] transition-colors hover:bg-rose-50 hover:text-rose-600 dark:text-[#9DA3B4] dark:hover:bg-rose-500/10 dark:hover:text-rose-300"
            >
              <Mic className="h-5 w-5" />
            </button>
          )}

          {/* Send Button */}
          <button
            id="composer-send-btn"
            onClick={handleSend}
            disabled={!text.trim()}
            aria-label="Send message"
            className={`flex items-center justify-center w-9 h-9 rounded-xl transition-all ${
              text.trim()
                ? 'bg-[#2563EB] text-white hover:bg-[#1D4ED8] shadow-md shadow-[#2563EB]/20 hover:scale-105 active:scale-95'
                : 'bg-[#F4F5F8] dark:bg-[#232630] text-[#8A8F9E] dark:text-[#74798C] cursor-not-allowed opacity-60'
            }`}
          >
            <SendHorizontal className="w-4 h-4" />
          </button>
        </div>
        </>}
      </div>
      {recorderError && (
        <div className="fixed bottom-5 right-5 z-[70] flex max-w-sm items-center justify-between gap-3 rounded-xl border border-rose-200 bg-white px-3.5 py-3 text-xs font-medium text-rose-700 shadow-xl shadow-slate-900/10 dark:border-rose-500/25 dark:bg-[#232630] dark:text-rose-200" role="alert">
          <span>{interactionText(language, VOICE_ERROR_COPY[recorderError])}</span>
          <button
            type="button"
            onClick={() => { setRecorderError(null); setRecorderStage('idle'); }}
            aria-label={interactionText(language, 'Dismiss error')}
            title={interactionText(language, 'Dismiss error')}
            className="rounded p-1 hover:bg-rose-100 dark:hover:bg-rose-500/20"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      )}
    </div>
  );
};
