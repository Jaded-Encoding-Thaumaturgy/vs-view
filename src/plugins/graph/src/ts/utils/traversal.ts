import type { EdgeData, NodeData } from '../types';

/**
 * Find the deepest leaf node: the source (in-degree 0) with the largest `depth`.
 */
export function findDeepestLeafNode<T extends NodeData>(
  nodes: readonly T[],
  edges: readonly EdgeData[],
): T | null {
  const targets = new Set(edges.map((edge) => edge.target));
  const leaves = nodes.filter((node) => !targets.has(node.id));
  const candidates = leaves.length > 0 ? leaves : nodes;

  return candidates.reduce<T | null>((best, node) => {
    if (!best) return node;
    if (node.depth !== best.depth) return node.depth > best.depth ? node : best;
    return node.processing_time_ns > best.processing_time_ns ? node : best;
  }, null);
}

/**
 * Next node in a graph walk ('up' = parents, 'down' = children), slowest first.
 * With `cycle`, rotates through neighbours across calls via `cursorMap`.
 * Returns the root if `currentId` is null.
 */
export function getNextNeighborNode(
  currentId: string | null,
  direction: 'up' | 'down',
  cycle: boolean,
  nodes: readonly NodeData[],
  edges: readonly EdgeData[],
  cursorMap: Map<string, number>,
): string | null {
  if (!currentId) {
    return nodes.find((node) => node.is_root)?.id ?? null;
  }

  const neighbours = [
    ...new Set(
      edges
        .filter((edge) => (direction === 'up' ? edge.target : edge.source) === currentId)
        .map((edge) => (direction === 'up' ? edge.source : edge.target)),
    ),
  ];

  if (neighbours.length === 0) return null;

  const byId = new Map(nodes.map((node) => [node.id, node]));
  const sorted = neighbours.sort(
    (a, b) =>
      (byId.get(b)?.processing_time_ns ?? 0) - (byId.get(a)?.processing_time_ns ?? 0) ||
      a.localeCompare(b),
  );

  const cursorKey = `${currentId}:${direction}`;
  let index = 0;
  if (cycle) {
    const prev = cursorMap.get(cursorKey);
    index = prev === undefined ? 0 : (prev + 1) % sorted.length;
  }
  cursorMap.set(cursorKey, index);

  return sorted[index] ?? null;
}

/**
 * Stateful keyboard walk over the graph.
 */
export class NeighborNavigator {
  private readonly cursor = new Map<string, number>();
  private lastStep: { anchor: string; direction: 'up' | 'down'; target: string } | null = null;

  public next(
    selectedId: string | null,
    direction: 'up' | 'down',
    cycle: boolean,
    nodes: readonly NodeData[],
    edges: readonly EdgeData[],
  ): string | null {
    const last = this.lastStep;
    const anchor =
      cycle && last !== null && last.direction === direction && last.target === selectedId
        ? last.anchor
        : selectedId;

    const target = getNextNeighborNode(anchor, direction, cycle, nodes, edges, this.cursor);

    if (target !== null) {
      this.lastStep = anchor !== null ? { anchor, direction, target } : null;
    }

    return target;
  }

  public reset(): void {
    this.cursor.clear();
    this.lastStep = null;
  }
}
