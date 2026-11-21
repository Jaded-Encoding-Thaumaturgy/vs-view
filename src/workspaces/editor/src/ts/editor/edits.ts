/**
 * The smallest single replacement that turns `oldText` into `newText`, or `undefined` when they are equal.
 *
 * Keeping the common start and end in place lets the editor keep the cursor,
 * the selection and the decorations outside the changed part.
 */
export function minimalEdit(
  oldText: string,
  newText: string,
): { start: number; end: number; text: string } | undefined {
  if (oldText === newText) return undefined;

  const maxCommon = Math.min(oldText.length, newText.length);
  let prefix = 0;
  while (prefix < maxCommon && oldText.charCodeAt(prefix) === newText.charCodeAt(prefix)) {
    prefix++;
  }

  let suffix = 0;
  while (
    suffix < maxCommon - prefix &&
    oldText.charCodeAt(oldText.length - 1 - suffix) ===
      newText.charCodeAt(newText.length - 1 - suffix)
  ) {
    suffix++;
  }

  // An edge inside a CRLF pair or a surrogate pair would split one character in two.
  if (prefix > 0 && isPairStart(oldText.charCodeAt(prefix - 1))) {
    prefix--;
  }
  if (suffix > 0 && isPairEnd(oldText.charCodeAt(oldText.length - suffix))) {
    suffix--;
  }

  return {
    start: prefix,
    end: oldText.length - suffix,
    text: newText.slice(prefix, newText.length - suffix),
  };
}

function isPairStart(code: number): boolean {
  return code === 0x0d || (code >= 0xd800 && code <= 0xdbff);
}

function isPairEnd(code: number): boolean {
  return code === 0x0a || (code >= 0xdc00 && code <= 0xdfff);
}
