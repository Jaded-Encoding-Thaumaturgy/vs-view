import * as vscode from "vscode";

import type { EditorService } from "../editor/service";
import type { LspService } from "../lsp/service";
import type { PythonBridge, QWebSignal } from "../types";
import { ConsolePanelService } from "../ui/console";
import { DOM_IDS } from "../ui/constants";
import { DisposableStore, toDisposable } from "../utils/disposables";
import { Result } from "../utils/result";

export class BridgeService implements vscode.Disposable {
  private static activeInstance: BridgeService | null = null;

  public readonly onDidReady: vscode.Event<PythonBridge>;

  private readonly disposables = new DisposableStore();
  private readonly onDidReadyEmitter = new vscode.EventEmitter<PythonBridge>();

  private bridge: PythonBridge | null = null;
  private debounceTimer: ReturnType<typeof setTimeout> | null = null;
  private loadingOverlayTimer: ReturnType<typeof setTimeout> | null = null;

  public static getActiveBridge(): Result<PythonBridge> {
    if (BridgeService.activeInstance) {
      return BridgeService.activeInstance.getBridge();
    }
    return Result.err(new Error("BridgeService instance is not available"));
  }

  constructor(
    private readonly editorService: EditorService,
    private readonly lspService: LspService,
    private readonly consoleService: ConsolePanelService,
  ) {
    BridgeService.activeInstance = this;
    this.onDidReady = this.onDidReadyEmitter.event;
    this.disposables.add(this.onDidReadyEmitter);
    this.setupConsoleListeners();
    this.setupEditorListeners();
  }

  public initBridge(): void {
    new window.QWebChannel(window.qt.webChannelTransport, (channel) => {
      const pyBridge = channel.objects["bridge"] as PythonBridge;

      if (!pyBridge) {
        console.error("Bridge object not found in channel.");
        this.removeLoadingOverlayWithDelay();
        return;
      }

      this.bridge = pyBridge;
      // Connect Python signals to editor/LSP methods
      this.connectSignals(pyBridge);
      // Fire ready event to listeners
      this.onDidReadyEmitter.fire(pyBridge);
      // Notify Python that JS editor is ready
      pyBridge.onEditorReady();
      // Delay hiding the loading screen to allow Monaco to render its first frames
      this.removeLoadingOverlayWithDelay();
    });
  }

  public getBridge(): Result<PythonBridge> {
    if (this.bridge) {
      return Result.ok(this.bridge);
    }
    return Result.err(new Error("Python bridge is not initialized"));
  }

  public dispose(): void {
    if (BridgeService.activeInstance === this) {
      BridgeService.activeInstance = null;
    }
    if (this.debounceTimer !== null) {
      clearTimeout(this.debounceTimer);
      this.debounceTimer = null;
    }
    if (this.loadingOverlayTimer !== null) {
      clearTimeout(this.loadingOverlayTimer);
      this.loadingOverlayTimer = null;
    }
    this.disposables.dispose();
  }

  public [Symbol.dispose](): void {
    this.dispose();
  }

  private setupConsoleListeners(): void {
    this.disposables.add(
      this.consoleService.onDidToggle(() => {
        this.editorService.layout();
      }),
    );

    this.disposables.add(
      this.consoleService.onDidResize(() => {
        this.editorService.layout();
      }),
    );
  }

