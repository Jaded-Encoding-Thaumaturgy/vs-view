import * as vscode from 'vscode';
import { Result, ResultAsync, WebChannelBridge } from 'vsview-shared';

import type { EditorService } from '../editor/service';
import type { LspService } from '../lsp/service';
import type { FileStatResponse, JsToPyCommandMap, PyToJsCommandMap, PythonBridge } from '../types';
import type { ConsolePanelService } from '../ui/console';
import { DOM_IDS } from '../ui/constants';
import { renderStartupError } from '../ui/layout';

export class BridgeService
  extends WebChannelBridge<PyToJsCommandMap, JsToPyCommandMap, PythonBridge>
  implements vscode.Disposable
{
  private static activeInstance: BridgeService | null = null;

  public readonly onDidReady: vscode.Event<PythonBridge>;

  private readonly onDidReadyEmitter = new vscode.EventEmitter<PythonBridge>();

  private debounceTimer: ReturnType<typeof setTimeout> | null = null;
  private loadingOverlayTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(
    private readonly editorService: EditorService,
    private readonly lspService: LspService,
    private readonly consoleService: ConsolePanelService,
  ) {
    super();
    BridgeService.activeInstance = this;
    this.onDidReady = this.onDidReadyEmitter.event;
    this._disposables.add(this.onDidReadyEmitter);
    this.setupCommandHandlers();
    this.setupConsoleListeners();
    this.setupEditorListeners();
  }

  public static get active(): Result<BridgeService> {
    const instance = BridgeService.activeInstance;
    return instance?.bridge
      ? Result.ok(instance)
      : Result.err(new Error('BridgeService instance is not available'));
  }

  /** Read file content from host via Python bridge. */
  public static readFile(filePath: string): ResultAsync<string> {
    const errorMsg = `Failed to read file: ${filePath}`;
    return ResultAsync.fromResult(BridgeService.active)
      .andThen((svc) => svc.invoke<unknown>((b, cb) => b.readFile(filePath, cb), errorMsg))
      .andThen((res) =>
        isRecord(res) && res.ok === true && typeof res.content === 'string'
          ? Result.ok(res.content)
          : Result.err(new Error(errorMsg)),
      );
  }

  /** Query file metadata from host via Python bridge. */
  public static statFile(filePath: string): ResultAsync<FileStatResponse> {
    const errorMsg = `File not found: ${filePath}`;
    return ResultAsync.fromResult(BridgeService.active)
      .andThen((svc) => svc.invoke<unknown>((b, cb) => b.statFile(filePath, cb), errorMsg))
      .andThen((res) =>
        isRecord(res) &&
        res.ok === true &&
        typeof res.type === 'number' &&
        typeof res.ctime === 'number' &&
        typeof res.mtime === 'number' &&
        typeof res.size === 'number'
          ? Result.ok({ type: res.type, ctime: res.ctime, mtime: res.mtime, size: res.size })
          : Result.err(new Error(errorMsg)),
      );
  }

  public flushState(): void {
    this.flushContentChanged();
  }

  public override dispose(): void {
    if (BridgeService.activeInstance === this) {
      BridgeService.activeInstance = null;
    }
    if (this.debounceTimer !== null) {
      clearTimeout(this.debounceTimer);
      this.debounceTimer = null;
    }
    this.cancelLoadingOverlayRemoval();
    super.dispose();
  }

  protected override onBridgeReady(pyBridge: PythonBridge): void {
    this.onDidReadyEmitter.fire(pyBridge);
    this.removeLoadingOverlayWithDelay();
  }

  protected override onBridgeError(err: Error): void {
    if (window.ENV?.VSVIEW) {
      this.cancelLoadingOverlayRemoval();
      renderStartupError('The editor could not connect to vsview.', err);
    } else {
      this.removeLoadingOverlayWithDelay();
    }
  }

  private setupCommandHandlers(): void {
    this._disposables.add(
      this.onCommand('editor.setValue', (p) => {
        if (typeof p.text === 'string') this.editorService.setValue(p.text);
      }),
    );
    this._disposables.add(
      this.onCommand('editor.setTheme', (p) => {
        if (typeof p.theme === 'string') {
          this.editorService.setTheme(p.theme);
          this.consoleService.setTheme(p.theme);
        }
      }),
    );
    this._disposables.add(
      this.onCommand('console.append', (p) => {
        if (typeof p.text === 'string') this.consoleService.append(p.text);
      }),
    );
    this._disposables.add(
      this.onCommand('console.toggle', (p) => {
        const forceState = typeof p.state === 'boolean' ? p.state : undefined;
        this.consoleService.toggle(forceState);
      }),
    );
    this._disposables.add(
      this.onCommand('editor.toggleWordWrap', () => {
        this.editorService.toggleWordWrap();
      }),
    );
    this._disposables.add(
      this.onCommand('lsp.connect', (p) => {
        if (
          typeof p.id === 'string' &&
          typeof p.name === 'string' &&
          typeof p.port === 'number' &&
          typeof p.language === 'string'
        ) {
          const configurationSection =
            typeof p.configurationSection === 'string'
              ? p.configurationSection
              : Array.isArray(p.configurationSection) &&
                  p.configurationSection.every((item) => typeof item === 'string')
                ? p.configurationSection
                : undefined;

          const progressNotifications = Array.isArray(p.progressNotifications)
            ? (p.progressNotifications as Array<{ begin?: unknown; end?: unknown }>)
                .filter(
                  (pair): pair is { begin: string; end: string } =>
                    typeof pair.begin === 'string' && typeof pair.end === 'string',
                )
                .map((pair) => ({ begin: pair.begin, end: pair.end }))
            : undefined;

          const config = {
            id: p.id,
            name: p.name,
            port: p.port,
            path: p.path,
            language: p.language,
            fileEventsPattern:
              typeof p.fileEventsPattern === 'string' ? p.fileEventsPattern : undefined,
            configurationSection,
            progressNotifications,
          };
          void Result.fromPromise(() => this.lspService.connect(config)).mapErr((err) =>
            console.error(`Failed to connect language server '${config.id}':`, err),
          );
        }
      }),
    );
    this._disposables.add(
      this.onCommand('lsp.disconnect', (p) => {
        const id = typeof p.id === 'string' ? p.id : undefined;
        void Result.fromPromise(() => this.lspService.disconnect(id)).mapErr((err) =>
          console.error(`Failed to disconnect language server '${id ?? 'all'}':`, err),
        );
      }),
    );
    this._disposables.add(
      this.onCommand('editor.updateLspSettings', (p) => {
        if (typeof p.section === 'string' && typeof p.settingsJson === 'string') {
          void this.applyLspSettings(p.section, p.settingsJson);
        }
      }),
    );
    this._disposables.add(
      this.onCommand('editor.updateOptions', (p) => {
        if (typeof p.optionsJson === 'string') {
          Result.fromThrowable(() => JSON.parse(p.optionsJson) as unknown).match({
            ok: (parsed) => {
              if (parsed && typeof parsed === 'object') {
                this.editorService.updateOptionsFromMap(parsed);
              }
            },
            err: (err) => console.error('Failed to parse editor options:', err),
          });
        }
      }),
    );
    this._disposables.add(
      this.onCommand('editor.openTab', (p) => {
        if (typeof p.uri === 'string' && typeof p.content === 'string') {
          const uri = p.uri;
          const language = typeof p.language === 'string' ? p.language : 'python';
          const isMain = typeof p.isMain === 'boolean' ? p.isMain : false;
          const replaceUri = typeof p.replaceUri === 'string' ? p.replaceUri : undefined;
          this.editorService
            .openTab(uri, p.content, language, isMain, replaceUri)
            .mapErr((err) => console.error(`Failed to open tab '${uri}':`, err));
        }
      }),
    );
    this._disposables.add(
      this.onCommand('editor.selectTab', (p) => {
        if (typeof p.uri === 'string') {
          const uri = p.uri;
          this.editorService
            .selectTab(uri)
            .mapErr((err) => console.error(`Failed to select tab '${uri}':`, err));
        }
      }),
    );
    this._disposables.add(
      this.onCommand('editor.tabSaved', (p) => {
        if (typeof p.uri === 'string') {
          const oldUri = typeof p.oldUri === 'string' ? p.oldUri : undefined;
          this.editorService.markTabSaved(p.uri, oldUri);
        }
      }),
    );
    this._disposables.add(
      this.onCommand('editor.triggerSave', () => {
        this.editorService.requestSave();
      }),
    );
    this._disposables.add(
      this.onCommand('editor.triggerSaveAs', () => {
        this.editorService.requestSaveAs();
      }),
    );
    this._disposables.add(
      this.onCommand('editor.triggerFormat', () => {
        this.editorService.requestFormat();
      }),
    );
    this._disposables.add(
      this.onCommand('editor.triggerRun', () => {
        this.flushState();
        this.sendEvent('editor.runRequested');
      }),
    );
  }

  private setupConsoleListeners(): void {
    this._disposables.add(this.consoleService.onDidToggle(() => this.editorService.layout()));
    this._disposables.add(this.consoleService.onDidResize(() => this.editorService.layout()));
  }

  private setupEditorListeners(): void {
    this._disposables.add(this.editorService.onDidChangeContent(() => this.notifyContentChanged()));
    this._disposables.add(
      this.editorService.onDidChangeCursorPosition((e) => {
        if (this.bridge) {
          this.sendEvent('editor.cursorPositionChanged', {
            line: e.position.lineNumber,
            column: e.position.column,
          });
        }
      }),
    );
    this._disposables.add(
      this.editorService.onDidChangeActiveTab((uri) => {
        if (this.bridge) {
          this.sendEvent('editor.activeTabChanged', { uri: uri ?? '' });
        }
        this.flushContentChanged();
      }),
    );
    this._disposables.add(
      this.editorService.onDidChangeMainTab((uri) => {
        if (this.bridge) {
          this.sendEvent('editor.mainTabChanged', { uri: uri ?? '' });
        }
        this.flushContentChanged();
      }),
    );
    this._disposables.add(
      this.editorService.onDidRequestSave(() => {
        this.flushState();
        if (this.bridge) {
          this.sendEvent('editor.saveRequested');
        }
      }),
    );
    this._disposables.add(
      this.editorService.onDidRequestSaveAs(() => {
        this.flushState();
        if (this.bridge) {
          this.sendEvent('editor.saveAsRequested');
        }
      }),
    );
    this._disposables.add(
      this.editorService.onDidRequestFormat(() => {
        this.flushState();
        if (this.bridge) {
          this.sendEvent('editor.formatRequested');
        }
      }),
    );
  }

  private removeLoadingOverlay(): void {
    const overlay = document.getElementById(DOM_IDS.LOADING_OVERLAY)!;
    overlay.addEventListener('transitionend', () => overlay.remove(), { once: true });
    overlay.classList.add('fade-out');
  }

  private cancelLoadingOverlayRemoval(): void {
    if (this.loadingOverlayTimer !== null) {
      clearTimeout(this.loadingOverlayTimer);
      this.loadingOverlayTimer = null;
    }
  }

  private removeLoadingOverlayWithDelay(delay: number = 200): void {
    this.cancelLoadingOverlayRemoval();
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
    if (this.bridge) {
      this.sendEvent('editor.contentChanged', { content: this.editorService.getValue() });
      this.sendEvent('editor.mainContentChanged', { content: this.editorService.getMainValue() });
      this.sendEvent('editor.sessionStateChanged', { tabs: this.editorService.getSessionState() });
    }
    this.editorService.updateTabBar();
  }

  private async applyLspSettings(section: string, jsonSettings: string): Promise<void> {
    const parseResult = Result.fromThrowable(
      () => JSON.parse(jsonSettings) as Record<string, unknown>,
    );
    if (!parseResult.ok) {
      console.error(`Failed to parse LSP settings for section '${section}':`, parseResult.error);
      return;
    }

    const parsed = parseResult.value;

    const conf = vscode.workspace.getConfiguration();
    const updateResult = await Result.fromPromise(
      Promise.all(
        Object.entries(parsed).map(([key, value]) =>
          conf.update(key, value, vscode.ConfigurationTarget.Workspace),
        ),
      ),
    );

    updateResult.match({
      ok: () =>
        console.debug(`Updated LSP configuration for '${section}':`, JSON.stringify(parsed)),
      err: (err) =>
        console.error(
          `Failed to apply LSP settings for '${section}' to workspace configuration:`,
          err,
        ),
    });
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}
