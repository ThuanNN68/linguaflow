export type VoiceRecorderStage =
  | 'idle'
  | 'requesting_permission'
  | 'recording'
  | 'stopping'
  | 'uploading'
  | 'sending'
  | 'error';

export type VoiceRecorderErrorCode =
  | 'microphone_unavailable'
  | 'microphone_permission_denied'
  | 'unsupported_recording_format'
  | 'recording_failed'
  | 'empty_recording'
  | 'voice_upload_failed'
  | 'voice_send_failed';

export class VoiceRecorderError extends Error {
  constructor(public readonly code: VoiceRecorderErrorCode) {
    super(code);
    this.name = 'VoiceRecorderError';
  }
}

export interface VoiceRecordingFormat {
  mimeType: string;
  extension: string;
}

interface MediaRecorderSupport {
  isTypeSupported(mimeType: string): boolean;
}

const SUPPORTED_EXTENSION_BY_MIME: Readonly<Record<string, string>> = {
  'audio/aac': 'aac',
  'audio/aiff': 'aiff',
  'audio/flac': 'flac',
  'audio/mp3': 'mp3',
  'audio/mp4': 'mp4',
  'audio/mpeg': 'mp3',
  'audio/ogg': 'ogg',
  'audio/wav': 'wav',
  'audio/webm': 'webm',
};

const RECORDING_CANDIDATES = [
  'audio/webm;codecs=opus',
  'audio/mp4;codecs=mp4a.40.2',
  'audio/ogg;codecs=opus',
  'audio/mp4;codecs=opus',
  'audio/webm',
  'audio/mp4',
  'audio/ogg',
  'audio/ogg;codecs=vorbis',
  'audio/aac',
  'audio/wav',
  'audio/mp3',
  'audio/aiff',
  'audio/flac',
] as const;

function normalizedMimeType(mimeType: string): string {
  return mimeType.split(';', 1)[0].trim().toLowerCase();
}

function isSupportedActualMimeType(mimeType: string): boolean {
  const normalized = normalizedMimeType(mimeType);
  if (!SUPPORTED_EXTENSION_BY_MIME[normalized]) return false;
  const compact = mimeType.toLowerCase().replace(/[\s"']/g, '');
  const codec = compact.match(/(?:^|;)codecs=([^;]+)/)?.[1];
  if (!codec) return true;
  if (normalized === 'audio/webm') return codec === 'opus';
  if (normalized === 'audio/ogg') return codec === 'opus' || codec === 'vorbis';
  if (normalized === 'audio/mp4') return codec === 'mp4a.40.2' || codec === 'opus';
  return false;
}

export function selectVoiceRecordingFormat(
  mediaRecorder: MediaRecorderSupport,
): VoiceRecordingFormat | null {
  for (const mimeType of RECORDING_CANDIDATES) {
    if (!mediaRecorder.isTypeSupported(mimeType)) continue;
    return {
      mimeType,
      extension: SUPPORTED_EXTENSION_BY_MIME[normalizedMimeType(mimeType)],
    };
  }
  return null;
}

export function createVoiceRecordingFile(
  chunks: Blob[],
  actualMimeType: string,
  recordedAt = Date.now(),
): File {
  const extension = SUPPORTED_EXTENSION_BY_MIME[normalizedMimeType(actualMimeType)];
  if (!extension || !isSupportedActualMimeType(actualMimeType)) {
    throw new VoiceRecorderError('unsupported_recording_format');
  }

  const blob = new Blob(chunks, { type: actualMimeType });
  if (blob.size === 0) throw new VoiceRecorderError('empty_recording');

  return new File([blob], `voice-${recordedAt}.${extension}`, {
    type: actualMimeType,
    lastModified: recordedAt,
  });
}

export function isMicrophonePermissionError(error: unknown): boolean {
  return error instanceof DOMException
    && (error.name === 'NotAllowedError' || error.name === 'SecurityError');
}

export function formatRecordingDuration(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return `${String(minutes).padStart(2, '0')}:${String(remainder).padStart(2, '0')}`;
}
