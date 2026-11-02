import { describe, expect, it } from 'vite-plus/test';

import { calculateHeatmapRatio, interpolateHeatmapColor } from './heatmap';

describe('heatmap scaling and color interpolation', () => {
  it('computes square-root heatmap ratio with non-positive clamping and maximum ceiling', () => {
    expect(calculateHeatmapRatio(0, 1000)).toBe(0);
    expect(calculateHeatmapRatio(-10, 1000)).toBe(0);
    expect(calculateHeatmapRatio(100, 0)).toBe(0);

    expect(calculateHeatmapRatio(25, 100)).toBeCloseTo(0.5, 5);
    expect(calculateHeatmapRatio(4, 100)).toBeCloseTo(0.2, 5);
    expect(calculateHeatmapRatio(100, 100)).toBeCloseTo(1.0, 5);
    expect(calculateHeatmapRatio(200, 100)).toBe(1.0);
  });

  it('interpolates heatmap colors across cool-to-warm stops, clamping out-of-range ratios', () => {
    expect(interpolateHeatmapColor(0)).toBe('rgb(30, 41, 59)');
    expect(interpolateHeatmapColor(0.25)).toBe('rgb(13, 148, 136)');
    expect(interpolateHeatmapColor(0.55)).toBe('rgb(245, 158, 11)');
    expect(interpolateHeatmapColor(0.8)).toBe('rgb(234, 88, 12)');
    expect(interpolateHeatmapColor(1.0)).toBe('rgb(239, 68, 68)');

    expect(interpolateHeatmapColor(-0.5)).toBe('rgb(30, 41, 59)');
    expect(interpolateHeatmapColor(1.5)).toBe('rgb(239, 68, 68)');
  });
});
