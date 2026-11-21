import { updateUserConfiguration } from '@codingame/monaco-vscode-configuration-service-override';
import * as monaco from 'monaco-editor';
import * as vscode from 'vscode';
import { DisposableStore, Result, toDisposable } from 'vsview-shared';

import { BridgeService } from '../bridge/python';
import { findExistingModel } from '../services/models';
import type { EditorOptionsPayload, TabInfo, TabSnapshot } from '../types';
import { DEFAULT_SCRIPT_URI, DOM_IDS } from '../ui/constants';
import { TabBarView } from '../ui/tabbar';
import { applyDocumentTheme, getInitialTheme, getThemeDefinition } from '../utils/theme';
import { isSameResource } from '../utils/uri';
import * as config from './config';
import { minimalEdit } from './edits';

interface TabRecord {
  uri: monaco.Uri;
  uriString: string;
  title: string;
  model: monaco.editor.ITextModel;
  ownsModel: boolean;
  viewState: monaco.editor.ICodeEditorViewState | null;
  /** Added to the model's version ids, so a late reply about a replaced model never matches the new one. */
  versionBase: number;
  savedVersionId: number;
  /** The version whose text Python last received, or null before the first send. */
  sentVersionId: number | null;
  isDirty: boolean;
  isMain: boolean;
  disposables: DisposableStore;
}

export class EditorService implements vscode.Disposable {
  private readonly editor: monaco.editor.IStandaloneCodeEditor;
  private readonly tabBarView: TabBarView;
  private readonly disposables = new DisposableStore();

  private readonly tabs = new Map<string, TabRecord>();
  private readonly contentChangeEmitter = this.disposables.add(new monaco.Emitter<void>());
  private readonly activeTabChangeEmitter = this.disposables.add(
    new monaco.Emitter<string | null>(),
  );
  private readonly mainTabChangeEmitter = this.disposables.add(new monaco.Emitter<string | null>());
  private readonly saveRequestEmitter = this.disposables.add(new monaco.Emitter<void>());
  private readonly saveAsRequestEmitter = this.disposables.add(new monaco.Emitter<void>());
  private readonly formatRequestEmitter = this.disposables.add(new monaco.Emitter<void>());
  private activeUri: string | null = null;
  private mainUri: string | null = null;
  private modelOptions: monaco.editor.ITextModelUpdateOptions = {};
  private sentMain: { tab: TabRecord; versionId: number } | null = null;

  constructor() {
    const container = document.getElementById(DOM_IDS.EDITOR)!;

    this.tabBarView = this.disposables.add(
      new TabBarView({
        onSelectTab: (uri) =>
          this.selectTab(uri).mapErr((err) => console.error('Failed to select tab:', err)),
        onCloseTab: (uri) =>
          this.closeTab(uri).mapErr((err) => console.error('Failed to close tab:', err)),
        onCloseOtherTabs: (uri) =>
          this.closeOtherTabs(uri).mapErr((err) =>
            console.error('Failed to close other tabs:', err),
          ),
        onCloseSavedTabs: () =>
          this.closeSavedTabs().mapErr((err) => console.error('Failed to close saved tabs:', err)),
        onSetMainTab: (uri) =>
          this.setMainTab(uri).mapErr((err) => console.error('Failed to set main tab:', err)),
      }),
    );

    // Resolve initial theme from URL to prevent theme flicker
    const def = getThemeDefinition(getInitialTheme()).unwrap();

    this.editor = this.disposables.add(
      monaco.editor.create(container, {
        ...config.defaultEditorOptions,
        theme: def.id,
      }),
    );

    this.setTheme(def.id);

    this.disposables.add(
      this.editor.addAction({
        id: 'vsview.save',
        label: 'Save Script',
        run: () => {
          this.saveRequestEmitter.fire();
        },
      }),
    );

    this.disposables.add(
      this.editor.addAction({
        id: 'vsview.generateStubs',
        label: 'VapourSynth: Generate Stubs',
        run: () =>
          void BridgeService.active.unwrapOr(undefined)?.sendEvent('editor.generateStubsRequested'),
      }),
    );

    this.disposables.add(
      this.editor.addAction({
        id: 'pyrefly.restartserver',
        label: 'Pyrefly: Restart Language Server',
        run: () => void BridgeService.active.unwrapOr(undefined)?.sendEvent('lsp.restartRequested'),
      }),
    );

    this.disposables.add(
      monaco.editor.registerEditorOpener({
        openCodeEditor: async (_source, resource, selectionOrPosition) => {
          const resourceStr = this.normalizeUri(resource.toString()).toString();
          let targetTab = this.tabs.get(resourceStr);
          if (!targetTab) {
            for (const tab of this.tabs.values()) {
              if (isSameResource(tab.uri, resource)) {
                targetTab = tab;
                break;
              }
            }
          }

          if (targetTab) {
            return this.selectTab(targetTab.uriString).match({
              ok: () => {
                this.revealSelectionOrPosition(selectionOrPosition);
                return true;
              },
              err: () => false,
            });
          }

          if (resource.scheme === 'file') {
            return (await BridgeService.readFile(resource.fsPath))
              .andThen((content) =>
                this.openTab(
                  resourceStr,
                  content,
                  resource.path.endsWith('.py') || resource.path.endsWith('.pyi')
                    ? 'python'
                    : undefined,
                  false,
                ),
              )
              .match({
                ok: () => {
                  this.revealSelectionOrPosition(selectionOrPosition);
                  return true;
                },
                err: () => false,
              });
          }
          return false;
        },
      }),
    );

    // Initialize default main tab
    this.openTab(DEFAULT_SCRIPT_URI, '', 'python', true).mapErr((err) =>
      console.error('Failed to open the default tab:', err),
    );
  }

