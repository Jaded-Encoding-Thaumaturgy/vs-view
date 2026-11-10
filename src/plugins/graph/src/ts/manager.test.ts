import type cytoscape from 'cytoscape';
import { type MockInstance, afterEach, beforeEach, describe, expect, it, vi } from 'vite-plus/test';

import { CytoscapeCanvas } from './canvas/cytoscape';
import { GraphManager, type GraphManagerOptions } from './manager';
import type { CytoscapePayload, PyToJsCommandMap } from './types';
import { makeEdge, makeGraphAnalysis, makeNode } from './utils/test-helpers';

interface CytoscapeModule {
  default: typeof cytoscape;
}

vi.mock('cytoscape', async (importOriginal) => {
  const actual = await importOriginal<CytoscapeModule>();
  const wrapped = ((opts?: cytoscape.CytoscapeOptions) => {
    const { container: _container, ...rest } = opts ?? {};
    return actual.default({ ...rest, headless: true });
  }) as typeof cytoscape;
  Object.assign(wrapped, actual.default);
  return { ...actual, default: wrapped };
});

interface BridgeInternals {
  bridge: { dispatchFromJs: (command: string, payloadJson: string) => void } | null;
  handleCommand(command: string, payloadJson: string): void;
}

const PAYLOAD: CytoscapePayload = {
  nodes: [
    { data: makeNode({ id: 'a', name: 'SourceA', processing_time_ns: 100_000 }) },
    { data: makeNode({ id: 'b', name: 'SourceB', processing_time_ns: 300_000 }) },
    { data: makeNode({ id: 'c', name: 'Merge', is_root: true, processing_time_ns: 50_000 }) },
  ],
  edges: [
    { data: makeEdge({ id: 'e1', source: 'a', target: 'c', index: 0 }) },
    { data: makeEdge({ id: 'e2', source: 'b', target: 'c', index: 1 }) },
  ],
  analysis: makeGraphAnalysis({ nodeCount: 3, edgeCount: 2 }),
};

describe('GraphManager', () => {
  let options: GraphManagerOptions;
  let manager: GraphManager;
  let bridge: BridgeInternals;
  let canvas: CytoscapeCanvas;
  let sentEvents: Array<{ command: string; payload: unknown }>;
  let centerSpy: MockInstance<CytoscapeCanvas['centerNode']>;

  function send<K extends keyof PyToJsCommandMap>(command: K, payload: PyToJsCommandMap[K]): void {
    bridge.handleCommand(command, JSON.stringify(payload));
  }

  function sentNodeSelections(): unknown[] {
    return sentEvents.filter((e) => e.command === 'node.select').map((e) => e.payload);
  }

  beforeEach(() => {
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation((contextId: string) => {
      if (contextId === '2d') {
        return {
          font: '',
          measureText: (text: string) => ({ width: text.length * 8 }),
        } as unknown as CanvasRenderingContext2D;
      }
      return null;
    });
    centerSpy = vi.spyOn(CytoscapeCanvas.prototype, 'centerNode').mockImplementation(() => {});

    const make = (): HTMLElement => document.body.appendChild(document.createElement('div'));
    options = {
      cyContainer: make(),
      hudContainer: make(),
      inspectorContainer: make(),
      overviewContainer: make(),
      loadingOverlay: make(),
      errorBanner: make(),
    };

    manager = new GraphManager(options);
    bridge = (manager as unknown as { bridge: BridgeInternals }).bridge;
    canvas = (manager as unknown as { canvas: CytoscapeCanvas }).canvas;

    sentEvents = [];
    bridge.bridge = {
      dispatchFromJs: (command, payloadJson) =>
        sentEvents.push({ command, payload: JSON.parse(payloadJson) as unknown }),
    };
  });

  afterEach(() => {
    manager.dispose();
    document.body.replaceChildren();
    vi.restoreAllMocks();
  });

  it('restores a selection after setData once and without moving the viewport', () => {
    send('graph.setData', PAYLOAD);
    send('graph.selectNode', { id: 'c' });

    expect(canvas.selectedId).toBe('c');
    expect(options.inspectorContainer.classList.contains('open')).toBe(true);
    expect(centerSpy).not.toHaveBeenCalled();

    // Python re-sends the selection after every setData
    sentEvents = [];
    send('graph.setData', PAYLOAD);
    send('graph.selectNode', { id: 'c' });

    expect(canvas.selectedId).toBe('c');
    expect(sentNodeSelections()).toEqual([{ id: 'c' }]);
    expect(centerSpy).not.toHaveBeenCalled();
  });

  it('cycles between sibling dependencies with Shift+Up', () => {
    send('graph.setData', PAYLOAD);
    send('graph.setKeybindings', {
      keybindings: [
        {
          action: 'navigateUp',
          key: 'ArrowUp',
          ctrl: false,
          shift: false,
          alt: false,
          meta: false,
        },
      ],
    });
    send('graph.selectNode', { id: 'c' });

    const shiftUp = (): boolean =>
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp', shiftKey: true }));

    shiftUp();
    expect(canvas.selectedId).toBe('b');
    shiftUp();
    expect(canvas.selectedId).toBe('a');
    shiftUp();
    expect(canvas.selectedId).toBe('b');
  });

  it('refreshes the inspector when timings update', () => {
    send('graph.setData', PAYLOAD);
    send('graph.selectNode', { id: 'a' });
    expect(options.inspectorContainer.textContent).toContain('0.10 ms');

    send('graph.updateTimings', {
      timings: [{ id: 'a', processing_time_ns: 5_000_000, processing_time_ms: 5 }],
    });
    expect(options.inspectorContainer.textContent).toContain('5.00 ms');
  });

  it('shows extraction errors and clears them on the next graph', () => {
    send('graph.setError', { message: 'Failed to inspect output 0' });

    expect(options.errorBanner.classList.contains('visible')).toBe(true);
    expect(options.errorBanner.textContent).toBe('Failed to inspect output 0');
    expect(options.loadingOverlay.classList.contains('fade-out')).toBe(true);

    send('graph.setData', PAYLOAD);
    expect(options.errorBanner.classList.contains('visible')).toBe(false);
    expect(options.errorBanner.textContent).toBe('');

    // A successful re-inspection of the displayed output only sends timings
    send('graph.setError', { message: 'Failed to inspect output 0' });
    send('graph.updateTimings', { timings: [] });
    expect(options.errorBanner.classList.contains('visible')).toBe(false);
  });
});