  private setupEditorListeners(): void {
    this.disposables.add(
      this.editorService.onDidChangeContent(() => {
        this.notifyContentChanged();
      }),
    );

    this.disposables.add(
      this.editorService.onDidChangeCursorPosition((e) => {
        if (this.bridge) {
          this.bridge.onCursorPositionChanged(e.position.lineNumber, e.position.column);
        }
      }),
    );

    this.disposables.add(
      this.editorService.onDidChangeActiveTab((uri) => {
        if (this.bridge) {
          this.bridge.onActiveTabChanged(uri || "");
          this.flushContentChanged();
        }
      }),
    );

    this.disposables.add(
      this.editorService.onDidChangeMainTab((uri) => {
        if (this.bridge) {
          this.bridge.onMainTabChanged(uri || "");
          this.flushContentChanged();
        }
      }),
    );

    this.disposables.add(
      this.editorService.onDidRequestSave(() => {
        if (this.bridge) {
          this.bridge.requestSave();
        }
      }),
    );

    this.disposables.add(
      this.editorService.onDidRequestSaveAs(() => {
        if (this.bridge) {
          this.bridge.requestSaveAs();
        }
      }),
    );

    this.disposables.add(
      this.editorService.onDidRequestFormat(() => {
        if (this.bridge) {
          this.bridge.requestFormat();
        }
      }),
    );
  }

  private removeLoadingOverlay(): void {
    const overlay = document.getElementById(DOM_IDS.LOADING_OVERLAY)!;
    overlay.addEventListener("transitionend", () => overlay.remove(), { once: true });
    overlay.classList.add("fade-out");
  }

  private removeLoadingOverlayWithDelay(delay: number = 200): void {
    if (this.loadingOverlayTimer !== null) {
      clearTimeout(this.loadingOverlayTimer);
    }
    this.loadingOverlayTimer = setTimeout(() => {
      this.loadingOverlayTimer = null;
      this.removeLoadingOverlay();
    }, delay);
  }

  private notifyContentChanged(): void {
    if (this.debounceTimer !== null) {
      clearTimeout(this.debounceTimer);
    }
    this.debounceTimer = setTimeout(() => this.flushContentChanged(), 300);
  }

  private flushContentChanged(): void {
    if (this.debounceTimer !== null) {
      clearTimeout(this.debounceTimer);
      this.debounceTimer = null;
    }
    this.bridge?.onContentChanged(this.editorService.getValue());
    this.bridge?.onMainContentChanged(this.editorService.getMainValue());
  }

  private bindSignal<T extends (...args: never[]) => void>(
    signal: QWebSignal<T>,
    handler: T,
  ): void {
    signal.connect(handler);
    this.disposables.add(toDisposable(() => signal.disconnect(handler)));
  }

  private connectSignals(pyBridge: PythonBridge): void {
    this.bindSignal(pyBridge.dispatchCommandSignal, (command: string, payloadJson: string) => {
      if (typeof command === "string") {
        let payload: Record<string, unknown> = {};
        if (payloadJson) {
          const parsed = Result.fromThrowable(() => JSON.parse(payloadJson));
          payload = Result.match(parsed, {
            ok: (val) =>
              typeof val === "object" && val !== null ? (val as Record<string, unknown>) : {},
            err: (e) => {
              console.error("Error parsing the JSON", e);
              return {};
            },
          });
        }
        this.handleCommand(command, payload);
      }
    });
  }

