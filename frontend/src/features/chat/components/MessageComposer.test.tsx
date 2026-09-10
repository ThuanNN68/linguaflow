import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MessageComposer } from './MessageComposer';
import { selectVoiceRecordingFormat } from '../voice-recorder';
import type { User } from '../types';

const originalMediaRecorder = Object.getOwnPropertyDescriptor(globalThis, 'MediaRecorder');
const originalMediaDevices = Object.getOwnPropertyDescriptor(navigator, 'mediaDevices');

interface RecorderHarness {
  instances: FakeMediaRecorder[];
  getUserMedia: ReturnType<typeof vi.fn>;
  stopTrack: ReturnType<typeof vi.fn>;
}

let supported: (mimeType: string) => boolean = (mimeType) => mimeType === 'audio/webm;codecs=opus';
let emittedAudio = 'recorded-audio';
let startThrows = false;

class FakeMediaRecorder {
  static isTypeSupported = vi.fn((mimeType: string) => supported(mimeType));
  static instances: FakeMediaRecorder[] = [];

  state: RecordingState = 'inactive';
  readonly mimeType: string;
  ondataavailable: ((event: BlobEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  onstop: ((event: Event) => void) | null = null;

  constructor(_stream: MediaStream, options?: MediaRecorderOptions) {
    this.mimeType = options?.mimeType || '';
    FakeMediaRecorder.instances.push(this);
  }

  start() {
    if (startThrows) throw new Error('recorder start failed');
    this.state = 'recording';
  }

  stop() {
    this.state = 'inactive';
    this.ondataavailable?.({
      data: new Blob(emittedAudio ? [emittedAudio] : [], { type: this.mimeType }),
    } as BlobEvent);
    this.onstop?.(new Event('stop'));
  }
}

function installRecorder(): RecorderHarness {
  const stopTrack = vi.fn();
  const stream = {
    getTracks: () => [{ stop: stopTrack }],
  } as unknown as MediaStream;
  const getUserMedia = vi.fn().mockResolvedValue(stream);
  Object.defineProperty(globalThis, 'MediaRecorder', {
    configurable: true,
    value: FakeMediaRecorder,
  });
  Object.defineProperty(navigator, 'mediaDevices', {
    configurable: true,
    value: { getUserMedia },
  });
  return { instances: FakeMediaRecorder.instances, getUserMedia, stopTrack };
}

function renderComposer(overrides: Partial<React.ComponentProps<typeof MessageComposer>> = {}) {
  const props: React.ComponentProps<typeof MessageComposer> = {
    recipientName: 'Recipient',
    onSendMessage: vi.fn(),
    replyTo: null,
    onCancelReply: vi.fn(),
    onSendAttachment: vi.fn(),
    onSendVoice: vi.fn().mockResolvedValue(undefined),
    onTyping: vi.fn(),
    mentionCandidates: [],
    language: 'en' as const,
    ...overrides,
  };
  return { ...render(<MessageComposer {...props} />), props };
}

beforeEach(() => {
  supported = (mimeType) => mimeType === 'audio/webm;codecs=opus';
  emittedAudio = 'recorded-audio';
  startThrows = false;
  FakeMediaRecorder.instances = [];
  FakeMediaRecorder.isTypeSupported.mockClear();
});

afterEach(() => {
  if (originalMediaRecorder) Object.defineProperty(globalThis, 'MediaRecorder', originalMediaRecorder);
  else Reflect.deleteProperty(globalThis, 'MediaRecorder');
  if (originalMediaDevices) Object.defineProperty(navigator, 'mediaDevices', originalMediaDevices);
  else Reflect.deleteProperty(navigator, 'mediaDevices');
});

describe('MessageComposer voice recorder', () => {
  it('requests audio permission, starts recording, and displays elapsed time', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-08-27T10:00:00Z'));
    const harness = installRecorder();
    renderComposer();

    fireEvent.click(screen.getByRole('button', { name: 'Record voice message' }));
    await act(async () => { await Promise.resolve(); });

    expect(harness.getUserMedia).toHaveBeenCalledWith({ audio: true });
    expect(harness.instances).toHaveLength(1);
    expect(harness.instances[0].state).toBe('recording');
    expect(screen.getByText('Recording…')).toBeDefined();
    expect(screen.getByText('00:00')).toBeDefined();

    act(() => { vi.advanceTimersByTime(1250); });
    expect(screen.getByText('00:01')).toBeDefined();
  });

  it('handles microphone permission denial without leaving the composer disabled', async () => {
    installRecorder();
    const getUserMedia = vi.fn().mockRejectedValue(new DOMException('denied', 'NotAllowedError'));
    Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: { getUserMedia } });
    renderComposer();

    fireEvent.click(screen.getByRole('button', { name: 'Record voice message' }));
    expect((await screen.findByRole('alert')).textContent).toContain('Microphone permission denied');
    expect(screen.getByRole('button', { name: 'Record voice message' })).toBeDefined();
  });

