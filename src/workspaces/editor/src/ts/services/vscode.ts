import {
  ExtensionHostKind,
  type RegisterLocalProcessExtensionResult,
  registerExtension,
} from "@codingame/monaco-vscode-api/extensions";
import { waitServicesReady } from "@codingame/monaco-vscode-api/lifecycle";
import {
  IMarkdownRendererService,
  initialize as initializeServices,
} from "@codingame/monaco-vscode-api/services";
import { Emitter } from "@codingame/monaco-vscode-api/vscode/vs/base/common/event";
import { type IDisposable } from "@codingame/monaco-vscode-api/vscode/vs/base/common/lifecycle";
import {
  FilePermission,
  FileSystemProviderCapabilities,
  FileSystemProviderError,
  FileSystemProviderErrorCode,
  FileType,
  type IFileChange,
  type IFileDeleteOptions,
  type IFileOverwriteOptions,
  type IFileSystemProviderWithFileReadWriteCapability,
  type IFileWriteOptions,
  type IStat,
  type IWatchOptions,
} from "@codingame/monaco-vscode-api/vscode/vs/platform/files/common/files";
import { SyncDescriptor } from "@codingame/monaco-vscode-api/vscode/vs/platform/instantiation/common/descriptors";
import { MarkdownRendererService } from "@codingame/monaco-vscode-api/vscode/vs/platform/markdown/browser/markdownRenderer";
import getConfigurationServiceOverride from "@codingame/monaco-vscode-configuration-service-override";
import getExtensionServiceOverride from "@codingame/monaco-vscode-extensions-service-override";
import getFilesServiceOverride, {
  RegisteredFileSystemProvider,
  RegisteredMemoryFile,
  registerFileSystemOverlay,
} from "@codingame/monaco-vscode-files-service-override";
import getLanguagesServiceOverride from "@codingame/monaco-vscode-languages-service-override";
import getLogServiceOverride from "@codingame/monaco-vscode-log-service-override";
import getModelServiceOverride from "@codingame/monaco-vscode-model-service-override";
import { whenReady as whenPythonExtensionReady } from "@codingame/monaco-vscode-python-default-extension";
import getTextmateServiceOverride from "@codingame/monaco-vscode-textmate-service-override";
import textmateWorker from "@codingame/monaco-vscode-textmate-service-override/worker?worker";
import { whenReady as whenThemeExtensionReady } from "@codingame/monaco-vscode-theme-defaults-default-extension";
import getThemeServiceOverride from "@codingame/monaco-vscode-theme-service-override";
import * as monaco from "monaco-editor";
import editorWorker from "monaco-editor/esm/vs/editor/editor.worker?worker";
import * as vscode from "vscode";

import githubThemeVersion from "../assets/themes/github/VERSION?raw";
import { BridgeService } from "../bridge/python";
import * as config from "../editor/config";
import type { FileStatResponse } from "../types";
import { Result } from "../utils/result";
import { GITHUB_THEMES } from "../utils/theme";

declare const __APP_VERSION__: string;

// Monaco Environment Setup
self.MonacoEnvironment = {
  getWorker(_moduleId: string, label: string): Worker {
    switch (label) {
      case "editorWorkerService":
        return new editorWorker();
      case "TextMateWorker":
        return new textmateWorker();
      default:
        throw new Error(`Unrecognized label ${label}`);
    }
  },
};

export class DiskFileSystemProvider implements IFileSystemProviderWithFileReadWriteCapability {
  public readonly capabilities =
    FileSystemProviderCapabilities.FileReadWrite | FileSystemProviderCapabilities.PathCaseSensitive;

  public readonly onDidChangeCapabilities = new Emitter<any>().event;
  public readonly onDidChangeFile = new Emitter<readonly IFileChange[]>().event;

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

    const bridgeResult = BridgeService.getActiveBridge();
    const filePath = resource.fsPath;

    if (bridgeResult.ok) {
      const readResult = await Result.fromPromise(
        new Promise<string>((resolve, reject) => {
          bridgeResult.value.readFile(filePath, (data) => {
            if (data != null) {
              resolve(data);
            } else {
              reject(new Error(`Null data for: ${filePath}`));
            }
          });
        }),
      );
      if (readResult.ok) {
        if (!monaco.editor.getModel(resource)) {
          const isPython = resource.path.endsWith(".py") || resource.path.endsWith(".pyi");
          monaco.editor.createModel(readResult.value, isPython ? "python" : undefined, resource);
        }
        return new TextEncoder().encode(readResult.value);
      }
    }

