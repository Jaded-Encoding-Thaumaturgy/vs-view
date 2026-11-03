import { afterEach, beforeEach, describe, expect, it, vi } from 'vite-plus/test';

import type { CytoscapeCanvas } from '../canvas/cytoscape';
import type { CanvasNodeData } from '../types';
import { makeCanvasNode } from '../utils/test-helpers';
import { DEFAULT_SEARCH_TOOLTIP, HudController } from './hud';

function createMockCanvas(nodes: CanvasNodeData[] = []) {
  let currentNodes = nodes;
  let patternStyles = false;
  return {
    direction: 'TB' as 'TB' | 'LR',
    heatmapActive: false,
    zoomIn: vi.fn(),
    zoomOut: vi.fn(),
    fit: vi.fn(),
    reset: vi.fn(),
    isShowingPatternStyles: vi.fn(() => patternStyles),
    setPatternStyles: vi.fn((enabled: boolean) => {
      patternStyles = enabled;
    }),
    getNodes: vi.fn(() => currentNodes),
    highlightSearchMatches: vi.fn(),
    clearSearch: vi.fn(),
    centerNode: vi.fn(),
    setMockNodes: (next: CanvasNodeData[]) => {
      currentNodes = next;
    },
  };
}

describe('HudController', () => {
  let container: HTMLElement;
  let mockCanvas: ReturnType<typeof createMockCanvas>;
  let onReloadRequested: ReturnType<typeof vi.fn<() => void>>;
  let onOutputSelected: ReturnType<typeof vi.fn<(index: number) => void>>;
  let onNodeMatched: ReturnType<typeof vi.fn<(nodeId: string) => void>>;
  let onPatternsToggled: ReturnType<typeof vi.fn<(enabled: boolean) => void>>;
  let onOverviewRequested: ReturnType<typeof vi.fn<() => void>>;
  let hud: HudController;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);

    mockCanvas = createMockCanvas([
      makeCanvasNode({ id: 'n1', name: 'BlankClip', label: 'BlankClip' }),
      makeCanvasNode({ id: 'n2', name: 'CropFirst', label: 'CropFirst' }),
      makeCanvasNode({ id: 'n3', name: 'CropSecond', label: 'CropSecond' }),
    ]);

    onReloadRequested = vi.fn<() => void>();
    onOutputSelected = vi.fn<(index: number) => void>();
    onNodeMatched = vi.fn<(nodeId: string) => void>();
    onPatternsToggled = vi.fn<(enabled: boolean) => void>();
    onOverviewRequested = vi.fn<() => void>();

    hud = new HudController({
      container,
      canvas: mockCanvas as unknown as CytoscapeCanvas,
      onReloadRequested,
      onOutputSelected,
      onNodeMatched,
      onPatternsToggled,
      onOverviewRequested,
    });
  });

  afterEach(() => {
    hud.dispose();
    container.remove();
  });

  it('renders output selector options and emits selected output index on change', () => {
    const select = container.querySelector<HTMLSelectElement>('#hud-output-select')!;
    expect(select.disabled).toBe(true);
    expect(select.textContent).toContain('No outputs');

    hud.setOutputs([
      { index: 0, name: 'Source', isActive: false },
      { index: 1, name: 'Filtered', isActive: true },
    ]);

    expect(select.disabled).toBe(false);
    expect(select.options).toHaveLength(2);
    expect(select.options[1]?.selected).toBe(true);
    expect(select.options[1]?.textContent.trim()).toBe('[1] Filtered');

    select.value = '0';
    select.dispatchEvent(new Event('change'));
    expect(onOutputSelected).toHaveBeenCalledWith(0);
  });

  it('triggers canvas controls, toggles tool actions, and syncs host options', () => {
    container.querySelector<HTMLButtonElement>('#hud-zoom-in')?.click();
    expect(mockCanvas.zoomIn).toHaveBeenCalledTimes(1);

    container.querySelector<HTMLButtonElement>('#hud-zoom-out')?.click();
    expect(mockCanvas.zoomOut).toHaveBeenCalledTimes(1);

    container.querySelector<HTMLButtonElement>('#hud-fit')?.click();
    expect(mockCanvas.fit).toHaveBeenCalledTimes(1);

    container.querySelector<HTMLButtonElement>('#hud-reset')?.click();
    expect(mockCanvas.reset).toHaveBeenCalledTimes(1);

    const dirBtn = container.querySelector<HTMLButtonElement>('#hud-direction')!;
    expect(dirBtn.textContent.trim()).toBe('↕ TB');
    dirBtn.click();
    expect(mockCanvas.direction).toBe('LR');
    expect(dirBtn.textContent.trim()).toBe('↔ LR');

    const heatmapBtn = container.querySelector<HTMLButtonElement>('#hud-heatmap')!;
    heatmapBtn.click();
    expect(mockCanvas.heatmapActive).toBe(true);
    expect(heatmapBtn.classList.contains('active')).toBe(true);

    const patternsBtn = container.querySelector<HTMLButtonElement>('#hud-patterns')!;
    patternsBtn.click();
    expect(mockCanvas.setPatternStyles).toHaveBeenCalledWith(true);
    expect(onPatternsToggled).toHaveBeenCalledWith(true);
    expect(patternsBtn.classList.contains('active')).toBe(true);

    container.querySelector<HTMLButtonElement>('#hud-overview')?.click();
    expect(onOverviewRequested).toHaveBeenCalledTimes(1);

    container.querySelector<HTMLButtonElement>('#hud-refresh')?.click();
    expect(onReloadRequested).toHaveBeenCalledTimes(1);

    hud.syncOptions({ direction: 'TB', heatmap: false, patterns: false });
    expect(dirBtn.textContent.trim()).toBe('↕ TB');
    expect(heatmapBtn.classList.contains('active')).toBe(false);
    expect(patternsBtn.classList.contains('active')).toBe(false);
  });

  it('performs live search, cycles through matches via buttons/keys, and clears on Escape', () => {
    const input = container.querySelector<HTMLInputElement>('#hud-search-input')!;
    const countEl = container.querySelector<HTMLElement>('#hud-search-count')!;
    const prevBtn = container.querySelector<HTMLButtonElement>('#hud-search-prev')!;
    const nextBtn = container.querySelector<HTMLButtonElement>('#hud-search-next')!;

    expect(prevBtn.disabled).toBe(true);
    expect(nextBtn.disabled).toBe(true);
    expect(input.title).toBe(DEFAULT_SEARCH_TOOLTIP);

    // Search matching 2 nodes ('n2' and 'n3')
    input.value = 'crop';
    input.dispatchEvent(new Event('input'));

    expect(mockCanvas.highlightSearchMatches).toHaveBeenCalledWith(['n2', 'n3']);
    expect(onNodeMatched).toHaveBeenLastCalledWith('n2');
    expect(countEl.textContent.trim()).toBe('1/2');
    expect(prevBtn.disabled).toBe(false);
    expect(nextBtn.disabled).toBe(false);
    expect(input.title).toBe(DEFAULT_SEARCH_TOOLTIP);

    // Cycle next via Enter and previous via Shift+Enter
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
    expect(countEl.textContent.trim()).toBe('2/2');
    expect(onNodeMatched).toHaveBeenLastCalledWith('n3');

    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', shiftKey: true }));
    expect(countEl.textContent.trim()).toBe('1/2');
    expect(onNodeMatched).toHaveBeenLastCalledWith('n2');

    // Cycle wrap-around via prev button
    prevBtn.click();
    expect(countEl.textContent.trim()).toBe('2/2');
    expect(onNodeMatched).toHaveBeenLastCalledWith('n3');

    // Refresh search after graph update preserves current matched node index
    mockCanvas.setMockNodes([
      makeCanvasNode({ id: 'n0', name: 'CropZero', label: 'CropZero' }),
      makeCanvasNode({ id: 'n2', name: 'CropFirst', label: 'CropFirst' }),
      makeCanvasNode({ id: 'n3', name: 'CropSecond', label: 'CropSecond' }),
    ]);
    hud.refreshSearch(false);
    expect(countEl.textContent.trim()).toBe('3/3'); // n3 is now at index 2 (3/3)

    // Invalid regex marks input invalid, displays '!', clears previous canvas search, and updates tooltip to error
    mockCanvas.clearSearch.mockClear();
    onNodeMatched.mockClear();
    input.value = '/unterminated[';
    input.dispatchEvent(new Event('input'));
    expect(input.classList.contains('invalid')).toBe(true);
    expect(countEl.textContent.trim()).toBe('!');
    expect(input.title).toContain('Invalid regular expression');
    expect(nextBtn.disabled).toBe(true);
    expect(mockCanvas.clearSearch).toHaveBeenCalledTimes(1);

    // Pressing Enter on an invalid regex does not cycle stale matches
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
    expect(onNodeMatched).not.toHaveBeenCalled();

    // Escape clears search, resets canvas highlight, and restores default tooltip
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    expect(input.value).toBe('');
    expect(countEl.textContent.trim()).toBe('');
    expect(input.title).toBe(DEFAULT_SEARCH_TOOLTIP);
    expect(mockCanvas.clearSearch).toHaveBeenCalledTimes(2);
  });
});
