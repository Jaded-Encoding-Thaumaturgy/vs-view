import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Hoisted Monaco Models & URI Mock Setup
const { FakeUri, FakeTextModel, modelsMap, mockModelsList } = vi.hoisted(() => {
  class FakeUri {
    public scheme: string;
    public path: string;
    public fsPath: string;
    private _uriStr: string;

    constructor(scheme: string, path: string, fsPath?: string, uriStr?: string) {
      this.scheme = scheme;
      this.path = path;
      this.fsPath =
        fsPath ?? (scheme === "file" ? path.replace(/^\//, "").replace(/\//g, "\\") : path);
      this._uriStr = uriStr ?? `${scheme}://${path}`;
    }

    public toString(): string {
      return this._uriStr;
    }

    public static file(path: string): FakeUri {
      const normalized = path.replace(/\\/g, "/");
      const uriPath = normalized.startsWith("/") ? normalized : `/${normalized}`;
      const fsPath = path.replace(/\//g, "\\");
      return new FakeUri("file", uriPath, fsPath, `file://${uriPath}`);
    }

    public static parse(uriStr: string): FakeUri {
      const scheme = uriStr.split(":")[0] || "file";
      let path = uriStr.replace(/^[^:]+:\/\//, "");
      if (!path.startsWith("/")) {
        path = "/" + path;
      }
      let fsPath = path.startsWith("/") ? path.slice(1) : path;
      fsPath = decodeURIComponent(fsPath).replace(/\//g, "\\");
      return new FakeUri(scheme, path, fsPath, uriStr);
    }
  }

  class FakeTextModel {
    public uri: FakeUri;
    private content: string;
    private _isDisposed = false;

    constructor(content: string, uri: FakeUri) {
      this.content = content;
      this.uri = uri;
    }

    public getValue(): string {
      return this.content;
    }

    public getValueLength(): number {
      return this.content.length;
    }

    public isDisposed(): boolean {
      return this._isDisposed;
    }

    public dispose(): void {
      this._isDisposed = true;
    }
  }

  const modelsMap = new Map<string, FakeTextModel>();
  const mockModelsList: FakeTextModel[] = [];

  return {
    FakeUri,
    FakeTextModel,
    modelsMap,
    mockModelsList,
  };
});

// Mock monaco-editor module methods used by findExistingModel and URI handling
vi.mock("monaco-editor", () => ({
  Uri: FakeUri,
  editor: {
    getModel: (uri: { toString(): string }) => modelsMap.get(uri.toString()),
    getModels: () => mockModelsList.filter((m) => !m.isDisposed()),
  },
}));

import {
  FilePermission,
  FileSystemProviderCapabilities,
  FileSystemProviderError,
  FileSystemProviderErrorCode,
  FileType,
} from "@codingame/monaco-vscode-api/vscode/vs/platform/files/common/files";
import type * as monaco from "monaco-editor";

import { BridgeService } from "../bridge/python";
import type { FileStatResponse } from "../types";
import { Result, ResultAsync } from "../utils/result";
import { DiskFileSystemProvider } from "./fs";
import { findExistingModel } from "./models";

// Test Suites
describe("DiskFileSystemProvider & findExistingModel Integration Tests", () => {
  let provider: DiskFileSystemProvider;

  beforeEach(() => {
    provider = new DiskFileSystemProvider();
    modelsMap.clear();
    mockModelsList.length = 0;
    vi.restoreAllMocks();
  });

  afterEach(() => {
    modelsMap.clear();
    mockModelsList.length = 0;
    vi.restoreAllMocks();
  });

  // Readonly Safety Invariants
  describe("1. Readonly Safety Invariants", () => {
    const fileUri = FakeUri.file("C:\\workspace\\test.py") as unknown as monaco.Uri;
    const destUri = FakeUri.file("C:\\workspace\\dest.py") as unknown as monaco.Uri;

    it("declares FileReadWrite capability", () => {
      expect(provider.capabilities & FileSystemProviderCapabilities.FileReadWrite).toBeTruthy();
    });

    it("writeFile() rejects with NoPermissions error", async () => {
      const content = new TextEncoder().encode("print('forbidden')");
      await expect(
        provider.writeFile(fileUri, content, {
          create: true,
          overwrite: true,
          unlock: false,
          atomic: false,
        }),
      ).rejects.toSatisfy((err: unknown) => {
        expect(err).toBeInstanceOf(FileSystemProviderError);
        const fsErr = err as FileSystemProviderError;
        expect(fsErr.code).toBe(FileSystemProviderErrorCode.NoPermissions);
        expect(fsErr.message).toContain("Readonly file system");
        return true;
      });
    });

    it("delete() rejects with NoPermissions error", async () => {
      await expect(
        provider.delete(fileUri, {
          recursive: false,
          useTrash: false,
          atomic: false,
        }),
      ).rejects.toSatisfy((err: unknown) => {
        expect(err).toBeInstanceOf(FileSystemProviderError);
        const fsErr = err as FileSystemProviderError;
        expect(fsErr.code).toBe(FileSystemProviderErrorCode.NoPermissions);
        expect(fsErr.message).toContain("Readonly file system");
        return true;
      });
    });

    it("mkdir() rejects with NoPermissions error", async () => {
      await expect(provider.mkdir(fileUri)).rejects.toSatisfy((err: unknown) => {
        expect(err).toBeInstanceOf(FileSystemProviderError);
        const fsErr = err as FileSystemProviderError;
        expect(fsErr.code).toBe(FileSystemProviderErrorCode.NoPermissions);
        expect(fsErr.message).toContain("Readonly file system");
        return true;
      });
    });

    it("createDirectory() rejects with NoPermissions error", async () => {
      await expect(provider.createDirectory(fileUri)).rejects.toSatisfy((err: unknown) => {
        expect(err).toBeInstanceOf(FileSystemProviderError);
        const fsErr = err as FileSystemProviderError;
        expect(fsErr.code).toBe(FileSystemProviderErrorCode.NoPermissions);
        expect(fsErr.message).toContain("Readonly file system");
        return true;
      });
    });

    it("rename() rejects with NoPermissions error", async () => {
      await expect(provider.rename(fileUri, destUri, { overwrite: false })).rejects.toSatisfy(
        (err: unknown) => {
          expect(err).toBeInstanceOf(FileSystemProviderError);
          const fsErr = err as FileSystemProviderError;
          expect(fsErr.code).toBe(FileSystemProviderErrorCode.NoPermissions);
          expect(fsErr.message).toContain("Readonly file system");
          return true;
        },
      );
    });

    it("copy() rejects with NoPermissions error", async () => {
      await expect(provider.copy(fileUri, destUri, { overwrite: false })).rejects.toSatisfy(
        (err: unknown) => {
          expect(err).toBeInstanceOf(FileSystemProviderError);
          const fsErr = err as FileSystemProviderError;
          expect(fsErr.code).toBe(FileSystemProviderErrorCode.NoPermissions);
          expect(fsErr.message).toContain("Readonly file system");
          return true;
        },
      );
    });

    it("readdir() returns an empty array", async () => {
      const result = await provider.readdir(fileUri);
      expect(result).toEqual([]);
    });

    it("watch() returns a disposable resource", () => {
      const watcher = provider.watch(fileUri, { recursive: false, excludes: [] });
      expect(watcher).toBeDefined();
      expect(typeof watcher.dispose).toBe("function");
      expect(() => watcher.dispose()).not.toThrow();
    });
  });

  // DiskFileSystemProvider.readFile()
  describe("2. DiskFileSystemProvider.readFile()", () => {
    it("throws FileNotFound for non-file scheme URIs", async () => {
      const memUri = FakeUri.parse("inmemory://workspace/script.py") as unknown as monaco.Uri;
      await expect(provider.readFile(memUri)).rejects.toSatisfy((err: unknown) => {
        expect(err).toBeInstanceOf(FileSystemProviderError);
        const fsErr = err as FileSystemProviderError;
        expect(fsErr.code).toBe(FileSystemProviderErrorCode.FileNotFound);
        expect(fsErr.message).toContain("Unsupported scheme: inmemory");
        return true;
      });
    });

    it("returns in-memory model content without calling BridgeService.readFile", async () => {
      const uriStr = "file:///C:/workspace/in_memory.py";
      const uri = FakeUri.parse(uriStr);
      const modelContent = "import vapoursynth as vs\ncore = vs.core\n";
      const model = new FakeTextModel(modelContent, uri);
      modelsMap.set(uriStr, model);
      mockModelsList.push(model);

      const readFileSpy = vi.spyOn(BridgeService, "readFile");

      const result = await provider.readFile(uri as unknown as monaco.Uri);

      expect(readFileSpy).not.toHaveBeenCalled();
      const decoded = new TextDecoder().decode(result);
      expect(decoded).toBe(modelContent);
    });

    it("falls back to BridgeService.readFile when no model exists in memory", async () => {
      const uriStr = "file:///C:/workspace/on_disk.py";
      const uri = FakeUri.parse(uriStr);
      const diskContent = "# Disk file content\nprint('from disk')\n";

      const readFileSpy = vi
        .spyOn(BridgeService, "readFile")
        .mockReturnValue(ResultAsync.fromResult(Result.ok(diskContent)));

      const result = await provider.readFile(uri as unknown as monaco.Uri);

      expect(readFileSpy).toHaveBeenCalledOnce();
      expect(readFileSpy).toHaveBeenCalledWith(uri.fsPath);
      const decoded = new TextDecoder().decode(result);
      expect(decoded).toBe(diskContent);
    });

    it("throws FileNotFound when BridgeService.readFile returns an error", async () => {
      const uriStr = "file:///C:/workspace/missing.py";
      const uri = FakeUri.parse(uriStr);

      vi.spyOn(BridgeService, "readFile").mockReturnValue(
        ResultAsync.fromResult(Result.err(new Error("Host file not found"))),
      );

      await expect(provider.readFile(uri as unknown as monaco.Uri)).rejects.toSatisfy(
        (err: unknown) => {
          expect(err).toBeInstanceOf(FileSystemProviderError);
          const fsErr = err as FileSystemProviderError;
          expect(fsErr.code).toBe(FileSystemProviderErrorCode.FileNotFound);
          expect(fsErr.message).toContain(`File not found: ${uri.fsPath}`);
          return true;
        },
      );
    });
  });

  // DiskFileSystemProvider.stat()
  describe("3. DiskFileSystemProvider.stat()", () => {
    it("throws FileNotFound for non-file scheme URIs", async () => {
      const vfsUri = FakeUri.parse("vscode-vfs://workspace/test.py") as unknown as monaco.Uri;
      await expect(provider.stat(vfsUri)).rejects.toSatisfy((err: unknown) => {
        expect(err).toBeInstanceOf(FileSystemProviderError);
        const fsErr = err as FileSystemProviderError;
        expect(fsErr.code).toBe(FileSystemProviderErrorCode.FileNotFound);
        expect(fsErr.message).toContain("Unsupported scheme: vscode-vfs");
        return true;
      });
    });

    it("returns stat for in-memory model without calling BridgeService.statFile", async () => {
      const uriStr = "file:///C:/workspace/active_script.py";
      const uri = FakeUri.parse(uriStr);
      const modelContent = "def main():\n    return 42\n";
      const model = new FakeTextModel(modelContent, uri);
      modelsMap.set(uriStr, model);
      mockModelsList.push(model);

      const statFileSpy = vi.spyOn(BridgeService, "statFile");

      const before = Date.now();
      const stat = await provider.stat(uri as unknown as monaco.Uri);
      const after = Date.now();

      expect(statFileSpy).not.toHaveBeenCalled();
      expect(stat.type).toBe(FileType.File);
      expect(stat.size).toBe(modelContent.length);
      expect(stat.permissions).toBe(FilePermission.Readonly);
      expect(stat.ctime).toBe(0);
      expect(stat.mtime).toBeGreaterThanOrEqual(before);
      expect(stat.mtime).toBeLessThanOrEqual(after);
    });

    it("queries BridgeService.statFile when no model exists in memory (file)", async () => {
      const uriStr = "file:///C:/workspace/disk_file.py";
      const uri = FakeUri.parse(uriStr);

      const expectedResponse: FileStatResponse = {
        type: 1, // File
        ctime: 1700000000000,
        mtime: 1700000050000,
        size: 2048,
      };

      const statFileSpy = vi
        .spyOn(BridgeService, "statFile")
        .mockReturnValue(ResultAsync.fromResult(Result.ok(expectedResponse)));

      const stat = await provider.stat(uri as unknown as monaco.Uri);

      expect(statFileSpy).toHaveBeenCalledOnce();
      expect(statFileSpy).toHaveBeenCalledWith(uri.fsPath);
      expect(stat.type).toBe(FileType.File);
      expect(stat.ctime).toBe(expectedResponse.ctime);
      expect(stat.mtime).toBe(expectedResponse.mtime);
      expect(stat.size).toBe(expectedResponse.size);
      expect(stat.permissions).toBe(FilePermission.Readonly);
    });

    it("queries BridgeService.statFile and maps directory type (type 2) to FileType.Directory", async () => {
      const uriStr = "file:///C:/workspace/scripts_folder";
      const uri = FakeUri.parse(uriStr);

      const expectedResponse: FileStatResponse = {
        type: 2, // Directory
        ctime: 1700000000000,
        mtime: 1700000010000,
        size: 0,
      };

      vi.spyOn(BridgeService, "statFile").mockReturnValue(
        ResultAsync.fromResult(Result.ok(expectedResponse)),
      );

      const stat = await provider.stat(uri as unknown as monaco.Uri);

      expect(stat.type).toBe(FileType.Directory);
      expect(stat.ctime).toBe(expectedResponse.ctime);
      expect(stat.mtime).toBe(expectedResponse.mtime);
      expect(stat.size).toBe(0);
      expect(stat.permissions).toBe(FilePermission.Readonly);
    });

    it("throws FileNotFound when BridgeService.statFile returns an error", async () => {
      const uriStr = "file:///C:/workspace/nonexistent_file.py";
      const uri = FakeUri.parse(uriStr);

      vi.spyOn(BridgeService, "statFile").mockReturnValue(
        ResultAsync.fromResult(Result.err(new Error("File not found"))),
      );

      await expect(provider.stat(uri as unknown as monaco.Uri)).rejects.toSatisfy(
        (err: unknown) => {
          expect(err).toBeInstanceOf(FileSystemProviderError);
          const fsErr = err as FileSystemProviderError;
          expect(fsErr.code).toBe(FileSystemProviderErrorCode.FileNotFound);
          expect(fsErr.message).toContain(`File not found: ${uri.fsPath}`);
          return true;
        },
      );
    });
  });

  // findExistingModel()
  describe("4. findExistingModel()", () => {
    it("finds model by direct exact URI match", () => {
      const uriStr = "file:///C:/workspace/direct_match.py";
      const uri = FakeUri.parse(uriStr);
      const model = new FakeTextModel("direct content", uri);
      modelsMap.set(uriStr, model);
      mockModelsList.push(model);

      const found = findExistingModel(uri as unknown as monaco.Uri);
      expect(found).toBe(model as unknown as monaco.editor.ITextModel);
      expect(found?.getValue()).toBe("direct content");
    });

    it("matches by isSameResource across Windows case-insensitivity variations", () => {
      const originalUriStr = "file:///C:/Workspace/CaseTest.py";
      const originalUri = FakeUri.parse(originalUriStr);
      const model = new FakeTextModel("case insensitive content", originalUri);
      mockModelsList.push(model);
      // modelsMap has original casing only; direct getModel with lowercased URI returns undefined
      modelsMap.set(originalUriStr, model);

      const queryUri = FakeUri.parse("file:///c:/workspace/casetest.py");
      const found = findExistingModel(queryUri as unknown as monaco.Uri);

      expect(found).toBe(model as unknown as monaco.editor.ITextModel);
      expect(found?.getValue()).toBe("case insensitive content");
    });

    it("matches by isSameResource across percent-encoded colon variations", () => {
      const encodedUriStr = "file:///c%3a/workspace/encoded.py";
      const encodedUri = FakeUri.parse(encodedUriStr);
      const model = new FakeTextModel("encoded colon content", encodedUri);
      mockModelsList.push(model);
      modelsMap.set(encodedUriStr, model);

      const unencodedQueryUri = FakeUri.parse("file:///c:/workspace/encoded.py");
      const found = findExistingModel(unencodedQueryUri as unknown as monaco.Uri);

      expect(found).toBe(model as unknown as monaco.editor.ITextModel);
      expect(found?.getValue()).toBe("encoded colon content");
    });

    it("returns undefined if direct model is disposed and no fallback matches", () => {
      const uriStr = "file:///C:/workspace/disposed.py";
      const uri = FakeUri.parse(uriStr);
      const model = new FakeTextModel("disposed content", uri);
      model.dispose();
      modelsMap.set(uriStr, model);
      mockModelsList.push(model);

      const found = findExistingModel(uri as unknown as monaco.Uri);
      expect(found).toBeUndefined();
    });

    it("returns undefined if model in getModels() list is disposed", () => {
      const originalUri = FakeUri.parse("file:///C:/Workspace/disposed_list.py");
      const model = new FakeTextModel("disposed in list", originalUri);
      mockModelsList.push(model);
      model.dispose();

      const queryUri = FakeUri.parse("file:///c:/workspace/disposed_list.py");
      const found = findExistingModel(queryUri as unknown as monaco.Uri);
      expect(found).toBeUndefined();
    });

    it("returns undefined when no model exists for URI", () => {
      const queryUri = FakeUri.parse("file:///C:/workspace/unknown.py");
      const found = findExistingModel(queryUri as unknown as monaco.Uri);
      expect(found).toBeUndefined();
    });
  });
});