    throw FileSystemProviderError.create(
      `File not found: ${filePath}`,
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
    const bridgeResult = BridgeService.getActiveBridge();
    const filePath = resource.fsPath;

    if (bridgeResult.ok) {
      const resResult = await Result.fromPromise(
        new Promise<FileStatResponse>((resolve, reject) => {
          bridgeResult.value.statFile(filePath, (data) => {
            if (data != null) {
              resolve(data);
            } else {
              reject(new Error(`Null data for: ${filePath}`));
            }
          });
        }),
      );
      if (resResult.ok) {
        return {
          type: resResult.value.type === 2 ? FileType.Directory : FileType.File,
          ctime: resResult.value.ctime,
          mtime: resResult.value.mtime,
          size: resResult.value.size,
          permissions: FilePermission.Readonly,
        };
      }
    }

    throw FileSystemProviderError.create(
      `File not found: ${filePath}`,
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

export class VSCodeWorkspaceRegistry {
  private static fileSystemProvider: RegisteredFileSystemProvider | null = null;

  public static initialize(provider: RegisteredFileSystemProvider): void {
    this.fileSystemProvider = provider;
  }

  public static registerMemoryFile(uri: monaco.Uri, content: string): Result<monaco.IDisposable> {
    if (!this.fileSystemProvider) {
      return Result.err(new Error("VSCodeWorkspaceRegistry is not initialized"));
    }
    return Result.fromThrowable(() => {
      return this.fileSystemProvider!.registerFile(new RegisteredMemoryFile(uri, content));
    });
  }
}

/** Initialize VS Code extension host & services before Monaco creation */
export async function initVscodeServices(): Promise<Result<void>> {
  // Register extension BEFORE services initialization (when !servicesInitialized)
  const extResult = Result.fromThrowable(registerMainExtension);
  if (!extResult.ok) {
    console.error("Failed to register VS Code extension:", extResult.error);
    return extResult;
  }

  // Register Github themes extension
  const githubExtResult = Result.fromThrowable(registerGithubThemesExtension);

  if (!githubExtResult.ok) {
    console.error("Failed to register GitHub theme extension:", githubExtResult.error);
  }
  const githubExtension = githubExtResult.ok ? githubExtResult.value : null;

  // Register memory filesystem files before file service initialization
  const workspaceDirUri = monaco.Uri.parse("file:///workspace");
  const workspaceFileUri = monaco.Uri.parse("file:///workspace/workspace.code-workspace");

  const fsProvider = new RegisteredFileSystemProvider(false);
  VSCodeWorkspaceRegistry.initialize(fsProvider);
  const mkdirRes = Result.fromThrowable(() => fsProvider.mkdirSync(workspaceDirUri));
  // Ignore error if exists
  if (!mkdirRes.ok) {
    console.info("fsProvider.mkdirSync failed with the error", mkdirRes.error);
  }
  const initialTheme = new URLSearchParams(window.location.search).get("initialTheme")!;

  const fsRegResult = Result.fromThrowable(() => {
    fsProvider.registerFile(
      new RegisteredMemoryFile(
        workspaceFileUri,
        JSON.stringify({
          folders: [{ path: "." }],
          settings: { "workbench.colorTheme": initialTheme },
        }),
      ),
    );
    registerFileSystemOverlay(1, fsProvider);
    registerFileSystemOverlay(-1, new DiskFileSystemProvider());
  });

  if (!fsRegResult.ok) {
    console.error("Failed to register memory filesystem files:", fsRegResult.error);
    return fsRegResult;
  }

  // Initialize VS Code standalone services
  const initServicesResult = await Result.fromPromise(
    initializeServices(
      {
        ...getLogServiceOverride(),
        ...getConfigurationServiceOverride(),
        ...getModelServiceOverride(),
        ...getExtensionServiceOverride(),
        ...getLanguagesServiceOverride(),
        ...getTextmateServiceOverride(),
        ...getThemeServiceOverride(),
        ...getFilesServiceOverride(),
        [IMarkdownRendererService.toString()]: new SyncDescriptor(
          MarkdownRendererService,
          [],
          true,
        ),
      },
      document.body,
      {
        workspaceProvider: {
          trusted: true,
          workspace: { workspaceUri: workspaceFileUri },
          async open() {
            return true;
          },
        },
      },
    ),
  );

  if (!initServicesResult.ok) {
    console.error("Failed to initialize VS Code standalone services:", initServicesResult.error);
    return initServicesResult;
  }

  // Wait for all service participants & LocalExtensionHost to complete startup
  const waitResult = await Result.fromPromise(waitServicesReady);
  if (!waitResult.ok) {
    console.error("Failed waiting for VS Code services to be ready:", waitResult.error);
    return waitResult;
  }

  await whenPythonExtensionReady();
  await whenThemeExtensionReady();
  if (githubExtension) {
    await githubExtension.whenReady();
  }

  // Expose defaultApi proxy (vscode.*)
  const defaultApiResult = await Result.fromPromise(extResult.value.setAsDefaultApi);
  if (!defaultApiResult.ok) {
    console.error("Failed to set extension as default API:", defaultApiResult.error);
    return defaultApiResult;
  }

  console.debug(
    "VS Code Services & defaultApi fully initialized! (vscode v" + vscode.version + ")",
  );
  return Result.ok(undefined);
}

function registerMainExtension(): RegisterLocalProcessExtensionResult {
  return registerExtension(
    {
      name: "vsview-editor",
      publisher: "vsview",
      version: __APP_VERSION__,
      engines: { vscode: "*" },
      activationEvents: ["onLanguage:python"],
      main: "./extension.js",
      contributes: {
        configuration: {
          title: "Basedpyright",
          properties: config.BASED_PYRIGHT_SETTINGS_DEFS,
        },
      },
    },
    ExtensionHostKind.LocalProcess,
  );
}

function registerGithubThemesExtension(): RegisterLocalProcessExtensionResult {
  const ext = registerExtension(
    {
      name: "github-vscode-theme",
      publisher: "primer",
      version: githubThemeVersion.trim(),
      engines: { vscode: "*" },
      contributes: {
        themes: GITHUB_THEMES.map((theme) => ({
          id: theme.id,
          label: theme.id,
          uiTheme: theme.kind.uiTheme,
          path: theme.path,
        })),
      },
    },
    ExtensionHostKind.LocalProcess,
  );

  for (const theme of GITHUB_THEMES) {
    ext.registerFileUrl(theme.path, theme.url, "application/json");
  }
  return ext;
}