  public getActiveUri(): string | null {
    return this.activeUri;
  }

  public getMainUri(): string | null {
    return this.mainUri;
  }

  public getActiveSnapshot(): TabSnapshot | undefined {
    const tab = this.activeUri ? this.tabs.get(this.activeUri) : undefined;
    return tab && this.getSnapshot(tab);
  }

  public takeChangedMainSnapshot(): TabSnapshot | undefined {
    const tab = this.mainUri ? this.tabs.get(this.mainUri) : undefined;
    if (!tab || tab.model.isDisposed()) return undefined;

    const versionId = this.versionOf(tab);
    if (this.sentMain?.tab === tab && this.sentMain.versionId === versionId) return undefined;

    this.sentMain = { tab, versionId };
    return this.getSnapshot(tab);
  }

  /** Answers `false`, and changes nothing, when the tab is gone or was edited after `versionId`. */
  public applyFormat(uriInput: string, versionId: number, text: string): Result<boolean> {
    return Result.fromThrowable(() => {
      const tab = this.tabs.get(this.normalizeUri(uriInput).toString());
      if (!tab || tab.model.isDisposed() || this.versionOf(tab) !== versionId) {
        return false;
      }

      const model = tab.model;
      const edit = minimalEdit(model.getValue(), text);
      if (!edit) return true;

      const start = model.getPositionAt(edit.start);
      const end = model.getPositionAt(edit.end);
      model.pushStackElement();
      model.pushEditOperations(
        [],
        [
          {
            range: {
              startLineNumber: start.lineNumber,
              startColumn: start.column,
              endLineNumber: end.lineNumber,
              endColumn: end.column,
            },
            text: edit.text,
          },
        ],
        () => null,
      );
      model.pushStackElement();
      return true;
    });
  }

  public setTheme(theme: string): void {
    getThemeDefinition(theme).match({
      ok: (def) => {
        applyDocumentTheme(def);
        monaco.editor.setTheme(def.id);
        void Result.fromPromise(() => this.persistThemeConfig(def.id)).mapErr((err) =>
          console.error(`Failed to set theme: ${err.message}`),
        );
      },
      err: (err) => console.error(`Failed to set theme: ${err.message}`),
    });
  }

  public focus(): void {
    this.editor.focus();
  }

  public layout(): void {
    this.editor.layout();
  }

  public toggleWordWrap(): void {
    const current = this.editor.getOption(monaco.editor.EditorOption.wordWrap);
    const next = current === 'on' ? 'off' : 'on';
    this.editor.updateOptions({ wordWrap: next });
  }

