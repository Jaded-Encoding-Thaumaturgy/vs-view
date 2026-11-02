import { afterEach, beforeEach, describe, expect, it, vi } from 'vite-plus/test';

import type { CanvasNodeData, IncomingConnection, NodeCopyTarget } from '../types';
import { makeCanvasEdge, makeCanvasNode, makeVideoInfo } from '../utils/test-helpers';
import { InspectorDrawer } from './inspector';

describe('InspectorDrawer', () => {
  let container: HTMLElement;
  let nodeMap: Map<string, CanvasNodeData>;
  let incomingMap: Map<string, IncomingConnection[]>;
  let drawer: InspectorDrawer;
  let focusSpy: ReturnType<typeof vi.fn<(id: string) => void>>;
  let copyNodeSpy: ReturnType<typeof vi.fn<(id: string, target: NodeCopyTarget) => void>>;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);

    copyNodeSpy = vi.fn<(id: string, target: NodeCopyTarget) => void>();
    focusSpy = vi.fn<(id: string) => void>();
    nodeMap = new Map();
    incomingMap = new Map();

    drawer = new InspectorDrawer({
      container,
      onCopyNode: copyNodeSpy,
      resolveNode: (id: string) => nodeMap.get(id) ?? null,
      getIncomingConnections: (id: string) => incomingMap.get(id) ?? [],
      onFocusNode: focusSpy,
    });
  });

  afterEach(() => {
    drawer.dispose();
    container.remove();
  });

  it('opens on inspection, closes via header close button, and cleans up on dispose', () => {
    expect(drawer.opened).toBe(false);
    expect(container.classList.contains('open')).toBe(false);

    drawer.inspectNode(makeCanvasNode({ id: 'node_1', name: 'BlankClip', is_root: true }));
    expect(drawer.opened).toBe(true);
    expect(container.classList.contains('open')).toBe(true);
    expect(container.querySelector('#inspector-name')?.textContent).toBe('BlankClip');
    expect(container.querySelector('.badge-amber')?.textContent).toBe('Output Root');

    container.querySelector<HTMLButtonElement>('#inspector-close')?.click();
    expect(drawer.opened).toBe(false);
    expect(container.classList.contains('open')).toBe(false);

    drawer.dispose();
    expect(container.children).toHaveLength(0);
  });

  it('renders upstream dependency badges in inputs card or None (Source) for source nodes', () => {
    const parentNode = makeCanvasNode({ id: 'node_1', name: 'BlankClip' });
    const childNode = makeCanvasNode({ id: 'node_2', name: 'Crop' });
    nodeMap.set('node_1', parentNode);
    nodeMap.set('node_2', childNode);
    incomingMap.set('node_2', [
      {
        edge: makeCanvasEdge({ id: 'e1', source: 'node_1', target: 'node_2', index: 0 }),
        source: parentNode,
      },
    ]);

    // Source node with no incoming edges
    drawer.inspectNode(parentNode);
    expect(container.textContent).toContain('None (Source)');

    // Child node with upstream input badge
    drawer.inspectNode(childNode);
    const inputBadge = container.querySelector<HTMLButtonElement>('.badge-input');
    expect(inputBadge?.textContent.trim()).toBe('[#0] ← BlankClip');
    expect(inputBadge?.title).toBe('Navigate to BlankClip [node_1] (Slot #0)');

    inputBadge?.click();
    expect(focusSpy).toHaveBeenCalledWith('node_1');
  });

  it('requests node ID, node report, and filter call copy for the inspected node', () => {
    const node = makeCanvasNode({
      id: 'node_123',
      name: 'Crop',
      call_stack: [
        {
          level: 0,
          frame_id: 'frame_1',
          plugin_id: 'com.vapoursynth.std',
          plugin_ns: 'std',
          function: 'Crop',
          arguments: { clip: 'node_1', left: 8 },
        },
      ],
    });
    drawer.inspectNode(node);

    container.querySelector<HTMLButtonElement>('#inspector-copy-id')?.click();
    expect(copyNodeSpy).toHaveBeenCalledWith('node_123', 'id');

    container.querySelector<HTMLButtonElement>('#inspector-copy-report')?.click();
    expect(copyNodeSpy).toHaveBeenCalledWith('node_123', 'report');

    container.querySelector<HTMLButtonElement>('#inspector-copy-call')?.click();
    expect(copyNodeSpy).toHaveBeenCalledWith('node_123', 'call');
  });

  it('formats media specs including GPU residency and updates timing display options', () => {
    const node = makeCanvasNode({
      id: 'node_gpu',
      name: 'BoxBlur',
      processing_time_ns: 2_500_000,
      media_info: makeVideoInfo({ gpu_resident: true }),
    });

    drawer.inspectNode(node);
    expect(container.textContent).toContain('GPU Resident');
    expect(container.textContent).toContain('Yes');
    expect(container.textContent).toContain('2.50 ms (2500000 ns)');

    drawer.setTimingOptions(true, 'ms');
    expect(container.textContent).toContain('2.50 ms');
    expect(container.textContent).not.toContain('2500000 ns');

    drawer.setTimingOptions(true, 'ns');
    expect(container.textContent).toContain('2500000 ns');

    drawer.setTimingOptions(false, 'ns');
    expect(container.textContent).not.toContain('Processing Time');
  });

  it('renders call stack references (resolved/unresolved clips, arrays, frames, funcs) and toggles accordion frames', () => {
    const clip1 = makeCanvasNode({ id: 'clip_1', name: 'BlankClip' });
    nodeMap.set('clip_1', clip1);

    const node = makeCanvasNode({
      id: 'node_multi',
      name: 'Interleave',
      call_stack: [
        {
          level: 0,
          frame_id: 'frame_0',
          plugin_id: 'com.vapoursynth.std',
          plugin_ns: 'std',
          function: 'Interleave',
          arguments: {
            clips: [
              { id: 'clip_1', type: 'vnode' },
              { id: 'pruned_999', type: 'vnode' },
            ],
            prop_frame: { id: 'frame_123', type: 'vframe' },
            callback: { id: 'func_456', type: 'func' },
            meta: { key: 'val' },
          },
        },
        {
          level: 1,
          frame_id: 'frame_1',
          plugin_id: 'user.script',
          plugin_ns: '',
          function: 'helper',
          arguments: {},
        },
      ],
    });

    drawer.inspectNode(node);

    // Frame 0 is expanded by default; Frame 1 is collapsed
    const items = container.querySelectorAll('.accordion-item');
    expect(items).toHaveLength(2);
    expect(items[0]?.classList.contains('active')).toBe(true);
    expect(items[1]?.classList.contains('active')).toBe(false);
    expect(items[1]?.textContent).toContain('No arguments');

    // Click Frame 1 header to expand it, and Frame 0 header to collapse it
    items[1]?.querySelector<HTMLButtonElement>('.accordion-header')?.click();
    items[0]?.querySelector<HTMLButtonElement>('.accordion-header')?.click();
    expect(container.querySelectorAll('.accordion-item')[0]?.classList.contains('active')).toBe(
      false,
    );
    expect(container.querySelectorAll('.accordion-item')[1]?.classList.contains('active')).toBe(
      true,
    );

    // Resolved and fallback clip badges in arguments table
    const clipBadges = container.querySelectorAll<HTMLButtonElement>(
      '.args-table .badge-clickable',
    );
    expect(clipBadges).toHaveLength(2);
    expect(clipBadges[0]?.textContent).toContain('BlankClip');
    expect(clipBadges[0]?.textContent).toContain('[clip_1]');
    expect(clipBadges[1]?.textContent).toContain('Clip');
    expect(clipBadges[1]?.textContent).toContain('[pruned_999]');

    clipBadges[0]?.click();
    expect(focusSpy).toHaveBeenCalledWith('clip_1');

    const argsText = container.querySelector('.args-table')?.textContent ?? '';
    expect(argsText).toContain('Frame [frame_123]');
    expect(argsText).toContain('Func [func_456]');
    expect(argsText).toContain('{"key":"val"}');
  });

  it('inspects edges with source/target resolution, pattern badge, and media transition label', () => {
    const source = makeCanvasNode({ id: 'n_src', name: 'BlankClip' });
    const target = makeCanvasNode({ id: 'n_dst', name: 'Resize' });
    const edge = makeCanvasEdge({
      id: 'e_1',
      source: 'n_src',
      target: 'n_dst',
      pattern_name: 'STRICT_SPATIAL',
      media_label: '1920x1080→1280x720',
    });

    drawer.inspectEdge(edge, source, target);

    expect(drawer.opened).toBe(true);
    expect(container.querySelector('#inspector-name')?.textContent).toBe('BlankClip → Resize');
    expect(container.querySelector('#inspector-copy-report')).toBeNull();
    expect(container.querySelector('#inspector-badges')?.textContent).toContain('STRICT_SPATIAL');
    expect(container.querySelector('#inspector-badges')?.textContent).toContain(
      '1920x1080→1280x720',
    );

    const bodyText = container.querySelector('#inspector-content')?.textContent ?? '';
    expect(bodyText).toContain('BlankClip [n_src]');
    expect(bodyText).toContain('Resize [n_dst]');
    expect(bodyText).toContain('Input Slot');
    expect(bodyText).toContain('#0');
    expect(bodyText).toContain('1920x1080→1280x720');
    expect(bodyText).toContain('e_1');
  });

  it('renders incoming connections with slot indices and preserves slot ordering', () => {
    const parent1 = makeCanvasNode({ id: 'node_1', name: 'ClipA' });
    const parent2 = makeCanvasNode({ id: 'node_2', name: 'ClipB' });
    const child = makeCanvasNode({ id: 'node_target', name: 'Merge' });

    const connsDrawer = new InspectorDrawer({
      container,
      onCopyNode: copyNodeSpy,
      resolveNode: (id: string) => (id === 'node_1' ? parent1 : id === 'node_2' ? parent2 : null),
      getIncomingConnections: () => [
        {
          edge: makeCanvasEdge({ id: 'e_1', source: 'node_2', target: 'node_target', index: 1 }),
          source: parent2,
        },
        {
          edge: makeCanvasEdge({ id: 'e_0', source: 'node_1', target: 'node_target', index: 0 }),
          source: parent1,
        },
      ],
      onFocusNode: focusSpy,
    });

    connsDrawer.inspectNode(child);
    const badges = container.querySelectorAll<HTMLButtonElement>('.badge-input');
    expect(badges).toHaveLength(2);
    expect(badges[0]?.textContent.trim()).toBe('[#1] ← ClipB');
    expect(badges[0]?.title).toBe('Navigate to ClipB [node_2] (Slot #1)');
    expect(badges[1]?.textContent.trim()).toBe('[#0] ← ClipA');
    expect(badges[1]?.title).toBe('Navigate to ClipA [node_1] (Slot #0)');

    badges[0]?.click();
    expect(focusSpy).toHaveBeenCalledWith('node_2');
    connsDrawer.dispose();
  });
});
