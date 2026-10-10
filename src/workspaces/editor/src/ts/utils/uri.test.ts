import { describe, expect, it } from "vitest";

import { type UriLike, canonicalUriKey, isSameResource, isWindowsPathOrUri } from "./uri";

function createMockUri(scheme: string, fsPath: string, uriStr: string): UriLike {
  return {
    scheme,
    fsPath,
    toString: () => uriStr,
  };
}

describe("URI Utilities", () => {
  describe("isWindowsPathOrUri", () => {
    it("detects Windows drive paths and URIs", () => {
      expect(isWindowsPathOrUri("C:\\workspace\\test.py")).toBe(true);
      expect(isWindowsPathOrUri("d:/workspace/test.py")).toBe(true);
      expect(isWindowsPathOrUri("file:///C:/workspace/test.py")).toBe(true);
      expect(isWindowsPathOrUri("file:///c%3a/workspace/test.py")).toBe(true);
      expect(isWindowsPathOrUri("vscode-vfs:///C:/workspace/test.py")).toBe(true);
      expect(isWindowsPathOrUri("\\\\server\\share\\test.py")).toBe(true);
    });

    it("returns false for POSIX paths and URIs", () => {
      expect(isWindowsPathOrUri("/workspace/test.py")).toBe(false);
      expect(isWindowsPathOrUri("file:///workspace/test.py")).toBe(false);
      expect(isWindowsPathOrUri("inmemory://workspace/test.py")).toBe(false);
    });
  });

  describe("canonicalUriKey", () => {
    it("lowercases strings and normalizes encoded colons on Windows/macOS", () => {
      expect(canonicalUriKey("file:///C%3A/test/script.py", false)).toBe(
        "file:///c:/test/script.py",
      );
      expect(canonicalUriKey("file:///c%3a/test/script.py", false)).toBe(
        "file:///c:/test/script.py",
      );
      expect(canonicalUriKey("file:///C:/test/script.py", false)).toBe("file:///c:/test/script.py");
    });

    it("handles UriLike objects", () => {
      const uri = createMockUri("file", "D:\\Workspace\\main.py", "file:///D%3A/Workspace/main.py");
      expect(canonicalUriKey(uri, false)).toBe("file:///d:/workspace/main.py");
    });

    it("preserves path casing on Linux for POSIX paths while lowercasing the scheme", () => {
      expect(canonicalUriKey("FILE:///workspace/MyScript.py", true)).toBe(
        "file:///workspace/MyScript.py",
      );
      expect(canonicalUriKey("file:///workspace/myscript.py", true)).toBe(
        "file:///workspace/myscript.py",
      );
      expect(canonicalUriKey("file:///workspace/MyScript.py", true)).not.toBe(
        canonicalUriKey("file:///workspace/myscript.py", true),
      );
    });

    it("lowercases Windows drive paths even when isCaseSensitive is true", () => {
      expect(canonicalUriKey("file:///C:/Workspace/CaseTest.py", true)).toBe(
        "file:///c:/workspace/casetest.py",
      );
      expect(canonicalUriKey("FILE:///C%3A/Workspace/CaseTest.py", true)).toBe(
        "file:///c:/workspace/casetest.py",
      );
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

    it("matches file schemes case-insensitively on Windows/macOS", () => {
      const a = createMockUri("file", "C:\\Documents\\test.py", "file:///C:/Documents/test.py");
      const b = createMockUri("file", "c:\\documents\\TEST.py", "file:///c:/documents/test.py");
      expect(isSameResource(a, b, false)).toBe(true);
    });

    it("matches Windows file schemes case-insensitively even when isCaseSensitive is true", () => {
      const a = createMockUri("file", "C:\\Documents\\test.py", "file:///C:/Documents/test.py");
      const b = createMockUri("file", "c:\\documents\\TEST.py", "file:///c:/documents/test.py");
      expect(isSameResource(a, b, true)).toBe(true);
    });

    it("distinguishes file schemes case-sensitively on Linux for POSIX paths", () => {
      const a = createMockUri("file", "/workspace/script.py", "file:///workspace/script.py");
      const b = createMockUri("file", "/workspace/Script.py", "file:///workspace/Script.py");
      expect(isSameResource(a, b, true)).toBe(false);

      const aExact = createMockUri("file", "/workspace/script.py", "file:///workspace/script.py");
      expect(isSameResource(a, aExact, true)).toBe(true);
    });

    it("matches file URIs with encoded vs unencoded colons", () => {
      const a = createMockUri("file", "C:\\documents\\test.py", "file:///c:/documents/test.py");
      const b = createMockUri("file", "c:\\documents\\test.py", "file:///C%3A/documents/test.py");
      expect(isSameResource(a, b, false)).toBe(true);
    });

    it("matches non-file URIs with colon variations", () => {
      const a = createMockUri("vscode-vfs", "/c:/test.py", "vscode-vfs:///c:/test.py");
      const b = createMockUri("vscode-vfs", "/c%3a/test.py", "vscode-vfs:///C%3A/test.py");
      expect(isSameResource(a, b, false)).toBe(true);
    });

    it("correctly identifies different file paths", () => {
      const a = createMockUri("file", "C:\\Documents\\test1.py", "file:///C:/Documents/test1.py");
      const b = createMockUri("file", "C:\\Documents\\test2.py", "file:///C:/Documents/test2.py");
      expect(isSameResource(a, b)).toBe(false);
    });
  });
});
