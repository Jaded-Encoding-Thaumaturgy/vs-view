import { updateUserConfiguration } from "@codingame/monaco-vscode-configuration-service-override";
import * as monaco from "monaco-editor";
import * as vscode from "vscode";

import { BridgeService } from "../bridge/python";
import { VSCodeWorkspaceRegistry } from "../services/vscode";
import type { EditorOptionsPayload, TabInfo } from "../types";
import { DOM_IDS } from "../ui/constants";
import { TabBarView } from "../ui/tabbar";
import { DisposableStore } from "../utils/disposables";
import { Result } from "../utils/result";
import { getThemeDefinition } from "../utils/theme";
import * as config from "./config";

interface TabRecord {
  uri: monaco.Uri;
  uriString: string;
  title: string;
  model: monaco.editor.ITextModel;
  ownsModel: boolean;
  viewState: monaco.editor.ICodeEditorViewState | null;
  savedVersionId: number;
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

  constructor() {
    const container = document.getElementById(DOM_IDS.EDITOR)!;

    this.tabBarView = this.disposables.add(
      new TabBarView({
        onSelectTab: (uri) =>
          this.selectTab(uri).mapErr((err) => console.error("Failed to select tab:", err)),
        onCloseTab: (uri) =>
          this.closeTab(uri).mapErr((err) => console.error("Failed to close tab:", err)),
        onSetMainTab: (uri) =>
          this.setMainTab(uri).mapErr((err) => console.error("Failed to set main tab:", err)),
      }),
    );

    // Resolve initial theme from URL to prevent theme flicker
    const initialTheme = new URLSearchParams(window.location.search).get("initialTheme")!;
    const resTheme = getThemeDefinition(initialTheme);
    if (!resTheme.ok) {
      throw new Error(
        `Theme initialization failed for '${initialTheme}': ${resTheme.error.message}`,
      );
    }
    const def = resTheme.value;

    this.editor = this.disposables.add(
      monaco.editor.create(container, {
        ...config.defaultEditorOptions,
        theme: def.id,
      }),
    );

    this.setTheme(def.id);

    this.editor.addAction({
      id: "vsview.save",
      label: "Save Script",
      run: () => {
        this.saveRequestEmitter.fire();
      },
    });

    this.editor.addAction({
      id: "vsview.generateStubs",
      label: "VapourSynth: Generate Stubs",
      run: () => BridgeService.getActiveBridge().unwrapOr(undefined)?.requestGenerateStubs(),
    });

    // Initialize default main tab
    const defaultMainUri = "file:///workspace/script.py";
    this.openTab(defaultMainUri, "", "python", true);
  }

  /** Get the text content of the active tab. */
  public getValue(): string {
    return this.editor.getValue();
  }

  /** Get the text content of the Main Tab. */
  public getMainValue(): string {
    if (this.mainUri && this.tabs.has(this.mainUri)) {
      return this.tabs.get(this.mainUri)!.model.getValue();
    }
    return this.getValue();
  }

  /** Set the text content of the active tab. */
  public setValue(text: string): void {
    if (text !== this.getValue()) {
      this.editor.setValue(text);
    }
  }

  public setTheme(theme: string): void {
    const resDef = getThemeDefinition(theme);
    if (!resDef.ok) {
      console.error(`Failed to set theme: ${resDef.error.message}`);
      return;
    }
    const def = resDef.value;
    document.documentElement.dataset.theme = def.id;
    document.documentElement.setAttribute("data-theme-kind", def.kind.toString());

    monaco.editor.setTheme(def.id);
    this.persistThemeConfig(def.id).catch((err) => console.error(`Failed to set theme: ${err}`));
  }

  public setFontSize(size: number): void {
    this.editor.updateOptions({ fontSize: size });
  }

  public setLanguage(lang: string): void {
    const activeTab = this.activeUri ? this.tabs.get(this.activeUri) : null;
    if (activeTab) {
      monaco.editor.setModelLanguage(activeTab.model, lang);
    }
  }

  public focus(): void {
    this.editor.focus();
  }

  public layout(): void {
    this.editor.layout();
  }

  public toggleWordWrap(): void {
    const current = this.editor.getOption(monaco.editor.EditorOption.wordWrap);
    const next = current === "on" ? "off" : "on";
    this.editor.updateOptions({ wordWrap: next });
  }

