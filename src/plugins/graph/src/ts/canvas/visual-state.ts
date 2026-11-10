import type cytoscape from 'cytoscape';

import type { FilterModeName } from '../types';

const NODE_TRANSIENT_CLASSES =
  'selected dimmed highlighted-upstream highlighted-downstream search-matched' as const;
const EDGE_TRANSIENT_CLASSES = 'dimmed highlighted-upstream highlighted-downstream' as const;

/**
 * Single owner of every transient visual class on the canvas.
 */
export class VisualState {
  private selectedId: string | null = null;
  private upstream = new Set<string>();
  private downstream = new Set<string>();
  private search: Set<string> | null = null;
  private mode: FilterModeName | null = null;
  private mediaOnly = false;
  private groupHighlight: Set<string> | null = null;

  public get hasLineage(): boolean {
    return this.selectedId !== null;
  }

  public get activeGroupHighlight(): ReadonlySet<string> | null {
    return this.groupHighlight;
  }

  public setLineage(
    selectedId: string,
    upstream: Iterable<string>,
    downstream: Iterable<string>,
  ): void {
    this.selectedId = selectedId;
    this.upstream = new Set(upstream);
    this.downstream = new Set(downstream);
  }

  public clearLineage(): void {
    this.selectedId = null;
    this.upstream.clear();
    this.downstream.clear();
  }

  /** `null` disables search dimming, an empty array dims everything. */
  public setSearch(matches: Iterable<string> | null): void {
    this.search = matches === null ? null : new Set(matches);
  }

  public setModeFilter(mode: FilterModeName | null): void {
    this.mode = mode;
  }

  public setMediaOnly(enabled: boolean): void {
    this.mediaOnly = enabled;
  }

  public setGroupHighlight(nodeIds: Iterable<string> | null): void {
    this.groupHighlight = nodeIds === null ? null : new Set(nodeIds);
  }

  public apply(cy: cytoscape.Core, { showLineage = true } = {}): void {
    cy.batch(() => {
      const nodes = cy.nodes();
      const edges = cy.edges();

      nodes.removeClass(NODE_TRANSIENT_CLASSES);
      edges.removeClass(EDGE_TRANSIENT_CLASSES);

      const dimmed = new Set<string>();
      const lineageActive = showLineage && this.selectedId !== null;
      const groupHighlightActive = this.groupHighlight !== null;

      nodes.forEach((node) => {
        const id = node.id();
        const isSelected = id === this.selectedId;
        const isUpstream = this.upstream.has(id);
        const isDownstream = this.downstream.has(id);

        if (isSelected) {
          node.addClass('selected');
        }

        if (lineageActive) {
          if (!isSelected && isUpstream) {
            node.addClass('highlighted-upstream');
          } else if (!isSelected && isDownstream) {
            node.addClass('highlighted-downstream');
          } else if (!isSelected) {
            dimmed.add(id);
          }
        }

        if (this.search !== null) {
          if (this.search.has(id)) {
            node.addClass('search-matched');
          } else if (!isSelected) {
            dimmed.add(id);
          }
        }

        if (!isSelected) {
          if (this.mode !== null && node.data('filter_mode_name') !== this.mode) {
            dimmed.add(id);
          }
          if (this.mediaOnly && !node.hasClass('media-change-node')) {
            dimmed.add(id);
          }
          if (groupHighlightActive && !this.groupHighlight?.has(id)) {
            dimmed.add(id);
          }
        }
      });

      if (lineageActive) {
        edges.forEach((edge) => {
          const source = edge.source().id();
          const target = edge.target().id();

          const isUpstreamEdge =
            (target === this.selectedId && this.upstream.has(source)) ||
            (this.upstream.has(source) && this.upstream.has(target));

          const isDownstreamEdge =
            (source === this.selectedId && this.downstream.has(target)) ||
            (this.downstream.has(source) && this.downstream.has(target));

          if (isUpstreamEdge) {
            edge.addClass('highlighted-upstream');
          } else if (isDownstreamEdge) {
            edge.addClass('highlighted-downstream');
          }
        });
      }

      nodes.forEach((node) => {
        if (dimmed.has(node.id())) {
          node.addClass('dimmed');
        }
      });

      edges.forEach((edge) => {
        if (edge.hasClass('highlighted-upstream') || edge.hasClass('highlighted-downstream')) {
          return;
        }

        if (this.mediaOnly && edge.hasClass('media-changed')) {
          return;
        }

        const source = edge.source().id();
        const target = edge.target().id();
        const bothMatched =
          this.search !== null && this.search.has(source) && this.search.has(target);

        if (!bothMatched && (dimmed.has(source) || dimmed.has(target))) {
          edge.addClass('dimmed');
        }
      });
    });
  }
}