  private handleCommand(command: string, payload: Record<string, unknown>): void {
    const handlers: Record<string, (p: Record<string, unknown>) => void> = {
      "editor.setValue": (p) => {
        if (typeof p.text === "string") this.editorService.setValue(p.text);
      },
      "editor.setTheme": (p) => {
        if (typeof p.theme === "string") {
          this.editorService.setTheme(p.theme);
          this.consoleService.setTheme(p.theme);
        }
      },
      "console.append": (p) => {
        if (typeof p.text === "string") this.consoleService.append(p.text);
      },
      "console.toggle": (p) => {
        const forceState = typeof p.state === "boolean" ? p.state : undefined;
        this.consoleService.toggle(forceState);
      },
      "console.clear": () => this.consoleService.clear(),
      "editor.setFontSize": (p) => {
        if (typeof p.size === "number") this.editorService.setFontSize(p.size);
      },
      "editor.setLanguage": (p) => {
        if (typeof p.lang === "string") this.editorService.setLanguage(p.lang);
      },
      "editor.toggleWordWrap": () => this.editorService.toggleWordWrap(),
      "lsp.connect": (p) => {
        if (
          typeof p.id === "string" &&
          typeof p.name === "string" &&
          typeof p.port === "number" &&
          typeof p.language === "string"
        ) {
          const configurationSection =
            typeof p.configurationSection === "string"
              ? p.configurationSection
              : Array.isArray(p.configurationSection) &&
                  p.configurationSection.every((item) => typeof item === "string")
                ? (p.configurationSection as string[])
                : undefined;

          void this.lspService.connect({
            id: p.id,
            name: p.name,
            port: p.port,
            language: p.language,
            fileEventsPattern:
              typeof p.fileEventsPattern === "string" ? p.fileEventsPattern : undefined,
            configurationSection,
          });
        }
      },
      "lsp.disconnect": (p) => {
        const id = typeof p.id === "string" ? p.id : undefined;
        void this.lspService.disconnect(id);
      },
      "editor.updateLspSettings": (p) => {
        if (typeof p.section === "string" && typeof p.settingsJson === "string") {
          void this.applyLspSettings(p.section, p.settingsJson);
        }
      },
      "editor.updateOptions": (p) => {
        if (typeof p.optionsJson === "string") {
          const parsed = Result.fromThrowable(() => JSON.parse(p.optionsJson as string));
          if (parsed.ok && typeof parsed.value === "object" && parsed.value !== null) {
            this.editorService.updateOptionsFromMap(parsed.value as Record<string, unknown>);
          }
        }
      },
      "editor.openTab": (p) => {
        if (typeof p.uri === "string" && typeof p.content === "string") {
          const language = typeof p.language === "string" ? p.language : "python";
          const isMain = typeof p.isMain === "boolean" ? p.isMain : false;
          this.editorService.openTab(p.uri, p.content, language, isMain);
        }
      },
      "editor.closeTab": (p) => {
        if (typeof p.uri === "string") this.editorService.closeTab(p.uri);
      },
      "editor.selectTab": (p) => {
        if (typeof p.uri === "string") this.editorService.selectTab(p.uri);
      },
      "editor.setMainTab": (p) => {
        if (typeof p.uri === "string") {
          this.editorService.setMainTab(p.uri);
          this.flushContentChanged();
        }
      },
      "editor.tabSaved": (p) => {
        if (typeof p.uri === "string") {
          const oldUri = typeof p.oldUri === "string" ? p.oldUri : undefined;
          this.editorService.markTabSaved(p.uri, oldUri);
        }
      },
      "editor.triggerSave": () => {
        this.flushContentChanged();
        this.editorService.requestSave();
      },
      "editor.triggerSaveAs": () => {
        this.flushContentChanged();
        this.editorService.requestSaveAs();
      },
      "editor.triggerFormat": () => {
        this.flushContentChanged();
        this.editorService.requestFormat();
      },
    };

    const handler = handlers[command];
    if (handler) {
      handler(payload);
    } else {
      console.warn(`Unknown bridge command: ${command}`);
    }
  }

  private async applyLspSettings(section: string, jsonSettings: string): Promise<void> {
    const parseResult = Result.fromThrowable(
      () => JSON.parse(jsonSettings) as Record<string, string | boolean>,
    );
    if (!parseResult.ok) {
      console.error(`Failed to parse LSP settings for section '${section}':`, parseResult.error);
      return;
    }

    const raw = parseResult.value;
    const updateResult = await Result.fromPromise(async () => {
      const conf = vscode.workspace.getConfiguration();
      for (const [key, value] of Object.entries(raw)) {
        await conf.update(key, value, vscode.ConfigurationTarget.Workspace);
      }
    });

    Result.match(updateResult, {
      ok: () => console.debug(`Updated LSP configuration for '${section}':`, JSON.stringify(raw)),
      err: (err) =>
        console.error(
          `Failed to apply LSP settings for '${section}' to workspace configuration:`,
          err,
        ),
    });
  }
}