  public updateOptionsFromMap(rawMap: EditorOptionsPayload): void {
    const opts: monaco.editor.IEditorOptions = {};
    const modelOpts: monaco.editor.ITextModelUpdateOptions = {};

    for (const [key, value] of Object.entries(rawMap)) {
      switch (key) {
        case 'editor.fontSize':
          if (typeof value === 'number') opts.fontSize = value;
          break;
        case 'editor.fontFamily':
          if (typeof value === 'string') opts.fontFamily = value;
          break;
        case 'editor.tabSize':
          if (typeof value === 'number') modelOpts.tabSize = value;
          break;
        case 'editor.insertSpaces':
          if (typeof value === 'boolean') modelOpts.insertSpaces = value;
          break;
        case 'editor.wordWrap':
          if (
            value === 'off' ||
            value === 'on' ||
            value === 'wordWrapColumn' ||
            value === 'bounded'
          ) {
            opts.wordWrap = value;
          }
          break;
        case 'editor.lineNumbers':
          if (value === 'on' || value === 'off' || value === 'relative' || value === 'interval') {
            opts.lineNumbers = value;
          }
          break;
        case 'editor.minimap.enabled':
          if (typeof value === 'boolean') opts.minimap = { enabled: value };
          break;
        case 'editor.renderWhitespace':
          if (
            value === 'none' ||
            value === 'boundary' ||
            value === 'selection' ||
            value === 'trailing' ||
            value === 'all'
          ) {
            opts.renderWhitespace = value;
          }
          break;
        case 'editor.cursorBlinking':
          if (
            value === 'blink' ||
            value === 'smooth' ||
            value === 'phase' ||
            value === 'expand' ||
            value === 'solid'
          ) {
            opts.cursorBlinking = value;
          }
          break;
        case 'editor.bracketPairColorization.enabled':
          if (typeof value === 'boolean') opts.bracketPairColorization = { enabled: value };
          break;
      }
    }

    this.editor.updateOptions(opts);

    if (Object.keys(modelOpts).length > 0) {
      this.modelOptions = { ...this.modelOptions, ...modelOpts };
      for (const tab of this.tabs.values()) {
        if (!tab.model.isDisposed()) {
          tab.model.updateOptions(modelOpts);
        }
      }
    }
  }

  /** Open a new tab or bring existing tab to focus. */
  public openTab(
    uriInput: string,
    content: string,
    language = 'python',
    isMain = false,
    replaceUriInput?: string,
  ): Result<string> {
    return Result.fromThrowable(() => {
      const uri = this.normalizeUri(uriInput);
      const uriString = uri.toString();

      let tab = this.tabs.get(uriString);
      if (tab?.model.isDisposed()) {
        this.reattachTab(tab, content, language, false);
      }

      if (!tab) {
        let model = findExistingModel(uri);
        let ownsModel = false;
        if (!model || model.isDisposed()) {
          model = monaco.editor.createModel(content, language, uri);
          ownsModel = true;
        } else {
          ownsModel = false;
        }
        if (content && model.getValue() !== content) {
          model.setValue(content);
        }

        tab = {
          uri,
          uriString,
          title: this.deriveTitle(uriInput),
          model,
          ownsModel,
          viewState: null,
          versionBase: 0,
          savedVersionId: model.getAlternativeVersionId(),
          sentVersionId: null,
          isDirty: false,
          isMain,
          disposables: new DisposableStore(),
        };
        this.attachModel(tab, model, ownsModel);

        this.tabs.set(uriString, tab);
      } else if (!tab.isDirty && tab.model.getValue() !== content) {
        tab.model.setValue(content);
        tab.savedVersionId = this.versionOf(tab);
        tab.isDirty = false;
        this.updateTabBar();
      }

      let oldTab: TabRecord | undefined;
      if (replaceUriInput) {
        const replaceUriString = this.normalizeUri(replaceUriInput).toString();
        if (replaceUriString !== uriString) {
          const candidate = this.tabs.get(replaceUriString);
          if (candidate && !candidate.isDirty) {
            oldTab = candidate;
            this.replaceTabInMap(replaceUriString, uriString, tab);
            if (this.mainUri === replaceUriString) {
              this.mainUri = null;
            }
          }
        }
      }

      this.selectTabInternal(uriString);

      if (oldTab) {
        this.disposeTab(oldTab);
      }

      if (isMain || this.tabs.size === 1 || !this.mainUri) {
        this.setMainTabInternal(uriString);
      }

      return uriString;
    });
  }

  /** Select an existing tab by URI. */
  public selectTab(uriInput: string): Result<void> {
    return Result.fromThrowable(() => {
      const uriString = this.normalizeUri(uriInput).toString();
      if (!this.tabs.has(uriString)) {
        throw new Error(`Tab ${uriString} does not exist.`);
      }
      this.selectTabInternal(uriString);
    });
  }

