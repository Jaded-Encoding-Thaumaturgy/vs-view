import cytoscape from 'cytoscape';
import { describe, expect, it } from 'vite-plus/test';

import { VisualState } from './visual-state';

describe('VisualState', () => {
  function createTestGraph() {
    return cytoscape({
      headless: true,
      elements: [
        { data: { id: 'a', filter_mode_name: 'PARALLEL' } },
        { data: { id: 'b', filter_mode_name: 'FRAME_STATE' } },
        { data: { id: 'c', filter_mode_name: 'UNORDERED' } },
        { data: { id: 'd', filter_mode_name: 'PARALLEL_REQUESTS' } },
        { data: { id: 'e1', source: 'a', target: 'b' } },
        { data: { id: 'e2', source: 'b', target: 'c' } },
      ],
    });
  }

  it('manages lineage state, highlights upstream/downstream edges, and dims unrelated nodes', () => {
    const state = new VisualState();
    const cy = createTestGraph();

    expect(state.hasLineage).toBe(false);

    state.setLineage('b', ['a'], ['c']);
    expect(state.hasLineage).toBe(true);

    state.apply(cy);

    expect(cy.$('#b').hasClass('dimmed')).toBe(false);
    expect(cy.$('#a').hasClass('highlighted-upstream')).toBe(true);
    expect(cy.$('#c').hasClass('highlighted-downstream')).toBe(true);
    expect(cy.$('#d').hasClass('dimmed')).toBe(true);
    expect(cy.$('#e1').hasClass('highlighted-upstream')).toBe(true);
    expect(cy.$('#e2').hasClass('highlighted-downstream')).toBe(true);

    // Heatmap mode keeps every node fully visible without discarding the current selection.
    state.apply(cy, { showLineage: false });
    expect(cy.$('#a').hasClass('highlighted-upstream')).toBe(false);
    expect(cy.$('#c').hasClass('highlighted-downstream')).toBe(false);
    expect(cy.$('#d').hasClass('dimmed')).toBe(false);

    // Disabling heatmap restores the still-active lineage treatment.
    state.apply(cy);
    expect(cy.$('#a').hasClass('highlighted-upstream')).toBe(true);
    expect(cy.$('#c').hasClass('highlighted-downstream')).toBe(true);
    expect(cy.$('#d').hasClass('dimmed')).toBe(true);

    state.clearLineage();
    expect(state.hasLineage).toBe(false);
    state.apply(cy);
    expect(cy.$('#a').hasClass('highlighted-upstream')).toBe(false);
    expect(cy.$('#d').hasClass('dimmed')).toBe(false);
  });

  it('highlights search matches, dims non-matches, and keeps edges between two matched nodes undimmed', () => {
    const state = new VisualState();
    const cy = createTestGraph();

    // Matching both 'a' and 'b' keeps edge e1 (a->b) undimmed while dimming e2 (b->c)
    state.setSearch(['a', 'b']);
    state.apply(cy);

    expect(cy.$('#a').hasClass('search-matched')).toBe(true);
    expect(cy.$('#b').hasClass('search-matched')).toBe(true);
    expect(cy.$('#e1').hasClass('dimmed')).toBe(false);
    expect(cy.$('#c').hasClass('dimmed')).toBe(true);
    expect(cy.$('#e2').hasClass('dimmed')).toBe(true);

    // Empty search array dims all unselected nodes
    state.setSearch([]);
    state.apply(cy);
    expect(cy.$('#a').hasClass('dimmed')).toBe(true);
    expect(cy.$('#b').hasClass('dimmed')).toBe(true);

    // Null disables search dimming
    state.setSearch(null);
    state.apply(cy);
    expect(cy.$('#a').hasClass('dimmed')).toBe(false);
    expect(cy.$('#b').hasClass('dimmed')).toBe(false);
  });

  it('dims non-matching filter modes and group highlights while protecting the selected node', () => {
    const state = new VisualState();
    const cy = createTestGraph();

    state.setLineage('b', ['a'], []);
    state.setModeFilter('PARALLEL');
    state.apply(cy);

    expect(cy.$('#a').hasClass('dimmed')).toBe(false);
    expect(cy.$('#b').hasClass('dimmed')).toBe(false); // selected node is protected
    expect(cy.$('#c').hasClass('dimmed')).toBe(true);
    expect(cy.$('#d').hasClass('dimmed')).toBe(true);

    state.setModeFilter(null);
    state.clearLineage();
    state.setGroupHighlight(['b', 'c']);
    expect(state.activeGroupHighlight?.has('b')).toBe(true);
    state.apply(cy);

    expect(cy.$('#b').hasClass('dimmed')).toBe(false);
    expect(cy.$('#c').hasClass('dimmed')).toBe(false);
    expect(cy.$('#a').hasClass('dimmed')).toBe(true);
    expect(cy.$('#d').hasClass('dimmed')).toBe(true);

    state.setGroupHighlight(null);
    expect(state.activeGroupHighlight).toBeNull();
    state.apply(cy);
    expect(cy.$('#a').hasClass('dimmed')).toBe(false);
  });

  it('protects media-changed edges and nodes when mediaOnly is active', () => {
    const state = new VisualState();
    const cy = createTestGraph();

    cy.$('#e1').addClass('media-changed');
    cy.$('#b').addClass('media-change-node');

    state.setMediaOnly(true);
    state.apply(cy);

    expect(cy.$('#b').hasClass('dimmed')).toBe(false);
    expect(cy.$('#e1').hasClass('dimmed')).toBe(false);
    expect(cy.$('#a').hasClass('dimmed')).toBe(true);
    expect(cy.$('#e2').hasClass('dimmed')).toBe(true);
  });
});
