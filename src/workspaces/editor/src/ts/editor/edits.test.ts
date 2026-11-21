import { describe, expect, it } from 'vite-plus/test';

import { minimalEdit } from './edits';

describe('minimalEdit', () => {
  it('answers undefined for equal texts', () => {
    expect(minimalEdit('a = 1\n', 'a = 1\n')).toBeUndefined();
  });

  it.each([
    { case: 'change in the middle', old: 'a=1\nb=2\n', new: 'a=1\nb = 2\n', edit: [5, 6, ' = '] },
    { case: 'insert at the start', old: 'x', new: 'yx', edit: [0, 0, 'y'] },
    { case: 'append at the end', old: 'x', new: 'xy', edit: [1, 1, 'y'] },
    { case: 'delete everything', old: 'abc', new: '', edit: [0, 3, ''] },
    { case: 'repeated text', old: 'aaa', new: 'aaaa', edit: [3, 3, 'a'] },
    { case: 'CRLF kept whole', old: 'a\r\nb', new: 'a\rc\r\nb', edit: [1, 3, '\rc\r\n'] },
    { case: 'CRLF at the end kept whole', old: 'a\nb\r\n', new: 'a\nc\n', edit: [2, 5, 'c\n'] },
    {
      case: 'surrogate pair kept whole',
      old: '\u{1F600}',
      new: '\u{1F601}',
      edit: [0, 2, '\u{1F601}'],
    },
  ])('replaces only the changed part: $case', ({ old, new: next, edit }) => {
    const [start, end, text] = edit;

    const result = minimalEdit(old, next);

    expect(result).toEqual({ start, end, text });
    expect(old.slice(0, result!.start) + result!.text + old.slice(result!.end)).toBe(next);
  });
});