  /** Close an existing tab by URI. */
  public closeTab(uriInput: string): Result<void> {
    return Result.fromThrowable(() => {
      const uriString = this.normalizeUri(uriInput).toString();
      this.closeTabs([uriString]);
    });
  }

  /** Close all tabs except the specified URI. */
  public closeOtherTabs(keepUriInput: string): Result<void> {
    return Result.fromThrowable(() => {
      const keepUriString = this.normalizeUri(keepUriInput).toString();
      if (!this.tabs.has(keepUriString)) {
        throw new Error(`Tab ${keepUriString} does not exist.`);
      }
      const urisToClose = Array.from(this.tabs.keys()).filter((k) => k !== keepUriString);
      this.closeTabs(urisToClose);
    });
  }

  /** Close all tabs without unsaved changes. */
  public closeSavedTabs(): Result<void> {
    return Result.fromThrowable(() => {
      const urisToClose = Array.from(this.tabs.entries())
        .filter(([, tab]) => !tab.isDirty)
        .map(([uriString]) => uriString);
      this.closeTabs(urisToClose);
    });
  }

  /** Set specified tab as the Main Script. */
  public setMainTab(uriInput: string): Result<void> {
    return Result.fromThrowable(() => {
      const uriString = this.normalizeUri(uriInput).toString();
      if (!this.tabs.has(uriString)) {
        throw new Error(`Tab ${uriString} does not exist.`);
      }
      this.setMainTabInternal(uriString);
    });
  }

  /** Edits made after `versionId` stay unsaved. Without it, the current version is saved. */
  public markTabSaved(uriInput: string, oldUriInput: string, versionId?: number): void {
    const tab = this.tabs.get(this.normalizeUri(oldUriInput).toString());
    if (!tab || tab.model.isDisposed()) return;

    tab.savedVersionId = versionId ?? this.versionOf(tab);
    this.refreshDirty(tab);
    // Mark it saved before the rename, which carries the dirty state over to the new model.
    this.renameTabInternal(tab.uriString, this.normalizeUri(uriInput).toString());
  }

  /** Rename an existing tab URI and update its model/title. */
  public renameTab(oldUriInput: string, newUriInput: string): Result<void> {
    return Result.fromThrowable(() => {
      const oldUriString = this.normalizeUri(oldUriInput).toString();
      const newUriString = this.normalizeUri(newUriInput).toString();
      if (!this.tabs.has(oldUriString)) {
        throw new Error(`Tab ${oldUriString} does not exist.`);
      }
      this.renameTabInternal(oldUriString, newUriString);
    });
  }

  public updateTabBar(): void {
    this.tabBarView.render(
      Array.from(this.tabs.values(), (tab) => this.getTabInfo(tab)),
      this.activeUri,
    );

    BridgeService.active
      .unwrapOr(undefined)
      ?.sendEvent('editor.tabStateChanged', { tabs: this.takeTabState() });
  }

  public getTabs(): TabInfo[] {
    return Array.from(this.tabs.values(), (tab) =>
      tab.model.isDisposed()
        ? this.getTabInfo(tab)
        : {
            ...this.getTabInfo(tab),
            content: tab.model.getValue(),
            versionId: this.versionOf(tab),
          },
    );
  }

  public requestSave(): void {
    this.saveRequestEmitter.fire();
  }

  public requestSaveAs(): void {
    this.saveAsRequestEmitter.fire();
  }

  public requestFormat(): void {
    this.formatRequestEmitter.fire();
  }

  public onDidChangeContent(listener: () => void): vscode.Disposable {
    return this.disposables.add(this.contentChangeEmitter.event(listener));
  }

  public onDidChangeActiveTab(listener: (uri: string | null) => void): vscode.Disposable {
    return this.disposables.add(this.activeTabChangeEmitter.event(listener));
  }

  public onDidChangeMainTab(listener: (uri: string | null) => void): vscode.Disposable {
    return this.disposables.add(this.mainTabChangeEmitter.event(listener));
  }

  public onDidChangeCursorPosition(
    listener: (e: monaco.editor.ICursorPositionChangedEvent) => void,
  ): vscode.Disposable {
    return this.disposables.add(this.editor.onDidChangeCursorPosition(listener));
  }

  public onDidRequestSave(listener: () => void): vscode.Disposable {
    return this.disposables.add(this.saveRequestEmitter.event(listener));
  }

  public onDidRequestSaveAs(listener: () => void): vscode.Disposable {
    return this.disposables.add(this.saveAsRequestEmitter.event(listener));
  }

