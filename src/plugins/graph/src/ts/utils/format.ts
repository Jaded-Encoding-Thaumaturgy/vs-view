import type { TimingUnit } from '../types';

export function formatTiming(nanoseconds: number, unit: TimingUnit = 'auto'): string {
  if (!Number.isFinite(nanoseconds) || nanoseconds <= 0) {
    return '';
  }

  if (unit === 'ns') {
    return `${Math.round(nanoseconds)} ns`;
  }

  const milliseconds = nanoseconds / 1_000_000;
  if (unit === 'ms') {
    return `${milliseconds.toFixed(2)} ms`;
  }

  return nanoseconds < 1_000_000
    ? `${Math.round(nanoseconds)} ns`
    : `${milliseconds.toFixed(2)} ms`;
}

export function formatFps(num: number, den: number): string {
  if (den === 0) return '0';

  return (num / den).toFixed(3).replace(/\.?0+$/, '');
}

export function formatProcessingTime(nanoseconds: number, unit: TimingUnit = 'auto'): string {
  const ns = formatTiming(nanoseconds, 'ns');
  if (unit === 'ns') {
    return ns || '0 ns';
  }

  const ms = formatTiming(nanoseconds, 'ms');
  if (unit === 'ms') {
    return ms || '0.00 ms';
  }

  return ns ? `${ms} (${ns})` : '0.00 ms (0 ns)';
}