  it('handles an unavailable microphone API before requesting audio', async () => {
    Object.defineProperty(globalThis, 'MediaRecorder', {
      configurable: true,
      value: FakeMediaRecorder,
    });
    Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: undefined });
    renderComposer();

    fireEvent.click(screen.getByRole('button', { name: 'Record voice message' }));

    expect((await screen.findByRole('alert')).textContent).toContain('Microphone unavailable');
  });

  it('stops microphone tracks when MediaRecorder cannot start', async () => {
    startThrows = true;
    const harness = installRecorder();
    renderComposer();

    fireEvent.click(screen.getByRole('button', { name: 'Record voice message' }));

    expect((await screen.findByRole('alert')).textContent).toContain('Recording failed');
    expect(harness.stopTrack).toHaveBeenCalledTimes(1);
  });

  it('cancels without sending and stops every microphone track', async () => {
    const harness = installRecorder();
    const onSendVoice = vi.fn();
    renderComposer({ onSendVoice });

    fireEvent.click(screen.getByRole('button', { name: 'Record voice message' }));
    await screen.findByText('Recording…');
    fireEvent.click(screen.getByRole('button', { name: 'Cancel recording' }));

    await waitFor(() => expect(screen.getByRole('button', { name: 'Record voice message' })).toBeDefined());
    expect(harness.stopTrack).toHaveBeenCalledTimes(1);
    expect(onSendVoice).not.toHaveBeenCalled();
  });

  it('stops tracks and sends a File using the recorder actual MIME/container', async () => {
    supported = (mimeType) => mimeType === 'audio/webm;codecs=opus';
    const harness = installRecorder();
    const onSendVoice = vi.fn(async (
      _file: File,
      _reply: string | undefined,
      onStage: (stage: 'uploading' | 'sending') => void,
    ) => onStage('sending'));
    renderComposer({ onSendVoice });

    fireEvent.click(screen.getByRole('button', { name: 'Record voice message' }));
    await screen.findByText('Recording…');
    fireEvent.click(screen.getByRole('button', { name: 'Send voice message' }));

    await waitFor(() => expect(onSendVoice).toHaveBeenCalledTimes(1));
    const file = onSendVoice.mock.calls[0][0] as File;
    expect(file.type).toBe('audio/webm;codecs=opus');
    expect(file.name).toMatch(/\.webm$/);
    expect(file.size).toBeGreaterThan(0);
    expect(harness.stopTrack).toHaveBeenCalledTimes(1);
  });

  it('fails before permission or upload when no browser-native candidate exists', async () => {
    supported = () => false;
    const harness = installRecorder();
    const onSendVoice = vi.fn();
    renderComposer({ onSendVoice });

    fireEvent.click(screen.getByRole('button', { name: 'Record voice message' }));

    expect((await screen.findByRole('alert')).textContent).toContain('Unsupported recording format');
    expect(harness.getUserMedia).not.toHaveBeenCalled();
    expect(onSendVoice).not.toHaveBeenCalled();
  });

  it('supports the representative Chromium, Firefox, and Safari native families', () => {
    expect(selectVoiceRecordingFormat({
      isTypeSupported: (mimeType) => mimeType === 'audio/webm;codecs=opus',
    })).toEqual({ mimeType: 'audio/webm;codecs=opus', extension: 'webm' });
    expect(selectVoiceRecordingFormat({
      isTypeSupported: (mimeType) => mimeType === 'audio/ogg;codecs=opus',
    })).toEqual({ mimeType: 'audio/ogg;codecs=opus', extension: 'ogg' });
    expect(selectVoiceRecordingFormat({
      isTypeSupported: (mimeType) => mimeType === 'audio/mp4;codecs=mp4a.40.2',
    })).toEqual({ mimeType: 'audio/mp4;codecs=mp4a.40.2', extension: 'mp4' });
  });

  it('rejects an empty recording without uploading it', async () => {
    emittedAudio = '';
    installRecorder();
    const onSendVoice = vi.fn();
    renderComposer({ onSendVoice });

    fireEvent.click(screen.getByRole('button', { name: 'Record voice message' }));
    await screen.findByText('Recording…');
    fireEvent.click(screen.getByRole('button', { name: 'Send voice message' }));

    expect((await screen.findByRole('alert')).textContent).toContain('Empty recording');
    expect(onSendVoice).not.toHaveBeenCalled();
  });

  it('stops microphone tracks when the component unmounts', async () => {
    const harness = installRecorder();
    const view = renderComposer();
    fireEvent.click(screen.getByRole('button', { name: 'Record voice message' }));
    await screen.findByText('Recording…');

    view.unmount();

    expect(harness.stopTrack).toHaveBeenCalledTimes(1);
  });
});