  public onDidRequestFormat(listener: () => void): vscode.Disposable {
    return this.disposables.add(this.formatRequestEmitter.event(listener));
  }

  public dispose(): void {
    for (const tab of this.tabs.values()) {
      this.disposeTab(tab);
    }
    this.tabs.clear();
    this.disposables.dispose();
  }

  public [Symbol.dispose](): void {
    this.dispose();
  }

  private applyModelOptions(model: monaco.editor.ITextModel): void {
    if (Object.keys(this.modelOptions).length > 0) {
      model.updateOptions(this.modelOptions);
    }
  }

  private attachModel(tab: TabRecord, model: monaco.editor.ITextModel, ownsModel: boolean): void {
    if (tab.model !== model && !tab.model.isDisposed()) {
      tab.versionBase += tab.model.getVersionId();
    }
    tab.disposables.dispose();
    tab.disposables = new DisposableStore();
    tab.model = model;
    tab.ownsModel = ownsModel;
    this.applyModelOptions(model);

    tab.disposables.add(
      model.onDidChangeContent(() => {
        this.refreshDirty(tab);
        this.contentChangeEmitter.fire();
      }),
    );
    if (!ownsModel) {
      // VS Code releases a borrowed model (a go-to-definition target) a few minutes after its last reference,
      // even while the tab is open.
      tab.disposables.add(
        model.onWillDispose(() => {
          const content = tab.model.getValue();
          const language = tab.model.getLanguageId();
          const wasDirty = tab.isDirty;
          // attachModel cannot read the version of a disposed model.
          tab.versionBase += tab.model.getVersionId();
          if (this.activeUri === tab.uriString) {
            tab.viewState = this.editor.saveViewState();
          }

          // Wait until the dispose has finished, so the URI is free and the editor has let go of the old model.
          const timer = setTimeout(() => this.reattachTab(tab, content, language, wasDirty), 0);
          tab.disposables.add(toDisposable(() => clearTimeout(timer)));
        }),
      );
    }
  }

  private reattachTab(tab: TabRecord, content: string, language: string, wasDirty: boolean): void {
    if (this.tabs.get(tab.uriString) !== tab) return;

    const existing = findExistingModel(tab.uri);
    const model = existing ?? monaco.editor.createModel(content, language, tab.uri);
    if (existing && existing.getValue() !== content) {
      existing.setValue(content);
    }
    this.attachModel(tab, model, existing === undefined);
    tab.savedVersionId = wasDirty ? -1 : this.versionOf(tab);
    tab.isDirty = wasDirty;

    if (this.activeUri === tab.uriString) {
      this.editor.setModel(model);
      if (tab.viewState) {
        this.editor.restoreViewState(tab.viewState);
      }
    }
    this.updateTabBar();
  }

  private versionOf(tab: TabRecord): number {
    return tab.versionBase + tab.model.getAlternativeVersionId();
  }

  private refreshDirty(tab: TabRecord): void {
    const dirty = this.versionOf(tab) !== tab.savedVersionId;
    if (tab.isDirty !== dirty) {
      tab.isDirty = dirty;
      this.updateTabBar();
    }
  }

  private getSnapshot(tab: TabRecord): TabSnapshot | undefined {
    if (tab.model.isDisposed()) return undefined;
    return { uri: tab.uriString, versionId: this.versionOf(tab), content: tab.model.getValue() };
  }

  private disposeTab(tab: TabRecord): void {
    tab.disposables.dispose();
    if (tab.ownsModel && !tab.model.isDisposed()) {
      tab.model.dispose();
    }
  }

