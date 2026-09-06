import { describe, expect, it } from "vitest";

import { type UriLike, canonicalUriKey, isSameResource } from "./uri";

function createMockUri(scheme: string, fsPath: string, uriStr: string): UriLike {
  return {
    scheme,
    fsPath,
    toString: () => uriStr,
  };
}

describe("URI Utilities", () => {
  describe("canonicalUriKey", () => {
    it("lowercases strings and normalizes encoded colons", () => {
      expect(canonicalUriKey("file:///C%3A/test/script.py")).toBe("file:///c:/test/script.py");
      expect(canonicalUriKey("file:///c%3a/test/script.py")).toBe("file:///c:/test/script.py");
      expect(canonicalUriKey("file:///C:/test/script.py")).toBe("file:///c:/test/script.py");
    });

    it("handles UriLike objects", () => {
      const uri = createMockUri("file", "D:\\Workspace\\main.py", "file:///D%3A/Workspace/main.py");
      expect(canonicalUriKey(uri)).toBe("file:///d:/workspace/main.py");
    });
  });

  describe("isSameResource", () => {
    it("returns true for identical references", () => {
      const uri = createMockUri("file", "C:\\test.py", "file:///workspace/script.py");
      expect(isSameResource(uri, uri)).toBe(true);
    });

    it("returns false for different schemes", () => {
      const a = createMockUri("inmemory", "/workspace/script.py", "inmemory://workspace/script.py");
      const b = createMockUri("file", "C:\\workspace\\script.py", "file:///workspace/script.py");
      expect(isSameResource(a, b)).toBe(false);
    });

    it("matches file schemes case-insensitively on Windows", () => {
      const a = createMockUri("file", "C:\\Documents\\test.py", "file:///C:/Documents/test.py");
      const b = createMockUri("file", "c:\\documents\\TEST.py", "file:///c:/documents/test.py");
      expect(isSameResource(a, b)).toBe(true);
    });

    it("matches file URIs with encoded vs unencoded colons", () => {
      const a = createMockUri("file", "C:\\documents\\test.py", "file:///c:/documents/test.py");
      const b = createMockUri("file", "c:\\documents\\test.py", "file:///C%3A/documents/test.py");
      expect(isSameResource(a, b)).toBe(true);
    });

    it("matches non-file URIs with colon variations", () => {
      const a = createMockUri("vscode-vfs", "/c:/test.py", "vscode-vfs:///c:/test.py");
      const b = createMockUri("vscode-vfs", "/c%3a/test.py", "vscode-vfs:///C%3A/test.py");
      expect(isSameResource(a, b)).toBe(true);
    });

    it("correctly identifies different file paths", () => {
      const a = createMockUri("file", "C:\\Documents\\test1.py", "file:///C:/Documents/test1.py");
      const b = createMockUri("file", "C:\\Documents\\test2.py", "file:///C:/Documents/test2.py");
      expect(isSameResource(a, b)).toBe(false);
    });
  });
});
