import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { EditorOptionsPayload } from "../types";
import { DOM_IDS } from "../ui/constants";
import { isSameResource } from "../utils/uri";

// Hoisted Monaco & VS Code Mocks
const { FakeEmitter, FakeUri, FakeCodeEditor, FakeTextModel, modelsMap, editorState } = vi.hoisted(
  () => {
    class FakeEmitter<T> {
      private listeners: Array<(val: T) => void> = [];

      public event = (listener: (val: T) => void) => {
        this.listeners.push(listener);
        return {
          dispose: () => {
            const idx = this.listeners.indexOf(listener);
            if (idx !== -1) this.listeners.splice(idx, 1);
          },
        };
      };

      public fire(val: T): void {
        for (const listener of [...this.listeners]) {
          listener(val);
        }
      }

      public dispose(): void {
        this.listeners = [];
      }
    }

    class FakeUri {
      public scheme: string;
      public authority = "";
      public path: string;
      public query = "";
      public fragment = "";
      public fsPath: string;

      constructor(scheme: string, path: string) {
        this.scheme = scheme;
        this.path = path.startsWith("/") ? path : `/${path}`;
        this.fsPath = this.path;
      }

      public static parse(val: string): FakeUri {
        const match = val.match(/^([a-zA-Z0-9+-.]+):\/\/(.*)$/);
        if (match) {
          const scheme = match[1]!;
          const rest = match[2]!;
          return new FakeUri(scheme, `/${rest.replace(/^\/+/, "")}`);
        }
        return new FakeUri("file", val);
      }

      public static file(path: string): FakeUri {
        const normalized = path.replace(/\\/g, "/");
        return new FakeUri("file", normalized);
      }

      public toString(): string {
        return `${this.scheme}://${this.path.replace(/^\/+/, "/")}`;
      }
    }

    class FakeTextModel {
      public uri: FakeUri;
      private content: string;
      private languageId: string;
      private versionId = 1;
      private _isDisposed = false;
      private contentListeners: Array<() => void> = [];
      public options: Record<string, unknown> = {};

      constructor(content: string, languageId: string, uri: FakeUri) {
        this.content = content;
        this.languageId = languageId;
        this.uri = uri;
      }

      public getValue(): string {
        return this.content;
      }

      public setValue(newContent: string): void {
        if (this.content !== newContent) {
          this.content = newContent;
          this.versionId++;
          for (const listener of [...this.contentListeners]) {
            listener();
          }
        }
      }

      public getLanguageId(): string {
        return this.languageId;
      }

      public setLanguageId(lang: string): void {
        this.languageId = lang;
      }

      public getAlternativeVersionId(): number {
        return this.versionId;
      }

      public onDidChangeContent(listener: () => void): { dispose: () => void } {
        this.contentListeners.push(listener);
        return {
          dispose: () => {
            const idx = this.contentListeners.indexOf(listener);
            if (idx !== -1) this.contentListeners.splice(idx, 1);
          },
        };
      }

      public updateOptions(opts: Record<string, unknown>): void {
        this.options = { ...this.options, ...opts };
      }

      public isDisposed(): boolean {
        return this._isDisposed;
      }

      public dispose(): void {
        this._isDisposed = true;
        this.contentListeners = [];
        modelsMap.delete(this.uri.toString());
      }
    }

    class FakeCodeEditor {
      public currentModel: FakeTextModel | null = null;
      public options: Record<string, unknown> = {};
      public viewState: Record<string, unknown> | null = null;
      public actions: Map<string, { id: string; label: string; run: () => void }> = new Map();
      public cursorPositionListeners: Array<(e: unknown) => void> = [];
      public selection: unknown = null;
      public position: unknown = null;
      public isFocused = false;
      public layoutCalled = false;
      private _isDisposed = false;

      constructor(_container: unknown, initialOptions: Record<string, unknown>) {
        this.options = { ...initialOptions };
      }

      public getValue(): string {
        return this.currentModel ? this.currentModel.getValue() : "";
      }

      public setValue(text: string): void {
        if (this.currentModel) {
          this.currentModel.setValue(text);
        }
      }

      public setModel(model: FakeTextModel | null): void {
        this.currentModel = model;
      }

      public getModel(): FakeTextModel | null {
        return this.currentModel;
      }

      public updateOptions(opts: Record<string, unknown>): void {
        this.options = { ...this.options, ...opts };
      }

      public getOption(opt: number | string): unknown {
        if (opt === 140) {
          // EditorOption.wordWrap
          return this.options.wordWrap ?? "off";
        }
        return this.options[opt as string];
      }

      public focus(): void {
        this.isFocused = true;
      }

      public layout(): void {
        this.layoutCalled = true;
      }

      public saveViewState(): Record<string, unknown> | null {
        return {
          modelUri: this.currentModel?.uri.toString(),
          cursor: this.position,
          selection: this.selection,
          viewId: `view-${this.currentModel?.uri.toString()}`,
        };
      }

      public restoreViewState(state: Record<string, unknown>): void {
        this.viewState = state;
        if (state?.cursor) this.position = state.cursor;
        if (state?.selection) this.selection = state.selection;
      }

      public addAction(action: { id: string; label: string; run: () => void }): {
        dispose: () => void;
      } {
        this.actions.set(action.id, action);
        return { dispose: () => this.actions.delete(action.id) };
      }

      public onDidChangeCursorPosition(listener: (e: unknown) => void): { dispose: () => void } {
        this.cursorPositionListeners.push(listener);
        return {
          dispose: () => {
            const idx = this.cursorPositionListeners.indexOf(listener);
            if (idx !== -1) this.cursorPositionListeners.splice(idx, 1);
          },
        };
      }

      public setSelection(selection: unknown): void {
        this.selection = selection;
      }

      public revealRangeInCenter(_range: unknown): void {}

      public setPosition(position: unknown): void {
        this.position = position;
      }

      public revealPositionInCenter(_position: unknown): void {}

      public isDisposed(): boolean {
        return this._isDisposed;
      }

      public dispose(): void {
        this._isDisposed = true;
      }
    }

    const modelsMap = new Map<string, FakeTextModel>();
    const editorState = {
      lastCreatedEditor: null as FakeCodeEditor | null,
    };

    return {
      FakeEmitter,
      FakeUri,
      FakeTextModel,
      FakeCodeEditor,
      modelsMap,
      editorState,
    };
  },
);