  private closeTabs(urisToClose: string[]): void {
    const targets: TabRecord[] = [];
    const closingSet = new Set<string>();
    for (const uri of urisToClose) {
      const tab = this.tabs.get(uri);
      if (tab && !closingSet.has(uri)) {
        closingSet.add(uri);
        targets.push(tab);
      }
    }
    if (targets.length === 0) return;

    const dirtyTargets = targets.filter((t) => t.isDirty);
    if (dirtyTargets.length > 0) {
      const message =
        dirtyTargets.length === 1
          ? `Discard unsaved changes for ${dirtyTargets[0]!.title}?`
          : `Discard unsaved changes for ${dirtyTargets.length} tabs (${dirtyTargets.map((t) => t.title).join(', ')})?`;
      if (!confirm(message)) return;
    }

    const tabKeys = Array.from(this.tabs.keys());
    const remainingKeys = tabKeys.filter((k) => !closingSet.has(k));
    const wasActiveClosing = this.activeUri !== null && closingSet.has(this.activeUri);
    const wasMainClosing = this.mainUri !== null && closingSet.has(this.mainUri);
    const closedActiveIndex = this.activeUri ? tabKeys.indexOf(this.activeUri) : 0;

    if (remainingKeys.length === 0) {
      for (const tab of targets) {
        this.tabs.delete(tab.uriString);
        this.disposeTab(tab);
      }
      this.activeUri = null;
      this.mainUri = null;
      this.openTab(DEFAULT_SCRIPT_URI, '', 'python', true).unwrap();
      return;
    }

    let activeChanged = false;
    if (wasActiveClosing) {
      let nextKey = remainingKeys[remainingKeys.length - 1]!;
      for (const k of remainingKeys) {
        if (tabKeys.indexOf(k) >= closedActiveIndex) {
          nextKey = k;
          break;
        }
      }
      const nextTab = this.tabs.get(nextKey);
      if (nextTab && !nextTab.model.isDisposed()) {
        this.activeUri = nextKey;
        this.editor.setModel(nextTab.model);
        if (nextTab.viewState) {
          this.editor.restoreViewState(nextTab.viewState);
        }
        this.editor.focus();
      } else {
        this.activeUri = null;
      }
      activeChanged = true;
    }

    for (const tab of targets) {
      this.tabs.delete(tab.uriString);
      this.disposeTab(tab);
    }

    let mainChanged = false;
    if (wasMainClosing) {
      const nextMainKey = remainingKeys[0]!;
      for (const [key, tab] of this.tabs.entries()) {
        tab.isMain = key === nextMainKey;
      }
      this.mainUri = nextMainKey;
      mainChanged = true;
    }

    this.updateTabBar();
    if (activeChanged || mainChanged) {
      this.contentChangeEmitter.fire();
    }
    if (activeChanged) {
      this.activeTabChangeEmitter.fire(this.activeUri);
    }
    if (mainChanged) {
      this.mainTabChangeEmitter.fire(this.mainUri);
    }
  }

  private replaceTabInMap(oldKey: string, newKey: string, record: TabRecord): void {
    if (oldKey === newKey) {
      this.tabs.set(newKey, record);
      return;
    }
    const entries = Array.from(this.tabs.entries());
    this.tabs.clear();
    let replaced = false;
    for (const [k, v] of entries) {
      if (k === oldKey) {
        this.tabs.set(newKey, record);
        replaced = true;
      } else if (k === newKey) {
        if (v !== record) {
          this.disposeTab(v);
        }
      } else {
        this.tabs.set(k, v);
      }
    }
    if (!replaced) {
      this.tabs.set(newKey, record);
    }
  }

  private async persistThemeConfig(themeId: string): Promise<void> {
    await updateUserConfiguration(JSON.stringify({ 'workbench.colorTheme': themeId }));
    const conf = vscode.workspace.getConfiguration();
    await conf.update('workbench.colorTheme', themeId, vscode.ConfigurationTarget.Workspace);
  }

  private getTabInfo(tab: TabRecord): TabInfo {
    return {
      uri: tab.uriString,
      title: tab.title,
      isMain: tab.isMain,
      isDirty: tab.isDirty,
      language: tab.model.isDisposed() ? 'plaintext' : tab.model.getLanguageId(),
    };
  }

  /** Includes a tab's text only when it changed since the last call. */
  private takeTabState(): TabInfo[] {
    return Array.from(this.tabs.values(), (tab) => {
      const info = this.getTabInfo(tab);
      if (!tab.model.isDisposed()) {
        const versionId = this.versionOf(tab);
        if (tab.sentVersionId !== versionId) {
          tab.sentVersionId = versionId;
          info.content = tab.model.getValue();
          info.versionId = versionId;
        }
      }
      return info;
    });
  }

