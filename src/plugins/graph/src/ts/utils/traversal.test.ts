import { describe, expect, it } from 'vite-plus/test';

import { makeEdge, makeNode } from './test-helpers';
import { NeighborNavigator, findDeepestLeafNode, getNextNeighborNode } from './traversal';

describe('findDeepestLeafNode', () => {
  it('returns null for empty node list', () => {
    expect(findDeepestLeafNode([], [])).toBeNull();
  });

  it('prefers the deepest source over a slower shallow source', () => {
    const shallow = makeNode({ id: 'shallow', depth: 1, processing_time_ns: 9_000_000 });
    const deep = makeNode({ id: 'deep', depth: 2, processing_time_ns: 100_000 });
    const mid = makeNode({ id: 'mid', depth: 1, processing_time_ns: 100_000 });
    const root = makeNode({ id: 'root', is_root: true });

    const edges = [
      makeEdge({ id: 'e1', source: 'shallow', target: 'root' }),
      makeEdge({ id: 'e2', source: 'deep', target: 'mid' }),
      makeEdge({ id: 'e3', source: 'mid', target: 'root' }),
    ];
    expect(findDeepestLeafNode([shallow, deep, mid, root], edges)?.id).toBe('deep');
  });

  it('breaks depth ties by processing time', () => {
    const leafA = makeNode({ id: 'leaf_a', depth: 1, processing_time_ns: 100_000 });
    const leafB = makeNode({ id: 'leaf_b', depth: 1, processing_time_ns: 500_000 });
    const root = makeNode({ id: 'root', is_root: true, processing_time_ns: 1_000_000 });

    const edges = [
      makeEdge({ id: 'e1', source: 'leaf_a', target: 'root' }),
      makeEdge({ id: 'e2', source: 'leaf_b', target: 'root' }),
    ];
    expect(findDeepestLeafNode([leafA, leafB, root], edges)?.id).toBe('leaf_b');
  });

  it('falls back to the slowest node when every node is part of a cycle', () => {
    const a = makeNode({ id: 'a', processing_time_ns: 100_000 });
    const b = makeNode({ id: 'b', processing_time_ns: 1_000_000 });

    const edges = [
      makeEdge({ id: 'e1', source: 'a', target: 'b' }),
      makeEdge({ id: 'e2', source: 'b', target: 'a' }),
    ];
    expect(findDeepestLeafNode([a, b], edges)?.id).toBe('b');
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

describe('NeighborNavigator', () => {
  const nodeA = makeNode({ id: 'a', processing_time_ns: 100_000 });
  const nodeB = makeNode({ id: 'b', processing_time_ns: 300_000 });
  const nodeC = makeNode({ id: 'c', is_root: true, processing_time_ns: 50_000 });
  const nodes = [nodeA, nodeB, nodeC];

  const edges = [
    makeEdge({ id: 'e1', source: 'a', target: 'c' }),
    makeEdge({ id: 'e2', source: 'b', target: 'c' }),
  ];

  it('cycles between siblings while the selection follows each step', () => {
    const navigator = new NeighborNavigator();
    let selected: string | null = 'c';

    selected = navigator.next(selected, 'up', true, nodes, edges);
    expect(selected).toBe('b');
    selected = navigator.next(selected, 'up', true, nodes, edges);
    expect(selected).toBe('a');
    selected = navigator.next(selected, 'up', true, nodes, edges);
    expect(selected).toBe('b');
  });

  it('continues a plain step with cycling and starts a new walk when the direction changes', () => {
    const navigator = new NeighborNavigator();

    expect(navigator.next('c', 'up', false, nodes, edges)).toBe('b');
    expect(navigator.next('b', 'up', true, nodes, edges)).toBe('a');

    expect(navigator.next('c', 'down', true, nodes, edges)).toBeNull();
    expect(navigator.next('a', 'down', true, nodes, edges)).toBe('c');
  });

  it('starts from the root without a selection and forgets the walk on reset', () => {
    const navigator = new NeighborNavigator();

    expect(navigator.next(null, 'up', true, nodes, edges)).toBe('c');
    expect(navigator.next('c', 'up', true, nodes, edges)).toBe('b');

    navigator.reset();
    expect(navigator.next('b', 'up', true, nodes, edges)).toBeNull();
  });
});
