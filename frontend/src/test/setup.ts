import { cleanup } from '@testing-library/react';
import { afterEach, vi } from 'vitest';

if (!URL.createObjectURL) {
  Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: vi.fn() });
}
if (!URL.revokeObjectURL) {
  Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() });
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.useRealTimers();
});
