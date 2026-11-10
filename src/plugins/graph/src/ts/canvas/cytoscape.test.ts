import type cytoscape from 'cytoscape';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vite-plus/test';

import { makeEdge, makeGraphAnalysis, makeNode, makeVideoInfo } from '../utils/test-helpers';
import { CytoscapeCanvas } from './cytoscape';

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

describe('CytoscapeCanvas', () => {
  let container: HTMLElement;
  let canvas: CytoscapeCanvas;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation((contextId: string) => {
      if (contextId === '2d') {
        return {
          font: '',
          measureText: (text: string) => ({ width: text.length * 8 }),
        } as unknown as CanvasRenderingContext2D;
      }
      return null;
    });
    canvas = new CytoscapeCanvas();
  });

  afterEach(() => {
    canvas.dispose();
    container.remove();
    vi.restoreAllMocks();
  });

  it('returns Err from exportPng when uninitialized', () => {
    const result = canvas.exportPng();
    expect(result.ok).toBe(false);
    expect(result.match({ ok: () => '', err: (e) => e.message })).toContain(
      'Cytoscape canvas is not initialized',
    );
  });

  it('falls back to character-based width measurement when Canvas 2D context is unavailable', () => {
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
    canvas.init(container);
    canvas.setGraphData({
      nodes: [{ data: makeNode({ id: 'n1', label: 'Test', processing_time_ns: 0 }) }],
      edges: [],
      analysis: makeGraphAnalysis({ nodeCount: 1 }),
    });

    const node = canvas.getNodeById('n1');
    expect(node).not.toBeNull();
    expect(node?.node_width).toBe(4 * 8);
    expect(node?.node_height).toBe(14);
  });

  it('populates graph elements, preserves edge media transitions, and resolves incoming nodes', () => {
    canvas.init(container);

    const nodeA = makeNode({
      id: 'n1',
      name: 'BlankClip',
      label: 'BlankClip',
      processing_time_ns: 500,
    });
    const nodeB = makeNode({
      id: 'n2',
      name: 'Resize',
      label: 'Resize',
      processing_time_ns: 2_000_000,
      media_info: makeVideoInfo({ width: 1280, height: 720 }),
    });
    const edge = makeEdge({
      id: 'e1',
      source: 'n1',
      target: 'n2',
      pattern_name: 'STRICT_SPATIAL',
      media_label: '1920x1080→1280x720',
      media_fields: ['resolution'],
    });

    canvas.setGraphData({
      nodes: [{ data: nodeA }, { data: nodeB }],
      edges: [{ data: edge }],
      analysis: makeGraphAnalysis({ nodeCount: 2, edgeCount: 1 }),
    });

    expect(canvas.getNodes()).toHaveLength(2);
    expect(canvas.getNodeById('n1')?.label).toBe('BlankClip\n500 ns');
    expect(canvas.getNodeById('n2')?.label).toBe('Resize\n2.00 ms');
    expect(canvas.getNodeById('missing')).toBeNull();

    const inConns = canvas.getIncomingConnections('n2');
    expect(inConns).toHaveLength(1);
    expect(inConns[0]?.edge.id).toBe('e1');
    expect(inConns[0]?.edge.index).toBe(0);
    expect(inConns[0]?.source.id).toBe('n1');
    expect(canvas.getIncomingConnections('n1')).toEqual([]);

    const edges = canvas.getEdges();
    expect(edges).toHaveLength(1);
    expect(edges[0]?.media_label).toBe('1920x1080→1280x720');
    expect(edges[0]?.media_fields).toEqual(['resolution']);
  });

  it('updates node timings and re-formats labels when timing display options change', () => {
    canvas.init(container);
    canvas.setGraphData({
      nodes: [{ data: makeNode({ id: 'n1', label: 'Crop', processing_time_ns: 1_000_000 }) }],
      edges: [],
      analysis: makeGraphAnalysis({ nodeCount: 1 }),
    });

    canvas.updateTimings([{ id: 'n1', processing_time_ns: 3_500_000, processing_time_ms: 3.5 }]);
    expect(canvas.getNodeById('n1')?.label).toBe('Crop\n3.50 ms');

    canvas.setTimingDisplay(true, 'ns');
    expect(canvas.isShowingTimings()).toBe(true);
    expect(canvas.getTimingUnit()).toBe('ns');
    expect(canvas.getNodeById('n1')?.label).toBe('Crop\n3500000 ns');

    canvas.setTimingDisplay(false, 'ns');
    expect(canvas.isShowingTimings()).toBe(false);
    expect(canvas.getNodeById('n1')?.label).toBe('Crop');
  });

  it('manages selection, restores selection across graph reloads, and toggles visual states', () => {
    canvas.init(container);
    const payload = {
      nodes: [
        { data: makeNode({ id: 'n1', name: 'Source' }) },
        { data: makeNode({ id: 'n2', name: 'Crop' }) },
      ],
      edges: [{ data: makeEdge({ id: 'e1', source: 'n1', target: 'n2' }) }],
      analysis: makeGraphAnalysis({ nodeCount: 2, edgeCount: 1 }),
    };
    canvas.setGraphData(payload);

    expect(canvas.selectNodeById('missing')).toBeNull();
    expect(canvas.selectNodeById('n2')?.id).toBe('n2');
    expect(canvas.selectedId).toBe('n2');

    // Reloading graph data while n2 is selected restores selection and notifies subscribers
    const selectSpy = vi.fn();
    const clearSpy = vi.fn();
    const sub = canvas.onNodeSelected(selectSpy);
    const clearSub = canvas.onSelectionCleared(clearSpy);
    canvas.setGraphData(payload, true);
    expect(selectSpy).toHaveBeenCalledTimes(1);
    expect(selectSpy.mock.calls[0]?.[0]?.id).toBe('n2');
    expect(clearSpy).not.toHaveBeenCalled();

    // Reloading when the previously selected node (n2) is absent clears selection and avoids dimming
    canvas.setGraphData(
      {
        nodes: [{ data: makeNode({ id: 'n1', name: 'Source' }) }],
        edges: [],
        analysis: makeGraphAnalysis({ nodeCount: 1 }),
      },
      true,
    );
    expect(canvas.selectedId).toBeNull();
    expect(clearSpy).toHaveBeenCalledTimes(1);
    const cy = (canvas as unknown as { cy: cytoscape.Core }).cy;
    expect(cy.getElementById('n1').hasClass('dimmed')).toBe(false);

    sub.dispose();
    clearSub.dispose();

    // Pattern styles and group highlight state
    expect(canvas.isShowingPatternStyles()).toBe(false);
    canvas.setPatternStyles(true);
    expect(canvas.isShowingPatternStyles()).toBe(true);

    canvas.highlightNodeGroup(['n1']);
    expect(canvas.activeGroupHighlight?.has('n1')).toBe(true);
    canvas.highlightNodeGroup(null);
    expect(canvas.activeGroupHighlight).toBeNull();

    // Layout and option properties
    canvas.direction = 'LR';
    expect(canvas.direction).toBe('LR');
    canvas.layout = 'breadthfirst';
    expect(canvas.layout).toBe('breadthfirst');
    canvas.rankSep = 80;
    expect(canvas.rankSep).toBe(80);
    canvas.nodeSep = 50;
    expect(canvas.nodeSep).toBe(50);
    canvas.heatmapActive = true;
    expect(canvas.heatmapActive).toBe(true);
    canvas.heatmapActive = false;
    expect(canvas.heatmapActive).toBe(false);
  });

  it('keeps heatmap active without applying lineage dimming on selection', () => {
    canvas.init(container);
    canvas.setGraphData({
      nodes: [
        { data: makeNode({ id: 'n1', processing_time_ns: 1_000_000 }) },
        { data: makeNode({ id: 'n2', processing_time_ns: 4_000_000 }) },
      ],
      edges: [{ data: makeEdge({ id: 'e1', source: 'n1', target: 'n2' }) }],
      analysis: makeGraphAnalysis({ nodeCount: 2, edgeCount: 1 }),
    });
    canvas.heatmapActive = true;

    const cy = (canvas as unknown as { cy: cytoscape.Core }).cy;
    expect(canvas.selectNodeById('n2')?.id).toBe('n2');
    expect(cy.getElementById('n1').hasClass('dimmed')).toBe(false);
    expect(cy.getElementById('n2').hasClass('dimmed')).toBe(false);
    expect(cy.getElementById('n2').hasClass('selected')).toBe(true);

    canvas.clearSelection();

    expect(canvas.heatmapActive).toBe(true);
    expect(cy.getElementById('n1').hasClass('dimmed')).toBe(false);
    expect(cy.getElementById('n2').hasClass('dimmed')).toBe(false);
    expect(cy.getElementById('n2').hasClass('selected')).toBe(false);
  });

  it('moves the selection marker on programmatic selection instead of relying on tap selection', () => {
    canvas.init(container);
    canvas.setGraphData({
      nodes: [{ data: makeNode({ id: 'n1' }) }, { data: makeNode({ id: 'n2' }) }],
      edges: [{ data: makeEdge({ id: 'e1', source: 'n1', target: 'n2' }) }],
      analysis: makeGraphAnalysis({ nodeCount: 2, edgeCount: 1 }),
    });

    const cy = (canvas as unknown as { cy: cytoscape.Core }).cy;
    cy.getElementById('n1').emit('tap');
    expect(canvas.selectedId).toBe('n1');
    expect(cy.getElementById('n1').hasClass('selected')).toBe(true);
    expect(cy.$(':selected')).toHaveLength(0);

    canvas.selectNodeById('n2');
    expect(cy.getElementById('n1').hasClass('selected')).toBe(false);
    expect(cy.getElementById('n2').hasClass('selected')).toBe(true);
  });

  it('resets the viewport to 100% zoom instead of fitting', () => {
    canvas.init(container);
    canvas.setGraphData({
      nodes: [{ data: makeNode({ id: 'n1' }) }],
      edges: [],
      analysis: makeGraphAnalysis({ nodeCount: 1 }),
    });

    const cy = (canvas as unknown as { cy: cytoscape.Core }).cy;
    cy.zoom(3.5);
    canvas.reset();
    expect(cy.zoom()).toBe(1);
  });

  it('resolves edges with their endpoints by id', () => {
    canvas.init(container);
    canvas.setGraphData({
      nodes: [
        { data: makeNode({ id: 'n1', name: 'Source' }) },
        { data: makeNode({ id: 'n2', name: 'Crop' }) },
      ],
      edges: [{ data: makeEdge({ id: 'e1', source: 'n1', target: 'n2' }) }],
      analysis: makeGraphAnalysis({ nodeCount: 2, edgeCount: 1 }),
    });

    const resolved = canvas.getEdgeById('e1');
    expect(resolved?.edge.id).toBe('e1');
    expect(resolved?.source.name).toBe('Source');
    expect(resolved?.target.name).toBe('Crop');
    expect(canvas.getEdgeById('n1')).toBeNull();
    expect(canvas.getEdgeById('missing')).toBeNull();
  });

  it('sorts incoming connections by edge index and manages edge tooltip on hover', () => {
    canvas.init(container);
    const nodeA = makeNode({ id: 'src_a', name: 'ClipA' });
    const nodeB = makeNode({ id: 'src_b', name: 'ClipB' });
    const nodeTarget = makeNode({ id: 'target_node', name: 'MaskedMerge' });

    // Add edges with index 2, 0, 1 out of order
    const edge2 = makeEdge({ id: 'e2', source: 'src_b', target: 'target_node', index: 2 });
    const edge0 = makeEdge({ id: 'e0', source: 'src_a', target: 'target_node', index: 0 });
    const edge1 = makeEdge({ id: 'e1', source: 'src_b', target: 'target_node', index: 1 });

    canvas.setGraphData({
      nodes: [{ data: nodeA }, { data: nodeB }, { data: nodeTarget }],
      edges: [{ data: edge2 }, { data: edge0 }, { data: edge1 }],
      analysis: makeGraphAnalysis({ nodeCount: 3, edgeCount: 3 }),
    });

    const conns = canvas.getIncomingConnections('target_node');
    expect(conns).toHaveLength(3);
    expect(conns.map((c) => c.edge.index)).toEqual([0, 1, 2]);
    expect(conns[0]?.source.name).toBe('ClipA');
    expect(conns[1]?.source.name).toBe('ClipB');
    expect(conns[2]?.source.name).toBe('ClipB');

    // Edge hover sets container title with Slot # and pattern
    const cy = (canvas as unknown as { cy: cytoscape.Core }).cy;
    const edgeElem = cy.getElementById('e0');
    edgeElem.emit('mouseover');
    expect(container.title).toBe('ClipA → MaskedMerge (Slot #0)\nPattern: GENERAL');

    edgeElem.emit('mouseout');
    expect(container.title).toBe('');
  });
});
