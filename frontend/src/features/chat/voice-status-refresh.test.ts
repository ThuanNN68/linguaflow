import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  VOICE_STATUS_REFRESH_DELAYS_MS,
  VoiceStatusRefreshScheduler,
} from './voice-status-refresh';

describe('VoiceStatusRefreshScheduler', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('rehydrates a pending voice through the full bounded retry window', async () => {
    const scheduler = new VoiceStatusRefreshScheduler();
    const refresh = vi.fn().mockResolvedValue(undefined);

    scheduler.schedule('message-1', 'conversation-1', refresh);
    await vi.advanceTimersByTimeAsync(VOICE_STATUS_REFRESH_DELAYS_MS.at(-1)!);

    expect(refresh).toHaveBeenCalledTimes(VOICE_STATUS_REFRESH_DELAYS_MS.length);
    expect(refresh).toHaveBeenLastCalledWith('conversation-1');
  });

  it('cancels remaining refreshes when a terminal event arrives', async () => {
    const scheduler = new VoiceStatusRefreshScheduler();
    const refresh = vi.fn().mockResolvedValue(undefined);

    scheduler.schedule('message-1', 'conversation-1', refresh);
    await vi.advanceTimersByTimeAsync(2_000);
    scheduler.cancel('message-1');
    await vi.runAllTimersAsync();

    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it('restarts the refresh window when the same failed message is retried', async () => {
    const scheduler = new VoiceStatusRefreshScheduler();
    const refresh = vi.fn().mockResolvedValue(undefined);

    scheduler.schedule('message-1', 'old-conversation', refresh);
    scheduler.schedule('message-1', 'conversation-1', refresh);
    await vi.advanceTimersByTimeAsync(2_000);

    expect(refresh).toHaveBeenCalledTimes(1);
    expect(refresh).toHaveBeenCalledWith('conversation-1');
  });

  it('clears every message timer on app-shell unmount', async () => {
    const scheduler = new VoiceStatusRefreshScheduler();
    const refresh = vi.fn().mockResolvedValue(undefined);

    scheduler.schedule('message-1', 'conversation-1', refresh);
    scheduler.schedule('message-2', 'conversation-1', refresh);
    scheduler.clear();
    await vi.runAllTimersAsync();

    expect(refresh).not.toHaveBeenCalled();
  });
});
