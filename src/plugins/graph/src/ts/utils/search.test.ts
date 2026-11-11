import { describe, expect, it } from 'vite-plus/test';

import { isEmptyQuery, matchesNode, parseSearchQuery } from './search';
import { makeNode } from './test-helpers';

describe('parseSearchQuery', () => {
  it('treats blank or whitespace-only input as an empty query', () => {
    expect(isEmptyQuery(parseSearchQuery(''))).toBe(true);
    expect(isEmptyQuery(parseSearchQuery('   '))).toBe(true);
  });

  it('parses multi-word bare text into lowercase textParts', () => {
    const query = parseSearchQuery('  Quick Brown   Fox ');
    expect(isEmptyQuery(query)).toBe(false);
    expect(query.error).toBeNull();
    expect(query.regex).toBeNull();
    expect(query.textParts).toEqual(['quick', 'brown', 'fox']);
  });

  it('parses regexes and strips stateful g and y flags', () => {
    const caseInsensitive = parseSearchQuery('/crop$/i');
    expect(caseInsensitive.error).toBeNull();
    expect(caseInsensitive.regex?.source).toBe('crop$');
    expect(caseInsensitive.regex?.flags).toBe('i');

    const stateful = parseSearchQuery('/crop/gyi');
    expect(stateful.regex?.flags).toBe('i');
  });

  it.each(['/', '/no-closing-slash', '/unterminated[/i'])(
    'reports error for invalid regex input %s',
    (raw) => {
      const query = parseSearchQuery(raw);
      expect(query.regex).toBeNull();
      expect(query.error).toContain('Invalid regular expression');
      expect(isEmptyQuery(query)).toBe(false);
    },
  );
});

describe('matchesNode', () => {
  const cropNode = makeNode({
    id: 'node_7',
    name: 'Crop',
    label: 'Crop',
    call_stack: [
      {
        level: 0,
        id: 'frame_1',
        plugin_id: 'com.vapoursynth.std',
        plugin_ns: 'std',
        function: 'Crop',
        arguments: { left: 8, right: 8, keep_sar: true, planes: [0, 1, 2] },
      },
    ],
  });

  it('matches across name, id, function, plugin namespace, argument keys, and primitive/array values', () => {
    expect(matchesNode(cropNode, parseSearchQuery('crop'))).toBe(true);
    expect(matchesNode(cropNode, parseSearchQuery('node_7'))).toBe(true);
    expect(matchesNode(cropNode, parseSearchQuery('std crop'))).toBe(true);
    expect(matchesNode(cropNode, parseSearchQuery('left 8'))).toBe(true);
    expect(matchesNode(cropNode, parseSearchQuery('keep_sar true'))).toBe(true);
    expect(matchesNode(cropNode, parseSearchQuery('planes 2'))).toBe(true);

    // Fails when any term in a multi-word query does not match
    expect(matchesNode(cropNode, parseSearchQuery('std blankclip'))).toBe(false);
    expect(matchesNode(cropNode, parseSearchQuery('planes 9'))).toBe(false);
  });

  it('evaluates anchored regexes and preserves consistency when /g or /y flags are passed', () => {
    const globalRegex = parseSearchQuery('/^crop$/gi');
    expect(matchesNode(cropNode, globalRegex)).toBe(true);
    expect(matchesNode(cropNode, globalRegex)).toBe(true);
    expect(matchesNode(cropNode, globalRegex)).toBe(true);

    expect(matchesNode(cropNode, parseSearchQuery('/^blank/i'))).toBe(false);
  });

  it('returns false when given a query with a parse error', () => {
    expect(matchesNode(cropNode, parseSearchQuery('/unterminated['))).toBe(false);
  });
});