  public updateOptionsFromMap(rawMap: EditorOptionsPayload): void {
    const opts: monaco.editor.IEditorOptions = {};
    const modelOpts: monaco.editor.ITextModelUpdateOptions = {};

    for (const [key, value] of Object.entries(rawMap)) {
      switch (key) {
        case "editor.fontSize":
          if (typeof value === "number") opts.fontSize = value;
          break;
        case "editor.fontFamily":
          if (typeof value === "string") opts.fontFamily = value;
          break;
        case "editor.tabSize":
          if (typeof value === "number") modelOpts.tabSize = value;
          break;
        case "editor.insertSpaces":
          if (typeof value === "boolean") modelOpts.insertSpaces = value;
          break;
        case "editor.wordWrap":
          if (
            value === "off" ||
            value === "on" ||
            value === "wordWrapColumn" ||
            value === "bounded"
          ) {
            opts.wordWrap = value;
          }
          break;
        case "editor.lineNumbers":
          if (value === "on" || value === "off" || value === "relative" || value === "interval") {
            opts.lineNumbers = value;
          }
          break;
        case "editor.minimap.enabled":
          if (typeof value === "boolean") opts.minimap = { enabled: value };
          break;
        case "editor.renderWhitespace":
          if (
            value === "none" ||
            value === "boundary" ||
            value === "selection" ||
            value === "trailing" ||
            value === "all"
          ) {
            opts.renderWhitespace = value;
          }
          break;
        case "editor.cursorBlinking":
          if (
            value === "blink" ||
            value === "smooth" ||
            value === "phase" ||
            value === "expand" ||
            value === "solid"
          ) {
            opts.cursorBlinking = value;
          }
          break;
        case "editor.bracketPairColorization.enabled":
          if (typeof value === "boolean") opts.bracketPairColorization = { enabled: value };
          break;
      }
    }

    this.editor.updateOptions(opts);

    if (Object.keys(modelOpts).length > 0) {
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
    language = "python",
    isMain = false,
  ): Result<string> {
    return Result.fromThrowable(() => {
      const uri = this.normalizeUri(uriInput);
      const uriString = uri.toString();

      let tab = this.tabs.get(uriString);
      if (tab && tab.model.isDisposed()) {
        tab.disposables.dispose();
        this.tabs.delete(uriString);
        tab = undefined;
      }

      if (!tab) {
        const tabDisposables = new DisposableStore();

        let model = monaco.editor.getModel(uri);
        let ownsModel = false;
        if (!model || model.isDisposed()) {
          if (uri.scheme === "file" && uri.path.startsWith("/workspace")) {
            const regResult = VSCodeWorkspaceRegistry.registerMemoryFile(uri, content);
            if (regResult.ok) {
              tabDisposables.add(regResult.value);
            } else {
              console.warn(`Memory file ${uriString} already registered:`, regResult.error);
            }
          }
          model = monaco.editor.createModel(content, language, uri);
          ownsModel = true;
        }
        if (content && model.getValue() !== content) {
          model.setValue(content);
        }

        const title = this.deriveTitle(uriInput);

        tab = {
          uri,
          uriString,
          title,
          model,
          ownsModel,
          viewState: null,
          savedVersionId: model.getAlternativeVersionId(),
          isDirty: false,
          isMain,
          disposables: tabDisposables,
        };

        // Listen for content changes for dirty state tracking
        const currentTabRecord = tab;
        tabDisposables.add(
          model.onDidChangeContent(() => {
            const dirty = model.getAlternativeVersionId() !== currentTabRecord.savedVersionId;
            if (currentTabRecord.isDirty !== dirty) {
              currentTabRecord.isDirty = dirty;
              this.updateTabBar();
            }
            this.contentChangeEmitter.fire();
          }),
        );

        this.tabs.set(uriString, tab);
      } else {
        if (content && tab.model.getValue() !== content) {
          tab.model.setValue(content);
          tab.savedVersionId = tab.model.getAlternativeVersionId();
          tab.isDirty = false;
        }
      }

      this.selectTabInternal(uriString);

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
      const tab = this.tabs.get(uriString);
      if (!tab) return;

      if (tab.isDirty) {
        const discard = confirm(`Discard unsaved changes for ${tab.title}?`);
        if (!discard) return;
      }

      // Save list before deletion to pick fallback active tab
      const tabKeys = Array.from(this.tabs.keys());
      const closedIndex = tabKeys.indexOf(uriString);

      // Switch active tab FIRST if closing current active tab
      if (this.activeUri === uriString) {
        const remainingKeys = tabKeys.filter((k) => k !== uriString);
        if (remainingKeys.length > 0) {
          const nextIndex = Math.min(closedIndex, remainingKeys.length - 1);
          const nextKey = remainingKeys[nextIndex]!;
          this.selectTabInternal(nextKey);
        } else {
          this.activeUri = null;
          this.activeTabChangeEmitter.fire(null);
          // Re-create default main tab if all tabs are closed
          this.openTab("file:///workspace/script.py", "", "python", true);
        }
      }

      // Remove tab record
      this.tabs.delete(uriString);

      // If closed tab was main, reassign main to another tab if available
      if (tab.isMain) {
        const [nextMainKey] = this.tabs.keys();
        if (nextMainKey) {
          this.setMainTabInternal(nextMainKey);
        } else {
          this.mainUri = null;
          this.mainTabChangeEmitter.fire(null);
          this.contentChangeEmitter.fire();
        }
      }

      // Safely dispose tab listeners and model after editor unbinding
      tab.disposables.dispose();
      if (tab.ownsModel && !tab.model.isDisposed()) {
        tab.model.dispose();
      }

      this.updateTabBar();
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

  public markTabSaved(uriInput: string, oldUriInput?: string): void {
    const newUriString = this.normalizeUri(uriInput).toString();
    const oldUriString = oldUriInput ? this.normalizeUri(oldUriInput).toString() : undefined;

    let tab = this.tabs.get(newUriString);
    if (!tab) {
      const targetOldUri =
        oldUriString && this.tabs.has(oldUriString)
          ? oldUriString
          : this.activeUri && this.tabs.has(this.activeUri)
            ? this.activeUri
            : undefined;

      if (targetOldUri) {
        tab = this.tabs.get(targetOldUri);
        if (tab) {
          this.renameTabInternal(targetOldUri, newUriString);
        }
      }
    }

    if (tab) {
      tab.savedVersionId = tab.model.getAlternativeVersionId();
      const dirty = tab.model.getAlternativeVersionId() !== tab.savedVersionId;
      if (tab.isDirty !== dirty) {
        tab.isDirty = dirty;
        this.updateTabBar();
      }
    }
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
      tab.disposables.dispose();
      if (tab.ownsModel) {
        tab.model.dispose();
      }
    }
    this.tabs.clear();
    this.disposables.dispose();
  }

  public [Symbol.dispose](): void {
    this.dispose();
  }

  private async persistThemeConfig(themeId: string): Promise<void> {
    await updateUserConfiguration(JSON.stringify({ "workbench.colorTheme": themeId }));
    const conf = vscode.workspace.getConfiguration();
    await conf.update("workbench.colorTheme", themeId, vscode.ConfigurationTarget.Workspace);
  }

  private renameTabInternal(oldUriString: string, newUriString: string): void {
    if (oldUriString === newUriString) return;
    const tab = this.tabs.get(oldUriString);
    if (!tab) return;

    const newUri = this.normalizeUri(newUriString);
    const newTitle = this.deriveTitle(newUriString);

    const viewState = this.activeUri === oldUriString ? this.editor.saveViewState() : tab.viewState;

    tab.disposables.dispose();
    tab.disposables = new DisposableStore();

    if (newUri.scheme === "file" && newUri.path.startsWith("/workspace")) {
      const regResult = VSCodeWorkspaceRegistry.registerMemoryFile(newUri, tab.model.getValue());
      if (regResult.ok) {
        tab.disposables.add(regResult.value);
      } else {
        console.warn(`Memory file ${newUriString} already registered:`, regResult.error);
      }
    }

    let newModel = monaco.editor.getModel(newUri);
    let ownsModel = false;
    if (!newModel || newModel.isDisposed()) {
      newModel = monaco.editor.createModel(tab.model.getValue(), tab.model.getLanguageId(), newUri);
      ownsModel = true;
    } else if (newModel.getValue() !== tab.model.getValue()) {
      newModel.setValue(tab.model.getValue());
    }

    if (tab.ownsModel && tab.model !== newModel && !tab.model.isDisposed()) {
      tab.model.dispose();
    }

    tab.uri = newUri;
    tab.uriString = newUriString;
    tab.title = newTitle;
    tab.model = newModel;
    tab.ownsModel = ownsModel;
    tab.viewState = viewState;

    this.tabs.delete(oldUriString);
    this.tabs.set(newUriString, tab);

    if (this.activeUri === oldUriString) {
      this.activeUri = newUriString;
      this.editor.setModel(newModel);
      if (viewState) {
        this.editor.restoreViewState(viewState);
      }
    }
    if (this.mainUri === oldUriString) {
      this.mainUri = newUriString;
      this.mainTabChangeEmitter.fire(this.mainUri);
    }

    const targetTabRecord = tab;
    tab.disposables.add(
      newModel.onDidChangeContent(() => {
        const dirty = newModel.getAlternativeVersionId() !== targetTabRecord.savedVersionId;
        if (targetTabRecord.isDirty !== dirty) {
          targetTabRecord.isDirty = dirty;
          this.updateTabBar();
        }
        this.contentChangeEmitter.fire();
      }),
    );

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

  private updateTabBar(): void {
    const tabInfos: TabInfo[] = Array.from(this.tabs.values()).map((t) => ({
      uri: t.uriString,
      title: t.title,
      isMain: t.isMain,
      isDirty: t.isDirty,
      language: t.model.getLanguageId(),
    }));

    // Ensure Main tab is rendered first
    tabInfos.sort((a, b) => (a.isMain ? -1 : b.isMain ? 1 : 0));

    this.tabBarView.render(tabInfos, this.activeUri);
  }

  private normalizeUri(uriInput: string): monaco.Uri {
    if (
      uriInput.includes("://") ||
      uriInput.startsWith("file:") ||
      uriInput.startsWith("inmemory:")
    ) {
      return monaco.Uri.parse(uriInput);
    }
    return monaco.Uri.file(uriInput);
  }

  private deriveTitle(uriInput: string): string {
    const uri = this.normalizeUri(uriInput);
    const path = uri.path;
    const parts = path.split("/");
    return parts[parts.length - 1] || "script.py";
  }
}
