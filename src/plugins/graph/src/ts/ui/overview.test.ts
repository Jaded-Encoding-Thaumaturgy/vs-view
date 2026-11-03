import { afterEach, beforeEach, describe, expect, it, vi } from 'vite-plus/test';

import type { CytoscapeCanvas } from '../canvas/cytoscape';
import { makeCanvasNode, makeGraphAnalysis, makeVideoInfo } from '../utils/test-helpers';
import { OverviewPanel } from './overview';

function createMockCanvas() {
  let patternStyles = false;
  return {
    isShowingPatternStyles: vi.fn(() => patternStyles),
    setPatternStyles: vi.fn((enabled: boolean) => {
      patternStyles = enabled;
    }),
    setModeFilter: vi.fn(),
    setMediaOnly: vi.fn(),
    highlightNodeGroup: vi.fn(),
  };
}

describe('OverviewPanel', () => {
  let container: HTMLElement;
  let mockCanvas: ReturnType<typeof createMockCanvas>;
  let onFocusNode: ReturnType<typeof vi.fn<(nodeId: string) => void>>;
  let onPatternsChanged: ReturnType<typeof vi.fn<(enabled: boolean) => void>>;
  let panel: OverviewPanel;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);

    mockCanvas = createMockCanvas();
    onFocusNode = vi.fn<(nodeId: string) => void>();
    onPatternsChanged = vi.fn<(enabled: boolean) => void>();

    panel = new OverviewPanel({
      container,
      canvas: mockCanvas as unknown as CytoscapeCanvas,
      onFocusNode,
      onPatternsChanged,
    });
  });

  afterEach(() => {
    panel.dispose();
    container.remove();
  });

  it('toggles open/close state, renders summary stats, and filters by mode and pattern labels', () => {
    const nodes = [
      makeCanvasNode({
        id: 'n1',
        name: 'Source',
        filter_mode_name: 'PARALLEL',
        processing_time_ns: 1_000_000,
      }),
      makeCanvasNode({
        id: 'n2',
        name: 'StateFilter',
        filter_mode_name: 'FRAME_STATE',
        is_root: true,
        processing_time_ns: 2_000_000,
      }),
    ];
    const analysis = makeGraphAnalysis({
      nodeCount: 2,
      edgeCount: 1,
      depth: 2,
      modes: [
        { name: 'FRAME_STATE', count: 1 },
        { name: 'PARALLEL', count: 1 },
      ],
      patterns: [{ name: 'GENERAL', count: 1 }],
      formats: [{ name: 'yuv420p8', count: 2 }],
    });

    panel.setData(nodes, analysis);
    expect(panel.opened).toBe(false);

    panel.toggle();
    expect(panel.opened).toBe(true);
    expect(container.classList.contains('open')).toBe(true);
    expect(container.textContent).toContain('3.00 ms'); // Total time
    expect(container.textContent).toContain('2.00 ms'); // Root time

    // Click FRAME_STATE mode chip -> filters canvas
    const modeChips = Array.from(
      container.querySelectorAll<HTMLButtonElement>('.overview-chips .overview-chip'),
    );
    const frameStateChip = modeChips.find((btn) => btn.textContent.includes('FRAME_STATE'));
    frameStateChip?.click();
    expect(mockCanvas.setModeFilter).toHaveBeenCalledWith('FRAME_STATE');

    // Click All modes chip -> clears mode filter
    const allModesChip = modeChips.find((btn) => btn.textContent.includes('All modes'));
    allModesChip?.click();
    expect(mockCanvas.setModeFilter).toHaveBeenCalledWith(null);

    // Click pattern labels toggle chip
    const patternBtn = Array.from(
      container.querySelectorAll<HTMLButtonElement>('.overview-chip'),
    ).find((btn) => btn.textContent.includes('Show pattern labels'));
    patternBtn?.click();
    expect(mockCanvas.setPatternStyles).toHaveBeenCalledWith(true);
    expect(onPatternsChanged).toHaveBeenCalledWith(true);

    // Close via header close button
    container.querySelector<HTMLButtonElement>('.overview-close')?.click();
    expect(panel.opened).toBe(false);
  });

  it('renders warning and media transition groups, supports group highlight, chip pagination, and node focus', () => {
    // Create 17 FRAME_STATE nodes to exercise the >15 chip pagination limit
    const stateNodes = Array.from({ length: 17 }, (_, i) => {
      const idx = String(i + 1).padStart(2, '0');
      return makeCanvasNode({
        id: `n_${idx}`,
        name: `Filter_${idx}`,
        filter_mode_name: 'FRAME_STATE',
        media_info: makeVideoInfo({ width: i === 0 ? 1920 : 1280, height: i === 0 ? 1080 : 720 }),
      });
    });
    const nodeIds = stateNodes.map((n) => n.id);
    const analysis = makeGraphAnalysis({
      nodeCount: 17,
      edgeCount: 1,
      depth: 2,
      warningCount: 17,
      warningGroups: [
        {
          code: 'framestate',
          severity: 'warning',
          title: 'Frame-state serialization',
          description:
            'Serializes the entire frame lifecycle to one frame at a time, bypassing parallelism.',
          warnings: stateNodes.map((n) => ({
            code: 'framestate',
            severity: 'warning',
            nodeId: n.id,
          })),
          nodeIds,
        },
      ],
      transitionCount: 1,
      transitionGroups: [
        {
          signature: '1920x1080→1280x720',
          fields: ['resolution'],
          transitions: [
            {
              edgeId: 'e_1',
              sourceId: 'n_01',
              targetId: 'n_02',
              delta: { fields: ['resolution'], label: '1920x1080→1280x720' },
            },
          ],
          targetNodeIds: ['n_02'],
        },
      ],
    });

    panel.setData(stateNodes, analysis);
    panel.open();

    // Media-only toggle button
    const mediaBtn = Array.from(
      container.querySelectorAll<HTMLButtonElement>('.overview-chip'),
    ).find((btn) => btn.textContent.includes('Dim unchanged nodes'));
    mediaBtn?.click();
    expect(mockCanvas.setMediaOnly).toHaveBeenCalledWith(true);

    // Highlight warning group on canvas without opening accordion
    const warnGroup = container.querySelector('.overview-group.severity-warning')!;
    const highlightBtn = warnGroup.querySelector<HTMLButtonElement>('.overview-group-action-btn')!;
    highlightBtn.click();
    expect(mockCanvas.highlightNodeGroup).toHaveBeenCalledWith(nodeIds);
    expect(warnGroup.classList.contains('open')).toBe(false);

    // Expand warning group accordion -> shows 15 chips + '+2 more' button
    warnGroup.querySelector<HTMLElement>('.overview-group-header')?.click();
    expect(warnGroup.classList.contains('open')).toBe(true);
    expect(warnGroup.querySelectorAll('.overview-node-chip')).toHaveLength(15);

    const moreBtn = warnGroup.querySelector<HTMLButtonElement>('.overview-chip-more')!;
    expect(moreBtn.textContent.trim()).toBe('+2 more');
    moreBtn.click();
    expect(warnGroup.querySelectorAll('.overview-node-chip')).toHaveLength(17);

    // Clicking a node chip focuses that node
    warnGroup.querySelector<HTMLButtonElement>('.overview-node-chip')?.click();
    expect(onFocusNode).toHaveBeenCalledWith('n_01');

    // Updating data when the highlighted warning group disappears clears the highlight
    panel.setData(
      [makeCanvasNode({ id: 'clean', filter_mode_name: 'PARALLEL' })],
      makeGraphAnalysis({ nodeCount: 1 }),
    );
    expect(mockCanvas.highlightNodeGroup).toHaveBeenLastCalledWith(null);
  });
});