describe('MessageComposer regressions', () => {
  it('inserts the active mention instead of sending when Enter is pressed in the mention menu', () => {
    installRecorder();
    const onSendMessage = vi.fn();
    renderComposer({
      onSendMessage,
      mentionCandidates: [{
        id: 'user-admin', name: 'Admin', username: 'admin', avatar: '/admin.png',
      } as User],
    });

    const input = screen.getByRole('textbox');
    fireEvent.change(input, { target: { value: '@', selectionStart: 1 } });
    fireEvent.keyDown(input, { key: 'Enter' });

    expect((input as HTMLTextAreaElement).value).toBe('@admin ');
    expect(onSendMessage).not.toHaveBeenCalled();
  });

  it('keeps ordinary text send behavior unchanged', () => {
    installRecorder();
    const onSendMessage = vi.fn();
    renderComposer({ onSendMessage });

    fireEvent.change(screen.getByRole('textbox'), { target: { value: '  Normal text  ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }));

    expect(onSendMessage).toHaveBeenCalledWith('Normal text', undefined, []);
  });

  it('shows only calendar identification in the assistant attachment menu', () => {
    installRecorder();
    renderComposer({ assistantMode: true, onCreateAppointment: vi.fn() });

    fireEvent.click(screen.getByRole('button', { name: 'Add attachment' }));

    expect(screen.getByText('Rà soát lịch hẹn')).toBeDefined();
    expect(screen.queryByText('Voice message')).toBeNull();
    expect(screen.queryByText('Document or File')).toBeNull();
  });

  it('sends an assistant request for the selected message-scan window', () => {
    installRecorder();
    const onSendMessage = vi.fn();
    renderComposer({ assistantMode: true, onCreateAppointment: vi.fn(), onSendMessage });

    fireEvent.click(screen.getByRole('button', { name: 'Add attachment' }));
    fireEvent.click(screen.getByText('Rà soát lịch hẹn'));
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Số lượng thời gian cần rà soát' }), { target: { value: '14' } });
    fireEvent.click(screen.getByRole('button', { name: 'Tìm lịch hẹn' }));

    expect(onSendMessage).toHaveBeenCalledWith(
      expect.stringContaining('14 ngày gần đây'),
      undefined,
      [{ type: 'assistant' }],
    );
  });

  it('uses the conversation scan callback when the host provides one', async () => {
    installRecorder();
    const onScanAppointments = vi.fn().mockResolvedValue(undefined);
    renderComposer({ assistantMode: true, onCreateAppointment: vi.fn(), onScanAppointments });

    fireEvent.click(screen.getByRole('button', { name: 'Add attachment' }));
    fireEvent.click(screen.getByText('Rà soát lịch hẹn'));
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Số lượng thời gian cần rà soát' }), { target: { value: '3' } });
    fireEvent.click(screen.getByRole('button', { name: 'Tìm lịch hẹn' }));

    await waitFor(() => expect(onScanAppointments).toHaveBeenCalledWith(3, 'days'));
  });

  it('uses an hourly window when the unit is set to hours', async () => {
    installRecorder();
    const onScanAppointments = vi.fn().mockResolvedValue(undefined);
    renderComposer({ assistantMode: true, onCreateAppointment: vi.fn(), onScanAppointments });

    fireEvent.click(screen.getByRole('button', { name: 'Add attachment' }));
    fireEvent.click(screen.getByText('Rà soát lịch hẹn'));
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Số lượng thời gian cần rà soát' }), { target: { value: '6' } });
    fireEvent.change(screen.getByRole('combobox', { name: 'Đơn vị thời gian' }), { target: { value: 'hours' } });
    fireEvent.click(screen.getByRole('button', { name: 'Tìm lịch hẹn' }));

    await waitFor(() => expect(onScanAppointments).toHaveBeenCalledWith(6, 'hours'));
  });

  it('keeps the existing generic attachment callback unchanged', () => {
    installRecorder();
    const onSendAttachment = vi.fn();
    const view = renderComposer({ onSendAttachment });
    const file = new File(['document'], 'notes.pdf', { type: 'application/pdf' });

    fireEvent.click(screen.getByRole('button', { name: 'Add attachment' }));
    const input = view.container.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file] } });

    expect(onSendAttachment).toHaveBeenCalledWith(file);
  });
});
