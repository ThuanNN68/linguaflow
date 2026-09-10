export const VOICE_STATUS_REFRESH_DELAYS_MS = [
  2_000,
  8_000,
  20_000,
  65_000,
  130_000,
  195_000,
] as const;

type RefreshVoiceStatus = (conversationId: string) => Promise<void>;

export class VoiceStatusRefreshScheduler {
  private readonly timersByMessageId = new Map<string, Set<number>>();

  schedule(
    messageId: string,
    conversationId: string,
    refresh: RefreshVoiceStatus,
  ): void {
    this.cancel(messageId);
    const timers = new Set<number>();
    this.timersByMessageId.set(messageId, timers);

    for (const delayMilliseconds of VOICE_STATUS_REFRESH_DELAYS_MS) {
      const timer = window.setTimeout(() => {
        timers.delete(timer);
        if (timers.size === 0) this.timersByMessageId.delete(messageId);
        void refresh(conversationId).catch(() => {
          // A later bounded refresh or WebSocket reconnect can still recover
          // the durable state after a transient history request failure.
        });
      }, delayMilliseconds);
      timers.add(timer);
    }
  }

  cancel(messageId: string): void {
    const timers = this.timersByMessageId.get(messageId);
    if (!timers) return;
    for (const timer of timers) window.clearTimeout(timer);
    this.timersByMessageId.delete(messageId);
  }

  clear(): void {
    for (const messageId of this.timersByMessageId.keys()) this.cancel(messageId);
  }
}
