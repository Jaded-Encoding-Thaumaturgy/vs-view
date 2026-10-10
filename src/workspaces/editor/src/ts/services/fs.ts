import { Event } from "@codingame/monaco-vscode-api/vscode/vs/base/common/event";
import { type IDisposable } from "@codingame/monaco-vscode-api/vscode/vs/base/common/lifecycle";
import { isLinux } from "@codingame/monaco-vscode-api/vscode/vs/base/common/platform";
import {
  FilePermission,
  FileSystemProviderCapabilities,
  FileSystemProviderError,
  FileSystemProviderErrorCode,
  FileType,
  type IFileDeleteOptions,
  type IFileOverwriteOptions,
  type IFileSystemProviderWithFileReadWriteCapability,
  type IFileWriteOptions,
  type IStat,
  type IWatchOptions,
} from "@codingame/monaco-vscode-api/vscode/vs/platform/files/common/files";
import type * as monaco from "monaco-editor";

import { BridgeService } from "../bridge/python";
import { findExistingModel } from "./models";

export class DiskFileSystemProvider implements IFileSystemProviderWithFileReadWriteCapability {
  public readonly capabilities =
    FileSystemProviderCapabilities.FileReadWrite |
    (isLinux ? FileSystemProviderCapabilities.PathCaseSensitive : 0);

  public readonly onDidChangeCapabilities = Event.None;
  public readonly onDidChangeFile = Event.None;

  public async copy(
    _from: monaco.Uri,
    _to: monaco.Uri,
    _opts: IFileOverwriteOptions,
  ): Promise<void> {
    throw FileSystemProviderError.create(
      "Readonly file system",
      FileSystemProviderErrorCode.NoPermissions,
    );
  }

  public async createDirectory(_resource: monaco.Uri): Promise<void> {
    throw FileSystemProviderError.create(
      "Readonly file system",
      FileSystemProviderErrorCode.NoPermissions,
    );
  }

  public async delete(_resource: monaco.Uri, _opts: IFileDeleteOptions): Promise<void> {
    throw FileSystemProviderError.create(
      "Readonly file system",
      FileSystemProviderErrorCode.NoPermissions,
    );
  }

  public async mkdir(_resource: monaco.Uri): Promise<void> {
    throw FileSystemProviderError.create(
      "Readonly file system",
      FileSystemProviderErrorCode.NoPermissions,
    );
  }

  public async readdir(_resource: monaco.Uri): Promise<[string, FileType][]> {
    return [];
  }

  public async readFile(resource: monaco.Uri): Promise<Uint8Array> {
    if (resource.scheme !== "file") {
      throw FileSystemProviderError.create(
        `Unsupported scheme: ${resource.scheme}`,
        FileSystemProviderErrorCode.FileNotFound,
      );
    }

    const existingModel = findExistingModel(resource);
    if (existingModel && !existingModel.isDisposed()) {
      return new TextEncoder().encode(existingModel.getValue());
    }

    const readResult = await BridgeService.readFile(resource.fsPath);
    if (readResult.ok) {
      return new TextEncoder().encode(readResult.value);
    }

    throw FileSystemProviderError.create(
      `File not found: ${resource.fsPath}`,
      FileSystemProviderErrorCode.FileNotFound,
    );
  }

  public async rename(
    _from: monaco.Uri,
    _to: monaco.Uri,
    _opts: IFileOverwriteOptions,
  ): Promise<void> {
    throw FileSystemProviderError.create(
      "Readonly file system",
      FileSystemProviderErrorCode.NoPermissions,
    );
  }

  public async stat(resource: monaco.Uri): Promise<IStat> {
    if (resource.scheme !== "file") {
      throw FileSystemProviderError.create(
        `Unsupported scheme: ${resource.scheme}`,
        FileSystemProviderErrorCode.FileNotFound,
      );
    }

    const existingModel = findExistingModel(resource);
    if (existingModel && !existingModel.isDisposed()) {
      return {
        type: FileType.File,
        ctime: 0,
        mtime: Date.now(),
        size: existingModel.getValueLength(),
        permissions: FilePermission.Readonly,
      };
    }

    const statResult = await BridgeService.statFile(resource.fsPath);
    if (statResult.ok) {
      return {
        type: statResult.value.type === 2 ? FileType.Directory : FileType.File,
        ctime: statResult.value.ctime,
        mtime: statResult.value.mtime,
        size: statResult.value.size,
        permissions: FilePermission.Readonly,
      };
    }

    throw FileSystemProviderError.create(
      `File not found: ${resource.fsPath}`,
      FileSystemProviderErrorCode.FileNotFound,
    );
  }

  public watch(_resource: monaco.Uri, _opts: IWatchOptions): IDisposable {
    return { dispose: () => {} };
  }

  public async writeFile(
    _resource: monaco.Uri,
    _content: Uint8Array,
    _opts: IFileWriteOptions,
  ): Promise<void> {
    throw FileSystemProviderError.create(
      "Readonly file system",
      FileSystemProviderErrorCode.NoPermissions,
    );
  }
}
