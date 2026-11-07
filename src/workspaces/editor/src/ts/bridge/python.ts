import * as vscode from 'vscode';
import { Result, ResultAsync, WebChannelBridge } from 'vsview-shared';

import type { EditorService } from '../editor/service';
import type { LspService } from '../lsp/service';
import type { FileStatResponse, JsToPyCommandMap, PyToJsCommandMap, PythonBridge } from '../types';
import type { ConsolePanelService } from '../ui/console';
import { DOM_IDS } from '../ui/constants';

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
    return ResultAsync.fromResult(BridgeService.active).andThen((svc) =>
      svc.invoke((b, cb) => b.readFile(filePath, cb), `Failed to read file: ${filePath}`),
    );
  }

  /** Query file metadata from host via Python bridge. */
  public static statFile(filePath: string): ResultAsync<FileStatResponse> {
    return ResultAsync.fromResult(BridgeService.active).andThen((svc) =>
      svc.invoke((b, cb) => b.statFile(filePath, cb), `File not found: ${filePath}`),
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
    if (this.loadingOverlayTimer !== null) {
      clearTimeout(this.loadingOverlayTimer);
      this.loadingOverlayTimer = null;
    }
    super.dispose();
  }

  protected override onBridgeReady(pyBridge: PythonBridge): void {
    this.onDidReadyEmitter.fire(pyBridge);
    this.removeLoadingOverlayWithDelay();
  }

  protected override onBridgeError(): void {
    this.removeLoadingOverlayWithDelay();
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

          void this.lspService.connect({
            id: p.id,
            name: p.name,
            port: p.port,
            language: p.language,
            fileEventsPattern:
              typeof p.fileEventsPattern === 'string' ? p.fileEventsPattern : undefined,
            configurationSection,
            progressNotifications,
          });
        }
      }),
    );
    this._disposables.add(
      this.onCommand('lsp.disconnect', (p) => {
        const id = typeof p.id === 'string' ? p.id : undefined;
        void this.lspService.disconnect(id);
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
          void Result.fromThrowable(() => JSON.parse(p.optionsJson) as unknown).tap((parsed) => {
            if (parsed && typeof parsed === 'object') {
              this.editorService.updateOptionsFromMap(parsed);
            }
          });
        }
      }),
    );
    this._disposables.add(
      this.onCommand('editor.openTab', (p) => {
        if (typeof p.uri === 'string' && typeof p.content === 'string') {
          const language = typeof p.language === 'string' ? p.language : 'python';
          const isMain = typeof p.isMain === 'boolean' ? p.isMain : false;
          const replaceUri = typeof p.replaceUri === 'string' ? p.replaceUri : undefined;
          this.editorService.openTab(p.uri, p.content, language, isMain, replaceUri);
        }
      }),
    );
    this._disposables.add(
      this.onCommand('editor.selectTab', (p) => {
        if (typeof p.uri === 'string') this.editorService.selectTab(p.uri);
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
        this.flushState();
        this.editorService.requestSave();
      }),
    );
    this._disposables.add(
      this.onCommand('editor.triggerSaveAs', () => {
        this.flushState();
        this.editorService.requestSaveAs();
      }),
    );
    this._disposables.add(
      this.onCommand('editor.triggerFormat', () => {
        this.flushState();
        this.editorService.requestFormat();
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
        if (this.bridge) {
          this.sendEvent('editor.saveRequested');
        }
      }),
    );
    this._disposables.add(
      this.editorService.onDidRequestSaveAs(() => {
        if (this.bridge) {
          this.sendEvent('editor.saveAsRequested');
        }
      }),
    );
    this._disposables.add(
      this.editorService.onDidRequestFormat(() => {
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
