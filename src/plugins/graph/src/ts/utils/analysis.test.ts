import { describe, expect, it } from 'vite-plus/test';

import { computeTimingStats } from './analysis';
import { makeNode } from './test-helpers';

describe('computeTimingStats', () => {
  it('aggregates total time, root time, and sorted per-namespace timing stats', () => {
    const nodes = [
      makeNode({ id: 'a', is_root: true, processing_time_ns: 5_000_000 }),
      makeNode({
        id: 'b',
        processing_time_ns: 2_000_000,
        call_stack: [
          {
            level: 0,
            frame_id: 'f1',
            plugin_id: 'com.Resize',
            plugin_ns: 'resize',
            function: 'Spline36',
            arguments: {},
          },
        ],
      }),
      makeNode({
        id: 'c',
        processing_time_ns: 6_000_000,
        call_stack: [
          {
            level: 0,
            frame_id: 'f2',
            plugin_id: 'com.Resize',
            plugin_ns: 'resize',
            function: 'Bicubic',
            arguments: {},
          },
        ],
      }),
    ];

    const stats = computeTimingStats(nodes);

    expect(stats.totalTimeNs).toBe(13_000_000);
    expect(stats.rootTimeNs).toBe(5_000_000);
    expect(stats.namespaces).toEqual([
      { name: 'resize', count: 2, totalTimeNs: 8_000_000 },
      { name: 'std', count: 1, totalTimeNs: 5_000_000 },
    ]);
  });

  it('handles empty node lists and nodes with empty call stacks', () => {
    expect(computeTimingStats([])).toEqual({
      totalTimeNs: 0,
      rootTimeNs: 0,
      namespaces: [],
    });

    const stats = computeTimingStats([
      makeNode({ id: 'mystery', processing_time_ns: 1_000, call_stack: [] }),
    ]);
    expect(stats.namespaces).toEqual([{ name: '', count: 1, totalTimeNs: 1_000 }]);
  });
});
