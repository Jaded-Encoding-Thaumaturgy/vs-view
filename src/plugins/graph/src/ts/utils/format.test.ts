import { describe, expect, it } from 'vite-plus/test';

import { formatFps, formatProcessingTime, formatTiming } from './format';

describe('formatTiming', () => {
  it('formats timing values with automatic scaling or explicit units', () => {
    expect(formatTiming(0)).toBe('');
    expect(formatTiming(-50)).toBe('');
    expect(formatTiming(Number.NaN)).toBe('');

    expect(formatTiming(420)).toBe('420 ns');
    expect(formatTiming(999_999)).toBe('999999 ns');
    expect(formatTiming(1_000_000)).toBe('1.00 ms');
    expect(formatTiming(2_500_000)).toBe('2.50 ms');

    expect(formatTiming(2_500_000, 'ns')).toBe('2500000 ns');
    expect(formatTiming(2_500_000, 'ms')).toBe('2.50 ms');
    expect(formatTiming(420, 'ms')).toBe('0.00 ms');
  });
});

describe('formatFps', () => {
  it('formats fps with zero-denominator handling and trailing zero stripping', () => {
    expect(formatFps(24000, 1001)).toBe('23.976');
    expect(formatFps(24, 1)).toBe('24');
    expect(formatFps(30, 1)).toBe('30');
    expect(formatFps(0, 0)).toBe('0');
    expect(formatFps(24, 0)).toBe('0');
  });
});

describe('formatProcessingTime', () => {
  it('formats processing time for inspector drawer with fallback and unit handling', () => {
    expect(formatProcessingTime(0, 'ns')).toBe('0 ns');
    expect(formatProcessingTime(0, 'ms')).toBe('0.00 ms');
    expect(formatProcessingTime(0, 'auto')).toBe('0.00 ms (0 ns)');

    expect(formatProcessingTime(2_500_000, 'ns')).toBe('2500000 ns');
    expect(formatProcessingTime(2_500_000, 'ms')).toBe('2.50 ms');
    expect(formatProcessingTime(2_500_000, 'auto')).toBe('2.50 ms (2500000 ns)');
  });
});
