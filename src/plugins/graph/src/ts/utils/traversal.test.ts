import { describe, expect, it } from 'vite-plus/test';

import { makeEdge, makeNode } from './test-helpers';
import { findSlowestLeafNode, getNextNeighborNode } from './traversal';

describe('findSlowestLeafNode', () => {
  it('returns null for empty node list', () => {
    expect(findSlowestLeafNode([], [])).toBeNull();
  });

  it('selects the slowest leaf node (in-degree 0) and falls back to all nodes on cycles', () => {
    const leafA = makeNode({ id: 'leaf_a', processing_time_ns: 100_000 });
    const leafB = makeNode({ id: 'leaf_b', processing_time_ns: 500_000 });
    const root = makeNode({ id: 'root', is_root: true, processing_time_ns: 1_000_000 });

    const dagEdges = [
      makeEdge({ id: 'e1', source: 'leaf_a', target: 'root' }),
      makeEdge({ id: 'e2', source: 'leaf_b', target: 'root' }),
    ];
    expect(findSlowestLeafNode([leafA, leafB, root], dagEdges)?.id).toBe('leaf_b');

    // When every node is an edge target (cycle), falls back to slowest overall node
    const cycleEdges = [
      makeEdge({ id: 'e1', source: 'leaf_a', target: 'root' }),
      makeEdge({ id: 'e2', source: 'root', target: 'leaf_a' }),
    ];
    expect(findSlowestLeafNode([leafA, root], cycleEdges)?.id).toBe('root');
  });
});

describe('getNextNeighborNode', () => {
  const nodeA = makeNode({ id: 'a', processing_time_ns: 100_000 });
  const nodeB = makeNode({ id: 'b', processing_time_ns: 300_000 });
  const nodeC = makeNode({ id: 'c', is_root: true, processing_time_ns: 50_000 });

  const edges = [
    makeEdge({ id: 'e1', source: 'a', target: 'c' }),
    makeEdge({ id: 'e2', source: 'b', target: 'c' }),
  ];

  it('returns root node when currentId is null, or null if no root exists', () => {
    const cursor = new Map<string, number>();
    expect(getNextNeighborNode(null, 'up', false, [nodeA, nodeB, nodeC], edges, cursor)).toBe('c');
    expect(getNextNeighborNode(null, 'up', false, [nodeA, nodeB], edges, cursor)).toBeNull();
  });

  it('navigates up to parents and down to children, returning null at boundaries', () => {
    const cursor = new Map<string, number>();
    // Up from c -> slowest parent b
    expect(getNextNeighborNode('c', 'up', false, [nodeA, nodeB, nodeC], edges, cursor)).toBe('b');
    // Up from leaf b -> no parents -> null
    expect(getNextNeighborNode('b', 'up', false, [nodeA, nodeB, nodeC], edges, cursor)).toBeNull();
    // Down from leaf a -> child c
    expect(getNextNeighborNode('a', 'down', false, [nodeA, nodeB, nodeC], edges, cursor)).toBe('c');
    // Down from root c -> no children -> null
    expect(
      getNextNeighborNode('c', 'down', false, [nodeA, nodeB, nodeC], edges, cursor),
    ).toBeNull();
  });

  it('cycles through neighbors sorted by processing time then ID', () => {
    const cursor = new Map<string, number>();
    expect(getNextNeighborNode('c', 'up', true, [nodeA, nodeB, nodeC], edges, cursor)).toBe('b');
    expect(getNextNeighborNode('c', 'up', true, [nodeA, nodeB, nodeC], edges, cursor)).toBe('a');
    expect(getNextNeighborNode('c', 'up', true, [nodeA, nodeB, nodeC], edges, cursor)).toBe('b');
  });
});
