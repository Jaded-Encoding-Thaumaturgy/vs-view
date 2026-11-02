import type { NodeData } from '../types';

interface NamespaceStats {
  name: string;
  count: number;
  totalTimeNs: number;
}

export interface TimingStats {
  totalTimeNs: number;
  rootTimeNs: number;
  namespaces: NamespaceStats[];
}

/** Aggregate dynamic node timings for the overview panel. */
export function computeTimingStats(nodes: readonly NodeData[]): TimingStats {
  const namespaces = new Map<string, NamespaceStats>();
  let totalTimeNs = 0;
  let rootTimeNs = 0;

  for (const node of nodes) {
    totalTimeNs += node.processing_time_ns;

    if (node.is_root) {
      rootTimeNs += node.processing_time_ns;
    }

    const namespace = node.call_stack[0]?.plugin_ns ?? '';
    const entry = namespaces.get(namespace) ?? { name: namespace, count: 0, totalTimeNs: 0 };
    entry.count += 1;
    entry.totalTimeNs += node.processing_time_ns;
    namespaces.set(namespace, entry);
  }

  return {
    totalTimeNs,
    rootTimeNs,
    namespaces: [...namespaces.values()].sort(
      (a, b) => b.totalTimeNs - a.totalTimeNs || a.name.localeCompare(b.name),
    ),
  };
}
