import type * as monaco from "monaco-editor";
import type * as vscode from "vscode";

export type UriLike =
  | monaco.Uri
  | vscode.Uri
  | { readonly scheme: string; readonly fsPath: string; toString(): string };

/**
 * Generates a canonical, lowercase lookup key for a URI.
 * Normalizes Windows drive letter colon encoding (%3a vs :) and path separators.
 */
export function canonicalUriKey(uriOrString: UriLike | string): string {
  const str = typeof uriOrString === "string" ? uriOrString : uriOrString.toString();
  return str.toLowerCase().replace(/%3a/g, ":");
}

/**
 * Determines whether two URIs refer to the same resource.
 * On Windows, 'file' scheme comparisons normalize drive letters and case insensitivity.
 */
export function isSameResource(a: UriLike, b: UriLike): boolean {
  if (a === b) {
    return true;
  }
  if (a.scheme !== b.scheme) {
    return false;
  }
  if (a.scheme === "file") {
    return a.fsPath.toLowerCase() === b.fsPath.toLowerCase();
  }
  return canonicalUriKey(a) === canonicalUriKey(b);
}
