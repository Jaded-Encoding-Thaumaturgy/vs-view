import { isLinux } from "@codingame/monaco-vscode-api/vscode/vs/base/common/platform";
import type * as monaco from "monaco-editor";
import type * as vscode from "vscode";

export type UriLike =
  | monaco.Uri
  | vscode.Uri
  | { readonly scheme: string; readonly fsPath: string; toString(): string };

export function isWindowsPathOrUri(pathOrUri: string): boolean {
  if (/^[a-zA-Z]:([\\/]|$)/.test(pathOrUri) || /^(\/\/|\\\\)/.test(pathOrUri)) {
    return true;
  }
  const normalized = pathOrUri.replace(/%3a/gi, ":");
  return /^[a-z0-9+.-]+:\/\/[^/]*\/[a-zA-Z]:([\\/]|$)/i.test(normalized);
}

/**
 * Generates a canonical lookup key for a URI.
 * - On Windows & macOS (or for Windows-style paths): case-insensitive path folding.
 * - On Linux (for POSIX paths): case-sensitive path preservation, normalizing scheme and drive encoding.
 */
export function canonicalUriKey(
  uriOrString: UriLike | string,
  isCaseSensitive: boolean = isLinux,
): string {
  const str = typeof uriOrString === "string" ? uriOrString : uriOrString.toString();
  const normalized = str.replace(/%3a/gi, ":");

  const isCasePreserved = isCaseSensitive && !isWindowsPathOrUri(normalized);

  if (isCasePreserved) {
    const match = normalized.match(/^([a-z0-9+.-]+:\/\/[^/]*)(.*)$/i);
    if (match && match[1] && match[2] !== undefined) {
      return match[1].toLowerCase() + match[2];
    }
    return normalized;
  }

  return normalized.toLowerCase();
}

/**
 * Determines whether two URIs refer to the same resource.
 * Respects OS case sensitivity: case-sensitive on Linux for POSIX paths,
 * case-insensitive on Windows/macOS and for Windows-style paths.
 */
export function isSameResource(
  a: UriLike,
  b: UriLike,
  isCaseSensitive: boolean = isLinux,
): boolean {
  if (a === b) {
    return true;
  }
  if (a.scheme !== b.scheme) {
    return false;
  }
  if (a.scheme === "file") {
    const isWindowsPath = isWindowsPathOrUri(a.fsPath) || isWindowsPathOrUri(b.fsPath);
    const caseSensitive = isWindowsPath ? false : isCaseSensitive;
    return caseSensitive
      ? a.fsPath === b.fsPath
      : a.fsPath.toLowerCase() === b.fsPath.toLowerCase();
  }
  return canonicalUriKey(a, isCaseSensitive) === canonicalUriKey(b, isCaseSensitive);
}
