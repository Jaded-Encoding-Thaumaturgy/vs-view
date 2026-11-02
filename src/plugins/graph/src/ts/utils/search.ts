import { Result } from 'vsview-shared';

import type { NodeData } from '../types';

export interface SearchQuery {
  raw: string;
  textParts: string[];
  regex: RegExp | null;
  error: string | null;
}

/**
 * Parse the search box input into a plain-text (multi-word substring) or `/regex/flags` query.
 */
export function parseSearchQuery(raw: string): SearchQuery {
  const trimmed = raw.trim();

  if (!trimmed) {
    return { raw, textParts: [], regex: null, error: null };
  }

  // Whole-input regex form: /pattern/flags
  if (trimmed.startsWith('/')) {
    const lastSlash = trimmed.lastIndexOf('/');

    if (lastSlash <= 0) {
      return { raw, textParts: [], regex: null, error: 'Invalid regular expression' };
    }

    // Strip 'g' and 'y' flags to avoid stateful lastIndex issues across multi-node test calls
    const flags = trimmed.slice(lastSlash + 1).replace(/[gy]/g, '');
    return Result.fromThrowable(
      () => new RegExp(trimmed.slice(1, lastSlash), flags),
    ).match<SearchQuery>({
      ok: (regex) => ({ raw, textParts: [], regex, error: null }),
      err: (err) => ({
        raw,
        textParts: [],
        regex: null,
        error: `Invalid regular expression: ${err.message}`,
      }),
    });
  }

  return {
    raw,
    textParts: trimmed.toLowerCase().split(/\s+/),
    regex: null,
    error: null,
  };
}

export function isEmptyQuery(query: SearchQuery): boolean {
  return query.error === null && query.regex === null && query.textParts.length === 0;
}

function haystack(node: NodeData): string[] {
  const values: string[] = [node.name, node.id, node.label];

  for (const frame of node.call_stack) {
    values.push(frame.function, frame.plugin_id, frame.plugin_ns);

    for (const [key, value] of Object.entries(frame.arguments)) {
      values.push(key);
      if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
        values.push(String(value));
      } else if (Array.isArray(value)) {
        for (const item of value) {
          if (typeof item === 'string' || typeof item === 'number' || typeof item === 'boolean') {
            values.push(String(item));
          }
        }
      }
    }
  }

  return values;
}

/** Evaluate a parsed query against one node. */
export function matchesNode(node: NodeData, query: SearchQuery): boolean {
  if (query.error !== null) return false;

  const values = haystack(node);

  if (query.regex !== null) {
    const regex = query.regex;
    return values.some((value) => regex.test(value));
  }

  return query.textParts.every((part) =>
    values.some((value) => value.toLowerCase().includes(part)),
  );
}
