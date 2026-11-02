interface ColorStop {
  stop: number;
  r: number;
  g: number;
  b: number;
}

const HEATMAP_STOPS: readonly ColorStop[] = [
  { stop: 0.0, r: 30, g: 41, b: 59 }, // Cool Slate (#1e293b) - 0 or baseline
  { stop: 0.25, r: 13, g: 148, b: 136 }, // Teal (#0d9488) - fast filters
  { stop: 0.55, r: 245, g: 158, b: 11 }, // Amber (#f59e0b) - moderate filters
  { stop: 0.8, r: 234, g: 88, b: 12 }, // Orange (#ea580c) - heavy filters
  { stop: 1.0, r: 239, g: 68, b: 68 }, // Bright Red (#ef4444) - bottlenecks
] as const;

export function interpolateHeatmapColor(ratio: number): string {
  const clamped = Math.max(0, Math.min(1, ratio));

  for (let i = 0; i < HEATMAP_STOPS.length - 1; i++) {
    const start = HEATMAP_STOPS[i];
    const end = HEATMAP_STOPS[i + 1];
    if (start && end && clamped <= end.stop) {
      const range = end.stop - start.stop;
      const t = range > 0 ? (clamped - start.stop) / range : 0;
      const r = Math.round(start.r + (end.r - start.r) * t);
      const g = Math.round(start.g + (end.g - start.g) * t);
      const b = Math.round(start.b + (end.b - start.b) * t);
      return `rgb(${r}, ${g}, ${b})`;
    }
  }

  const last = HEATMAP_STOPS[HEATMAP_STOPS.length - 1];
  return last ? `rgb(${last.r}, ${last.g}, ${last.b})` : 'rgb(30, 41, 59)';
}

/** Computes a square-root-scaled, clamped timing ratio for the heatmap. */
export function calculateHeatmapRatio(value: number, maxValue: number): number {
  if (maxValue <= 0 || value <= 0) {
    return 0;
  }
  const normalized = Math.min(Math.max(0, value / maxValue), 1);
  return Math.sqrt(normalized);
}
