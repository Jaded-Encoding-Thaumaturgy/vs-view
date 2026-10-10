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
import * as config from "../editor/config";
import { Result } from "../utils/result";
import { GITHUB_THEMES } from "../utils/theme";
import { DiskFileSystemProvider } from "./fs";

export { DiskFileSystemProvider } from "./fs";
export { findExistingModel } from "./models";

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
      version: "0",
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