  private renameTabInternal(oldUriString: string, newUriString: string): void {
    if (oldUriString === newUriString) return;
    const tab = this.tabs.get(oldUriString);
    if (!tab) return;

    const collided = this.tabs.get(newUriString);
    const inheritsActive =
      collided !== undefined && collided !== tab && this.activeUri === newUriString;
    const inheritsMain =
      collided !== undefined &&
      collided !== tab &&
      (collided.isMain || this.mainUri === newUriString);
    if (collided && collided !== tab) {
      this.tabs.delete(newUriString);
      this.disposeTab(collided);
    }

    const newUri = this.normalizeUri(newUriString);
    const newTitle = this.deriveTitle(newUriString);

    const viewState = this.activeUri === oldUriString ? this.editor.saveViewState() : tab.viewState;

    let newModel = findExistingModel(newUri);
    let ownsModel = false;
    if (!newModel || newModel.isDisposed()) {
      newModel = monaco.editor.createModel(tab.model.getValue(), tab.model.getLanguageId(), newUri);
      ownsModel = true;
    } else {
      ownsModel = false;
      if (newModel.getValue() !== tab.model.getValue()) {
        newModel.setValue(tab.model.getValue());
      }
    }
    const oldModel = tab.model;
    const oldOwnsModel = tab.ownsModel;

    this.attachModel(tab, newModel, ownsModel);
    tab.savedVersionId = tab.isDirty ? -1 : this.versionOf(tab);
    tab.uri = newUri;
    tab.uriString = newUriString;
    tab.title = newTitle;
    tab.viewState = viewState;

    this.replaceTabInMap(oldUriString, newUriString, tab);

    if (this.activeUri === oldUriString || inheritsActive) {
      this.activeUri = newUriString;
      this.editor.setModel(newModel);
      if (viewState) {
        this.editor.restoreViewState(viewState);
      }
    }
    if (oldOwnsModel && oldModel !== newModel && !oldModel.isDisposed()) {
      oldModel.dispose();
    }
    if (this.mainUri === oldUriString || inheritsMain) {
      tab.isMain = true;
      this.mainUri = newUriString;
      this.mainTabChangeEmitter.fire(this.mainUri);
    }

    this.updateTabBar();
    this.contentChangeEmitter.fire();
    this.activeTabChangeEmitter.fire(this.activeUri);
  }

  private setMainTabInternal(uriString: string): void {
    let changed = this.mainUri !== uriString;
    for (const [key, tab] of this.tabs.entries()) {
      const shouldBeMain = key === uriString;
      if (tab.isMain !== shouldBeMain) {
        tab.isMain = shouldBeMain;
        changed = true;
      }
    }
    this.mainUri = uriString;
    this.updateTabBar();
    if (changed) {
      this.contentChangeEmitter.fire();
      this.mainTabChangeEmitter.fire(this.mainUri);
    }
  }

  private selectTabInternal(uriString: string): void {
    if (this.activeUri === uriString) return;

    // Save view state of current active tab
    if (this.activeUri && this.tabs.has(this.activeUri)) {
      const currentTab = this.tabs.get(this.activeUri)!;
      currentTab.viewState = this.editor.saveViewState();
    }

    const nextTab = this.tabs.get(uriString);
    if (!nextTab || nextTab.model.isDisposed()) return;

    this.activeUri = uriString;
    this.editor.setModel(nextTab.model);

    if (nextTab.viewState) {
      this.editor.restoreViewState(nextTab.viewState);
    }

    this.editor.focus();
    this.updateTabBar();
    this.contentChangeEmitter.fire();
    this.activeTabChangeEmitter.fire(this.activeUri);
  }

  private normalizeUri(uriInput: string): monaco.Uri {
    if (
      uriInput.includes('://') ||
      uriInput.startsWith('file:') ||
      uriInput.startsWith('inmemory:')
    ) {
      return monaco.Uri.parse(uriInput);
    }
    return monaco.Uri.file(uriInput);
  }

  private deriveTitle(uriInput: string): string {
    const uri = this.normalizeUri(uriInput);
    const path = uri.path;
    const parts = path.split('/').filter(Boolean);
    const filename = parts[parts.length - 1] ?? 'script.py';
    if ((filename === '__init__.pyi' || filename === '__init__.py') && parts.length >= 2) {
      return `${parts[parts.length - 2]}/${filename}`;
    }
    return filename;
  }

  private revealSelectionOrPosition(selectionOrPosition?: monaco.IRange | monaco.IPosition): void {
    if (!selectionOrPosition) return;
    if ('startLineNumber' in selectionOrPosition) {
      this.editor.setSelection(selectionOrPosition);
      this.editor.revealRangeInCenter(selectionOrPosition);
    } else {
      this.editor.setPosition(selectionOrPosition);
      this.editor.revealPositionInCenter(selectionOrPosition);
    }
  }
}