vi.mock("@codingame/monaco-vscode-configuration-service-override", () => ({
  updateUserConfiguration: vi.fn().mockResolvedValue(undefined),
}));

vi.mock("vscode", () => ({
  workspace: {
    getConfiguration: vi.fn().mockReturnValue({
      update: vi.fn().mockResolvedValue(undefined),
    }),
  },
  ConfigurationTarget: {
    Workspace: 2,
  },
  Disposable: {
    from: (...disposables: Array<{ dispose?: () => void }>) => ({
      dispose: () => disposables.forEach((d) => d?.dispose?.()),
    }),
  },
}));

vi.mock("monaco-editor", () => ({
  Emitter: FakeEmitter,
  Uri: FakeUri,
  editor: {
    create: (container: unknown, options: Record<string, unknown>) => {
      editorState.lastCreatedEditor = new FakeCodeEditor(container, options);
      return editorState.lastCreatedEditor;
    },
    createModel: (content: string, language: string, uri: { toString: () => string }) => {
      const model = new FakeTextModel(
        content,
        language || "plaintext",
        uri as typeof FakeUri.prototype,
      );
      modelsMap.set(uri.toString(), model);
      return model;
    },
    getModel: (uri: { toString: () => string }) => modelsMap.get(uri.toString()),
    getModels: () => Array.from(modelsMap.values()).filter((m) => !m.isDisposed()),
    setModelLanguage: (model: { setLanguageId: (l: string) => void }, lang: string) =>
      model.setLanguageId(lang),
    setTheme: vi.fn(),
    registerEditorOpener: vi.fn().mockReturnValue({ dispose: vi.fn() }),
    EditorOption: {
      wordWrap: 140,
    },
  },
}));

vi.mock("../services/models", () => ({
  findExistingModel: (resource: { scheme: string; fsPath: string; toString: () => string }) => {
    const direct = modelsMap.get(resource.toString());
    if (direct && !direct.isDisposed()) {
      return direct;
    }
    for (const m of modelsMap.values()) {
      if (m.isDisposed()) continue;
      if (isSameResource(m.uri, resource)) {
        return m;
      }
    }
    return undefined;
  },
  DiskFileSystemProvider: class {},
  initVscodeServices: vi.fn().mockResolvedValue({ ok: true, value: undefined }),
}));

import { EditorService } from "./service";

describe("EditorService", () => {
  let service: EditorService;

  beforeEach(() => {
    modelsMap.clear();
    editorState.lastCreatedEditor = null;
    document.body.innerHTML = "";

    const tabBarContainer = document.createElement("div");
    tabBarContainer.id = DOM_IDS.TAB_BAR;
    document.body.appendChild(tabBarContainer);

    const editorContainer = document.createElement("div");
    editorContainer.id = DOM_IDS.EDITOR;
    document.body.appendChild(editorContainer);

    window.confirm = vi.fn().mockReturnValue(true);

    service = new EditorService();
  });

  afterEach(() => {
    service.dispose();
    document.body.innerHTML = "";
    vi.restoreAllMocks();
  });

  describe("Initialization & Default Tab", () => {
    it("creates default main tab 'file:///workspace/script.py' and sets up Monaco editor", () => {
      expect(service.getValue()).toBe("");
      expect(service.getMainValue()).toBe("");
      expect(editorState.lastCreatedEditor).not.toBeNull();
      expect(editorState.lastCreatedEditor?.currentModel?.uri.toString()).toBe(
        "file:///workspace/script.py",
      );

      const tabBarContainer = document.getElementById(DOM_IDS.TAB_BAR)!;
      expect(tabBarContainer.children).toHaveLength(1);

      const defaultTabEl = tabBarContainer.children[0] as HTMLElement;
      expect(defaultTabEl.classList.contains("tab")).toBe(true);
      expect(defaultTabEl.classList.contains("active")).toBe(true);
      expect(defaultTabEl.classList.contains("main")).toBe(true);
      expect(defaultTabEl.querySelector(".tab-title")?.textContent).toBe("script.py");
    });
  });

  describe("Multi-tab operations", () => {
    it("openTab() creates a tab, derives correct title, sets active tab, and updates tab bar", () => {
      // Regular python file
      const res1 = service.openTab(
        "file:///workspace/sub/helper.py",
        "def help(): pass",
        "python",
        false,
      );
      expect(res1.ok).toBe(true);
      expect(service.getValue()).toBe("def help(): pass");

      // Special case: pkg/__init__.py
      const res2 = service.openTab(
        "file:///workspace/mypkg/__init__.py",
        "# package init",
        "python",
        false,
      );
      expect(res2.ok).toBe(true);

      // Special case: pkg/__init__.pyi
      const res3 = service.openTab(
        "file:///workspace/typings/stubs/__init__.pyi",
        "# stubs init",
        "python",
        false,
      );
      expect(res3.ok).toBe(true);

      // Special case: workspace root __init__.py
      const res4 = service.openTab("file:///workspace/__init__.py", "# root init", "python", false);
      expect(res4.ok).toBe(true);

      const tabBarContainer = document.getElementById(DOM_IDS.TAB_BAR)!;
      // Main tab is always sorted first, followed by remaining open tabs
      const titles = Array.from(tabBarContainer.children).map(
        (tab) => tab.querySelector(".tab-title")?.textContent,
      );
      expect(titles).toContain("script.py");
      expect(titles).toContain("helper.py");
      expect(titles).toContain("mypkg/__init__.py");
      expect(titles).toContain("stubs/__init__.pyi");
      expect(titles).toContain("workspace/__init__.py");

      // Active tab should be the last opened tab
      expect(service.getValue()).toBe("# root init");
    });

    it("openTab() on existing tab selects it and updates content if changed", () => {
      service.openTab("file:///workspace/test.py", "version 1", "python", false);
      expect(service.getValue()).toBe("version 1");

      // Switch back to script.py
      service.selectTab("file:///workspace/script.py");
      expect(service.getValue()).toBe("");

      // Open existing tab with updated content
      service.openTab("file:///workspace/test.py", "version 2", "python", false);
      expect(service.getValue()).toBe("version 2");
    });

    it("selectTab() switches active tab and saves/restores viewState", () => {
      service.openTab("file:///workspace/file1.py", "content 1", "python", false);
      service.openTab("file:///workspace/file2.py", "content 2", "python", false);

      const activeTabChanges: Array<string | null> = [];
      service.onDidChangeActiveTab((uri) => activeTabChanges.push(uri));

      // Select file1.py
      const selRes = service.selectTab("file:///workspace/file1.py");
      expect(selRes.ok).toBe(true);
      expect(service.getValue()).toBe("content 1");
      expect(activeTabChanges).toEqual(["file:///workspace/file1.py"]);

      // Verify view state was restored on editor
      expect(editorState.lastCreatedEditor?.viewState).not.toBeNull();
      expect(editorState.lastCreatedEditor?.viewState?.modelUri).toBe("file:///workspace/file1.py");

      // Selecting non-existent tab returns Result.err
      const invalidSel = service.selectTab("file:///workspace/nonexistent.py");
      expect(invalidSel.ok).toBe(false);
    });

    it("closeTab() on active tab switches to adjacent tab", () => {
      service.openTab("file:///workspace/file1.py", "1", "python", false);
      service.openTab("file:///workspace/file2.py", "2", "python", false);
      service.openTab("file:///workspace/file3.py", "3", "python", false);

      // file3 is active. Close it -> should switch to file2
      service.closeTab("file:///workspace/file3.py");
      expect(service.getValue()).toBe("2");

      // file2 is active. Close it -> should switch to file1
      service.closeTab("file:///workspace/file2.py");
      expect(service.getValue()).toBe("1");
    });

    it("closeTab() on main tab re-assigns main tab to next available tab and fires onDidChangeMainTab", () => {
      service.openTab("file:///workspace/secondary.py", "secondary content", "python", false);

      const mainTabChanges: Array<string | null> = [];
      service.onDidChangeMainTab((uri) => mainTabChanges.push(uri));

      // Default main is script.py. Close it.
      service.closeTab("file:///workspace/script.py");

      expect(mainTabChanges).toContain("file:///workspace/secondary.py");
      expect(service.getMainValue()).toBe("secondary content");
    });

    it("closeTab() when last tab is closed recreates default 'file:///workspace/script.py' main tab", () => {
      // Open a custom tab and close default script.py tab
      service.openTab("file:///workspace/custom.py", "custom code", "python", true);
      service.closeTab("file:///workspace/script.py");
      expect(service.getValue()).toBe("custom code");

      // Close the last remaining tab (custom.py)
      service.closeTab("file:///workspace/custom.py");

      // Should automatically re-open default script.py tab
      expect(service.getValue()).toBe("");
      expect(service.getMainValue()).toBe("");

      const tabBarContainer = document.getElementById(DOM_IDS.TAB_BAR)!;
      expect(tabBarContainer.children).toHaveLength(1);
      expect(tabBarContainer.children[0]!.querySelector(".tab-title")?.textContent).toBe(
        "script.py",
      );
    });

    it("closeTab() on dirty tab prompts confirmation; cancels if rejected", () => {
      service.openTab("file:///workspace/dirty.py", "initial", "python", false);
      service.setValue("modified");

      // Mock confirm to return false (user cancelled)
      vi.mocked(window.confirm).mockReturnValue(false);

      service.closeTab("file:///workspace/dirty.py");

      // Tab should still exist and still be active
      expect(service.getValue()).toBe("modified");
      const tabBarContainer = document.getElementById(DOM_IDS.TAB_BAR)!;
      const titles = Array.from(tabBarContainer.children).map(
        (tab) => tab.querySelector(".tab-title")?.textContent,
      );
      expect(titles).toContain("dirty.py");

      // Mock confirm to return true (user accepted)
      vi.mocked(window.confirm).mockReturnValue(true);
      service.closeTab("file:///workspace/dirty.py");
      const remainingTitles = Array.from(tabBarContainer.children).map(
        (tab) => tab.querySelector(".tab-title")?.textContent,
      );
      expect(remainingTitles).not.toContain("dirty.py");
    });

    it("setMainTab() designates tab as main and fires onDidChangeMainTab", () => {
      service.openTab("file:///workspace/tabA.py", "code A", "python", false);
      service.openTab("file:///workspace/tabB.py", "code B", "python", false);

      const mainTabChanges: Array<string | null> = [];
      service.onDidChangeMainTab((uri) => mainTabChanges.push(uri));

      const res = service.setMainTab("file:///workspace/tabB.py");
      expect(res.ok).toBe(true);
      expect(mainTabChanges).toContain("file:///workspace/tabB.py");
      expect(service.getMainValue()).toBe("code B");

      // Setting non-existent tab returns Result.err
      const invalidRes = service.setMainTab("file:///workspace/nonexistent.py");
      expect(invalidRes.ok).toBe(false);
    });

    it("renameTab() renames tab URI, updates model and title, and maintains active/main state", () => {
      service.openTab("file:///workspace/old_name.py", "print('hello')", "python", true);

      const activeChanges: Array<string | null> = [];
      const mainChanges: Array<string | null> = [];
      service.onDidChangeActiveTab((uri) => activeChanges.push(uri));
      service.onDidChangeMainTab((uri) => mainChanges.push(uri));

      const renameRes = service.renameTab(
        "file:///workspace/old_name.py",
        "file:///workspace/new_name.py",
      );
      expect(renameRes.ok).toBe(true);

      expect(activeChanges).toContain("file:///workspace/new_name.py");
      expect(mainChanges).toContain("file:///workspace/new_name.py");
      expect(service.getValue()).toBe("print('hello')");

      const tabBarContainer = document.getElementById(DOM_IDS.TAB_BAR)!;
      const titles = Array.from(tabBarContainer.children).map(
        (tab) => tab.querySelector(".tab-title")?.textContent,
      );
      expect(titles).toContain("new_name.py");
      expect(titles).not.toContain("old_name.py");

      // Renaming non-existent tab returns Result.err
      const invalidRename = service.renameTab(
        "file:///workspace/fake.py",
        "file:///workspace/fake2.py",
      );
      expect(invalidRename.ok).toBe(false);
    });

    it("markTabSaved() clears dirty flag on tab and handles URI updates", () => {
      service.openTab("file:///workspace/save_test.py", "original", "python", false);
      service.setValue("modified text");

      const tabBarContainer = document.getElementById(DOM_IDS.TAB_BAR)!;
      let activeTabEl = tabBarContainer.querySelector(".tab.active")!;
      expect(activeTabEl.classList.contains("dirty")).toBe(true);

      service.markTabSaved("file:///workspace/save_test.py");
      activeTabEl = tabBarContainer.querySelector(".tab.active")!;
      expect(activeTabEl.classList.contains("dirty")).toBe(false);

      // Test markTabSaved with rename mapping
      service.setValue("another change");
      expect(tabBarContainer.querySelector(".tab.active")!.classList.contains("dirty")).toBe(true);

      service.markTabSaved("file:///workspace/saved_as.py", "file:///workspace/save_test.py");
      const savedTabEl = tabBarContainer.querySelector(".tab.active")!;
      expect(savedTabEl.querySelector(".tab-title")?.textContent).toBe("saved_as.py");
      expect(savedTabEl.classList.contains("dirty")).toBe(false);
    });
  });

  describe("Editor Options Mapping", () => {
    it("updateOptionsFromMap() correctly maps configuration dictionary to Monaco editor and model options", () => {
      service.openTab("file:///workspace/opts.py", "test", "python", false);

      const payload: EditorOptionsPayload = {
        "editor.fontSize": 18,
        "editor.fontFamily": "Cascadia Code, monospace",
        "editor.tabSize": 2,
        "editor.insertSpaces": false,
        "editor.wordWrap": "on",
        "editor.lineNumbers": "relative",
        "editor.minimap.enabled": false,
        "editor.renderWhitespace": "all",
        "editor.cursorBlinking": "expand",
        "editor.bracketPairColorization.enabled": false,
      };

      service.updateOptionsFromMap(payload);

      expect(editorState.lastCreatedEditor?.options.fontSize).toBe(18);
      expect(editorState.lastCreatedEditor?.options.fontFamily).toBe("Cascadia Code, monospace");
      expect(editorState.lastCreatedEditor?.options.wordWrap).toBe("on");
      expect(editorState.lastCreatedEditor?.options.lineNumbers).toBe("relative");
      expect(editorState.lastCreatedEditor?.options.minimap).toEqual({ enabled: false });
      expect(editorState.lastCreatedEditor?.options.renderWhitespace).toBe("all");
      expect(editorState.lastCreatedEditor?.options.cursorBlinking).toBe("expand");
      expect(editorState.lastCreatedEditor?.options.bracketPairColorization).toEqual({
        enabled: false,
      });

      const model = editorState.lastCreatedEditor?.currentModel;
      expect(model?.options.tabSize).toBe(2);
      expect(model?.options.insertSpaces).toBe(false);
    });
  });

  describe("Dirty state tracking", () => {
    it("modifying model content marks tab dirty and fires onDidChangeContent", () => {
      service.openTab("file:///workspace/dirty_track.py", "init", "python", false);

      let contentChangedCalls = 0;
      service.onDidChangeContent(() => {
        contentChangedCalls++;
      });

      const tabBarContainer = document.getElementById(DOM_IDS.TAB_BAR)!;
      let activeTabEl = tabBarContainer.querySelector(".tab.active")!;
      expect(activeTabEl.classList.contains("dirty")).toBe(false);

      // Modify content -> marks dirty
      service.setValue("changed content");

      expect(contentChangedCalls).toBeGreaterThanOrEqual(1);
      activeTabEl = tabBarContainer.querySelector(".tab.active")!;
      expect(activeTabEl.classList.contains("dirty")).toBe(true);

      // Save tab -> clears dirty state
      service.markTabSaved("file:///workspace/dirty_track.py");
      activeTabEl = tabBarContainer.querySelector(".tab.active")!;
      expect(activeTabEl.classList.contains("dirty")).toBe(false);
    });
  });

  describe("General Editor Methods & Actions", () => {
    it("setFontSize, setLanguage, toggleWordWrap, focus, and layout", () => {
      service.setFontSize(20);
      expect(editorState.lastCreatedEditor?.options.fontSize).toBe(20);

      service.setLanguage("json");
      expect(editorState.lastCreatedEditor?.currentModel?.getLanguageId()).toBe("json");

      expect(editorState.lastCreatedEditor?.options.wordWrap).toBe("off");
      service.toggleWordWrap();
      expect(editorState.lastCreatedEditor?.options.wordWrap).toBe("on");
      service.toggleWordWrap();
      expect(editorState.lastCreatedEditor?.options.wordWrap).toBe("off");

      service.focus();
      expect(editorState.lastCreatedEditor?.isFocused).toBe(true);

      service.layout();
      expect(editorState.lastCreatedEditor?.layoutCalled).toBe(true);
    });

    it("requestSave, requestSaveAs, and requestFormat fire corresponding events", () => {
      let saveFired = false;
      let saveAsFired = false;
      let formatFired = false;

      service.onDidRequestSave(() => {
        saveFired = true;
      });
      service.onDidRequestSaveAs(() => {
        saveAsFired = true;
      });
      service.onDidRequestFormat(() => {
        formatFired = true;
      });

      service.requestSave();
      expect(saveFired).toBe(true);

      service.requestSaveAs();
      expect(saveAsFired).toBe(true);

      service.requestFormat();
      expect(formatFired).toBe(true);
    });
  });

  describe("Disposal", () => {
    it("dispose() cleans up all tab records, tab listeners, and owned models", () => {
      service.openTab("file:///workspace/t1.py", "1", "python", false);
      service.openTab("file:///workspace/t2.py", "2", "python", false);

      const modelsBefore = Array.from(modelsMap.values());
      expect(modelsBefore.length).toBeGreaterThanOrEqual(2);

      service.dispose();

      expect(editorState.lastCreatedEditor?.isDisposed()).toBe(true);
      for (const model of modelsBefore) {
        expect(model.isDisposed()).toBe(true);
      }
    });
  });
});
