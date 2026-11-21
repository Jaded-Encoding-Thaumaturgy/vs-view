import { afterEach, beforeEach, describe, expect, it, vi } from 'vite-plus/test';

import type { EditorOptionsPayload } from '../types';
import { DOM_IDS } from '../ui/constants';
import { isSameResource } from '../utils/uri';

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
      public authority = '';
      public path: string;
      public query = '';
      public fragment = '';
      public fsPath: string;

      constructor(scheme: string, path: string) {
        this.scheme = scheme;
        this.path = path.startsWith('/') ? path : `/${path}`;
        this.fsPath = this.path;
      }

      public static parse(val: string): FakeUri {
        const match = val.match(/^([a-zA-Z0-9+-.]+):\/\/(.*)$/);
        if (match) {
          const scheme = match[1]!;
          const rest = match[2]!;
          return new FakeUri(scheme, `/${rest.replace(/^\/+/, '')}`);
        }
        return new FakeUri('file', val);
      }

      public static file(path: string): FakeUri {
        const normalized = path.replace(/\\/g, '/');
        return new FakeUri('file', normalized);
      }

      public toString(): string {
        return `${this.scheme}://${this.path.replace(/^\/+/, '/')}`;
      }
    }

    class FakeTextModel {
      public uri: FakeUri;
      private content: string;
      private languageId: string;
      private versionId = 1;
      private _isDisposed = false;
      private contentListeners: Array<() => void> = [];
      private willDisposeListeners: Array<() => void> = [];
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

      public getPositionAt(offset: number): { lineNumber: number; column: number } {
        const lines = this.content.slice(0, offset).split('\n');
        return { lineNumber: lines.length, column: lines[lines.length - 1]!.length + 1 };
      }

      public pushStackElement(): void {}

      public pushEditOperations(
        _selections: unknown,
        operations: Array<{
          range: {
            startLineNumber: number;
            startColumn: number;
            endLineNumber: number;
            endColumn: number;
          };
          text: string;
        }>,
      ): null {
        for (const { range, text } of operations) {
          const start = this.getOffsetAt(range.startLineNumber, range.startColumn);
          const end = this.getOffsetAt(range.endLineNumber, range.endColumn);
          this.setValue(this.content.slice(0, start) + text + this.content.slice(end));
        }
        return null;
      }

      public onWillDispose(listener: () => void): { dispose: () => void } {
        this.willDisposeListeners.push(listener);
        return {
          dispose: () => {
            const idx = this.willDisposeListeners.indexOf(listener);
            if (idx !== -1) this.willDisposeListeners.splice(idx, 1);
          },
        };
      }

      private getOffsetAt(lineNumber: number, column: number): number {
        const lines = this.content.split('\n');
        let offset = 0;
        for (let i = 0; i < lineNumber - 1; i++) {
          offset += lines[i]!.length + 1;
        }
        return offset + column - 1;
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

      public getVersionId(): number {
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
        for (const listener of [...this.willDisposeListeners]) {
          listener();
        }
        this._isDisposed = true;
        this.contentListeners = [];
        this.willDisposeListeners = [];
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
        return this.currentModel ? this.currentModel.getValue() : '';
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
          return this.options.wordWrap ?? 'off';
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
        if (state.cursor) this.position = state.cursor;
        if (state.selection) this.selection = state.selection;
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

vi.mock('@codingame/monaco-vscode-configuration-service-override', () => ({
  updateUserConfiguration: vi.fn().mockResolvedValue(undefined),
}));

vi.mock('vscode', () => ({
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
      dispose: () => disposables.forEach((d) => d.dispose?.()),
    }),
  },
}));

vi.mock('monaco-editor', () => ({
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
        language || 'plaintext',
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

vi.mock('../services/models', () => ({
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

import { updateUserConfiguration } from '@codingame/monaco-vscode-configuration-service-override';
import * as monaco from 'monaco-editor';
import { Result } from 'vsview-shared';

import { BridgeService } from '../bridge/python';
import { EditorService } from './service';

describe('EditorService', () => {
  let service: EditorService;

  beforeEach(() => {
    modelsMap.clear();
    editorState.lastCreatedEditor = null;
    document.body.innerHTML = '';

    const tabBarContainer = document.createElement('div');
    tabBarContainer.id = DOM_IDS.TAB_BAR;
    document.body.appendChild(tabBarContainer);

    const editorContainer = document.createElement('div');
    editorContainer.id = DOM_IDS.EDITOR;
    document.body.appendChild(editorContainer);

    window.confirm = vi.fn().mockReturnValue(true);

    service = new EditorService();
  });

  afterEach(() => {
    service.dispose();
    document.body.innerHTML = '';
    vi.restoreAllMocks();
  });

  function activeContent(): string | undefined {
    return service.getActiveSnapshot()?.content;
  }

  function mainContent(): string | undefined {
    return service.getTabs().find((tab) => tab.isMain)?.content;
  }

  /** Type into the active tab, as the user would. */
  function editActiveTab(text: string): void {
    editorState.lastCreatedEditor!.getModel()!.setValue(text);
  }

  function tabState(uri: string): { isDirty: boolean; content: string | undefined } | undefined {
    const tab = service.getTabs().find((t) => t.uri === uri);
    return tab && { isDirty: tab.isDirty, content: tab.content };
  }

  describe('Initialization & Default Tab', () => {
    it("creates default main tab 'file:///workspace/script.py' and sets up Monaco editor", () => {
      expect(activeContent()).toBe('');
      expect(mainContent()).toBe('');
      expect(editorState.lastCreatedEditor).not.toBeNull();
      expect(editorState.lastCreatedEditor?.currentModel?.uri.toString()).toBe(
        'file:///workspace/script.py',
      );

      const tabBarContainer = document.getElementById(DOM_IDS.TAB_BAR)!;
      expect(tabBarContainer.children).toHaveLength(1);

      const defaultTabEl = tabBarContainer.children[0] as HTMLElement;
      expect(defaultTabEl.classList.contains('tab')).toBe(true);
      expect(defaultTabEl.classList.contains('active')).toBe(true);
      expect(defaultTabEl.classList.contains('main')).toBe(true);
      expect(defaultTabEl.querySelector('.tab-title')?.textContent).toBe('script.py');
    });

    it('logs when the theme cannot be stored in the configuration', async () => {
      const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
      vi.mocked(updateUserConfiguration).mockRejectedValueOnce(new Error('config locked'));

      service.setTheme('Light Modern');

      await vi.waitFor(() =>
        expect(errorSpy).toHaveBeenCalledWith('Failed to set theme: config locked'),
      );
    });

    it('logs when the default tab cannot be opened', () => {
      modelsMap.clear();
      const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
      const failure = new Error('createModel failed');
      vi.spyOn(monaco.editor, 'createModel').mockImplementationOnce(() => {
        throw failure;
      });

      const second = new EditorService();

      expect(errorSpy).toHaveBeenCalledWith('Failed to open the default tab:', failure);
      second.dispose();
    });
  });

  describe('Multi-tab operations', () => {
    it('openTab() creates a tab, derives correct title, sets active tab, and updates tab bar', () => {
      // Regular python file
      const res1 = service.openTab(
        'file:///workspace/sub/helper.py',
        'def help(): pass',
        'python',
        false,
      );
      expect(res1.ok).toBe(true);
      expect(activeContent()).toBe('def help(): pass');

      // Special case: pkg/__init__.py
      const res2 = service.openTab(
        'file:///workspace/mypkg/__init__.py',
        '# package init',
        'python',
        false,
      );
      expect(res2.ok).toBe(true);

      // Special case: pkg/__init__.pyi
      const res3 = service.openTab(
        'file:///workspace/typings/stubs/__init__.pyi',
        '# stubs init',
        'python',
        false,
      );
      expect(res3.ok).toBe(true);

      // Special case: workspace root __init__.py
      const res4 = service.openTab('file:///workspace/__init__.py', '# root init', 'python', false);
      expect(res4.ok).toBe(true);

      const tabBarContainer = document.getElementById(DOM_IDS.TAB_BAR)!;
      // Main tab is always sorted first, followed by remaining open tabs
      const titles = Array.from(tabBarContainer.children).map(
        (tab) => tab.querySelector('.tab-title')?.textContent,
      );
      expect(titles).toContain('script.py');
      expect(titles).toContain('helper.py');
      expect(titles).toContain('mypkg/__init__.py');
      expect(titles).toContain('stubs/__init__.pyi');
      expect(titles).toContain('workspace/__init__.py');

      // Active tab should be the last opened tab
      expect(activeContent()).toBe('# root init');
    });

    it('openTab() on existing tab selects it and updates content if changed', () => {
      service.openTab('file:///workspace/test.py', 'version 1', 'python', false);
      expect(activeContent()).toBe('version 1');

      // Switch back to script.py
      service.selectTab('file:///workspace/script.py');
      expect(activeContent()).toBe('');

      // Open existing tab with updated content
      service.openTab('file:///workspace/test.py', 'version 2', 'python', false);
      expect(activeContent()).toBe('version 2');
    });

    it('openTab() on the active clean tab with new content leaves the tab bar clean', () => {
      const uri = 'file:///workspace/reopen.py';
      service.openTab(uri, 'initial', 'python', false).unwrap();

      service.openTab(uri, 'updated from disk', 'python', false).unwrap();

      expect(activeContent()).toBe('updated from disk');
      expect(service.getTabs().find((t) => t.uri === uri)?.isDirty).toBe(false);
      const activeTabEl = document.getElementById(DOM_IDS.TAB_BAR)!.querySelector('.tab.active')!;
      expect(activeTabEl.classList.contains('dirty')).toBe(false);

      editActiveTab('edited');
      expect(service.getTabs().find((t) => t.uri === uri)?.isDirty).toBe(true);
    });

    it('openTab() with replaceUri removes target tab, disposes its model, and sets new tab as active and main', () => {
      const sessionBefore = service.getTabs();
      expect(sessionBefore.map((t) => t.uri)).toEqual(['file:///workspace/script.py']);

      const oldModel = modelsMap.get('file:///workspace/script.py')!;
      expect(oldModel.isDisposed()).toBe(false);

      const res = service.openTab(
        'file:///workspace/replaced.py',
        'print("hello")',
        'python',
        true,
        'file:///workspace/script.py',
      );
      expect(res.ok).toBe(true);

      const sessionAfter = service.getTabs();
      expect(sessionAfter.map((t) => t.uri)).toEqual(['file:///workspace/replaced.py']);
      expect(sessionAfter[0]?.isMain).toBe(true);
      expect(service.getActiveUri()).toBe('file:///workspace/replaced.py');
      expect(service.getMainUri()).toBe('file:///workspace/replaced.py');
      expect(activeContent()).toBe('print("hello")');
      expect(oldModel.isDisposed()).toBe(true);
    });

    it('openTab() with replaceUri does not replace target tab if target tab has unsaved edits', () => {
      service.openTab('file:///workspace/dirty.py', 'original', 'python', false);
      editActiveTab('modified by user');

      const res = service.openTab(
        'file:///workspace/new.py',
        'new code',
        'python',
        false,
        'file:///workspace/dirty.py',
      );
      expect(res.ok).toBe(true);

      const session = service.getTabs();
      const uris = session.map((t) => t.uri);
      expect(uris).toContain('file:///workspace/dirty.py');
      expect(uris).toContain('file:///workspace/new.py');
    });

    it('openTab() with replaceUri === uri does not delete the opened tab', () => {
      const res = service.openTab(
        'file:///workspace/same.py',
        'code',
        'python',
        false,
        'file:///workspace/same.py',
      );
      expect(res.ok).toBe(true);

      const session = service.getTabs();
      expect(session.map((t) => t.uri)).toContain('file:///workspace/same.py');
    });

    it('openTab() with non-existent replaceUri opens new tab without errors', () => {
      const res = service.openTab(
        'file:///workspace/fresh.py',
        'fresh code',
        'python',
        false,
        'file:///workspace/missing.py',
      );
      expect(res.ok).toBe(true);

      const session = service.getTabs();
      expect(session.map((t) => t.uri)).toContain('file:///workspace/fresh.py');
    });

    it('openTab() with replaceUri on non-main tab keeps existing main tab', () => {
      service.openTab('file:///workspace/sub.py', 'sub', 'python', false);
      expect(service.getMainUri()).toBe('file:///workspace/script.py');

      service.openTab(
        'file:///workspace/replacement_sub.py',
        'replacement',
        'python',
        false,
        'file:///workspace/sub.py',
      );

      expect(service.getMainUri()).toBe('file:///workspace/script.py');
      const session = service.getTabs();
      const uris = session.map((t) => t.uri);
      expect(uris).toContain('file:///workspace/replacement_sub.py');
      expect(uris).not.toContain('file:///workspace/sub.py');
    });

    it('openTab() does not overwrite unsaved content when tab is dirty', () => {
      service.openTab('file:///workspace/dirty.py', 'original', 'python', false);
      expect(activeContent()).toBe('original');

      // Mutate content to make it dirty
      editActiveTab('user edited content');
      expect(activeContent()).toBe('user edited content');

      // Re-opening dirty tab with disk content should NOT overwrite user edits
      service.openTab('file:///workspace/dirty.py', 'disk content', 'python', false);
      expect(activeContent()).toBe('user edited content');
    });

    it('selectTab() switches active tab and saves/restores viewState', () => {
      service.openTab('file:///workspace/file1.py', 'content 1', 'python', false);
      service.openTab('file:///workspace/file2.py', 'content 2', 'python', false);

      const activeTabChanges: Array<string | null> = [];
      service.onDidChangeActiveTab((uri) => activeTabChanges.push(uri));

      // Select file1.py
      const selRes = service.selectTab('file:///workspace/file1.py');
      expect(selRes.ok).toBe(true);
      expect(activeContent()).toBe('content 1');
      expect(activeTabChanges).toEqual(['file:///workspace/file1.py']);

      // Verify view state was restored on editor
      expect(editorState.lastCreatedEditor?.viewState).not.toBeNull();
      expect(editorState.lastCreatedEditor?.viewState?.modelUri).toBe('file:///workspace/file1.py');

      // Selecting non-existent tab returns Result.err
      const invalidSel = service.selectTab('file:///workspace/nonexistent.py');
      expect(invalidSel.ok).toBe(false);
    });

    it('closeTab() on active tab switches to adjacent tab', () => {
      service.openTab('file:///workspace/file1.py', '1', 'python', false);
      service.openTab('file:///workspace/file2.py', '2', 'python', false);
      service.openTab('file:///workspace/file3.py', '3', 'python', false);

      // file3 is active. Close it -> should switch to file2
      service.closeTab('file:///workspace/file3.py');
      expect(activeContent()).toBe('2');

      // file2 is active. Close it -> should switch to file1
      service.closeTab('file:///workspace/file2.py');
      expect(activeContent()).toBe('1');
    });

    it('closeTab() on main tab re-assigns main tab to next available tab and fires onDidChangeMainTab', () => {
      service.openTab('file:///workspace/secondary.py', 'secondary content', 'python', false);

      const mainTabChanges: Array<string | null> = [];
      service.onDidChangeMainTab((uri) => mainTabChanges.push(uri));

      // Default main is script.py. Close it.
      service.closeTab('file:///workspace/script.py');

      expect(mainTabChanges).toContain('file:///workspace/secondary.py');
      expect(mainContent()).toBe('secondary content');
    });

    it("closeTab() and closeSavedTabs() on the lone default 'file:///workspace/script.py' tab recreate a fresh default tab and model", () => {
      const initialModel = modelsMap.get('file:///workspace/script.py')!;
      expect(initialModel.isDisposed()).toBe(false);

      // 1. closeTab on the only default tab
      const closeRes = service.closeTab('file:///workspace/script.py');
      expect(closeRes.ok).toBe(true);
      expect(initialModel.isDisposed()).toBe(true);

      const secondModel = modelsMap.get('file:///workspace/script.py')!;
      expect(secondModel).toBeDefined();
      expect(secondModel).not.toBe(initialModel);
      expect(secondModel.isDisposed()).toBe(false);
      expect(service.getActiveUri()).toBe('file:///workspace/script.py');
      expect(service.getMainUri()).toBe('file:///workspace/script.py');
      expect(service.getTabs().map((t) => t.uri)).toEqual(['file:///workspace/script.py']);

      // 2. closeSavedTabs on the only default tab
      const savedRes = service.closeSavedTabs();
      expect(savedRes.ok).toBe(true);
      expect(secondModel.isDisposed()).toBe(true);

      const thirdModel = modelsMap.get('file:///workspace/script.py')!;
      expect(thirdModel).toBeDefined();
      expect(thirdModel.isDisposed()).toBe(false);
      expect(service.getActiveUri()).toBe('file:///workspace/script.py');
      expect(service.getMainUri()).toBe('file:///workspace/script.py');
      expect(service.getTabs().map((t) => t.uri)).toEqual(['file:///workspace/script.py']);
    });

    it("closeTab() when last custom tab is closed recreates default 'file:///workspace/script.py' main tab", () => {
      // Open a custom tab and close default script.py tab
      service.openTab('file:///workspace/custom.py', 'custom code', 'python', true);
      service.closeTab('file:///workspace/script.py');
      expect(activeContent()).toBe('custom code');

      // Close the last remaining tab (custom.py)
      service.closeTab('file:///workspace/custom.py');

      // Should automatically re-open default script.py tab
      expect(activeContent()).toBe('');
      expect(mainContent()).toBe('');

      const tabBarContainer = document.getElementById(DOM_IDS.TAB_BAR)!;
      expect(tabBarContainer.children).toHaveLength(1);
      expect(tabBarContainer.children[0]!.querySelector('.tab-title')?.textContent).toBe(
        'script.py',
      );
    });

    it('closeTab() on dirty tab prompts confirmation; cancels if rejected', () => {
      service.openTab('file:///workspace/dirty.py', 'initial', 'python', false);
      editActiveTab('modified');

      // Mock confirm to return false (user cancelled)
      vi.mocked(window.confirm).mockReturnValue(false);

      service.closeTab('file:///workspace/dirty.py');

      // Tab should still exist and still be active
      expect(activeContent()).toBe('modified');
      const tabBarContainer = document.getElementById(DOM_IDS.TAB_BAR)!;
      const titles = Array.from(tabBarContainer.children).map(
        (tab) => tab.querySelector('.tab-title')?.textContent,
      );
      expect(titles).toContain('dirty.py');

      // Mock confirm to return true (user accepted)
      vi.mocked(window.confirm).mockReturnValue(true);
      service.closeTab('file:///workspace/dirty.py');
      const remainingTitles = Array.from(tabBarContainer.children).map(
        (tab) => tab.querySelector('.tab-title')?.textContent,
      );
      expect(remainingTitles).not.toContain('dirty.py');
    });

    it('setMainTab() designates tab as main, preserves tab order, and fires onDidChangeMainTab', () => {
      service.openTab('file:///workspace/tabA.py', 'code A', 'python', false);
      service.openTab('file:///workspace/tabB.py', 'code B', 'python', false);

      const mainTabChanges: Array<string | null> = [];
      service.onDidChangeMainTab((uri) => mainTabChanges.push(uri));

      const res = service.setMainTab('file:///workspace/tabB.py');
      expect(res.ok).toBe(true);
      expect(mainTabChanges).toContain('file:///workspace/tabB.py');
      expect(mainContent()).toBe('code B');

      const tabBarContainer = document.getElementById(DOM_IDS.TAB_BAR)!;
      const titles = Array.from(tabBarContainer.children).map(
        (tab) => tab.querySelector('.tab-title')?.textContent,
      );
      expect(titles).toEqual(['script.py', 'tabA.py', 'tabB.py']);

      // Setting non-existent tab returns Result.err
      const invalidRes = service.setMainTab('file:///workspace/nonexistent.py');
      expect(invalidRes.ok).toBe(false);
    });

    it('closeOtherTabs() and closeSavedTabs() batch state transitions and fire events once', () => {
      service.openTab('file:///workspace/clean1.py', 'c1', 'python', false);
      service.openTab('file:///workspace/dirty1.py', 'd1', 'python', false);
      editActiveTab('d1 edited');
      service.openTab('file:///workspace/clean2.py', 'c2', 'python', false);

      const activeChanges: Array<string | null> = [];
      const mainChanges: Array<string | null> = [];
      service.onDidChangeActiveTab((u) => activeChanges.push(u));
      service.onDidChangeMainTab((u) => mainChanges.push(u));

      // Currently active is clean2.py, main is script.py (clean).
      // closeSavedTabs() closes script.py, clean1.py, clean2.py in one batch -> dirty1.py becomes active and main once!
      const savedRes = service.closeSavedTabs();
      expect(savedRes.ok).toBe(true);

      expect(service.getTabs().map((t) => t.uri)).toEqual(['file:///workspace/dirty1.py']);
      expect(activeChanges).toEqual(['file:///workspace/dirty1.py']);
      expect(mainChanges).toEqual(['file:///workspace/dirty1.py']);

      // Open two more tabs, make drop.py dirty and active
      service.openTab('file:///workspace/keep.py', 'keep', 'python', false);
      service.openTab('file:///workspace/drop.py', 'drop', 'python', false);
      editActiveTab('drop edited');

      activeChanges.length = 0;
      mainChanges.length = 0;
      vi.mocked(window.confirm).mockClear();

      // closeOtherTabs('keep.py') closes dirty1.py and drop.py with a single confirm prompt and single active/main switch
      const otherRes = service.closeOtherTabs('file:///workspace/keep.py');
      expect(otherRes.ok).toBe(true);
      expect(window.confirm).toHaveBeenCalledTimes(1);
      expect(service.getTabs().map((t) => t.uri)).toEqual(['file:///workspace/keep.py']);
      expect(activeChanges).toEqual(['file:///workspace/keep.py']);
      expect(mainChanges).toEqual(['file:///workspace/keep.py']);

      expect(service.closeOtherTabs('file:///workspace/missing.py').ok).toBe(false);
    });

    it('renameTab() renames tab URI, updates model and title, and maintains active/main state', () => {
      service.openTab('file:///workspace/old_name.py', "print('hello')", 'python', true);

      const activeChanges: Array<string | null> = [];
      const mainChanges: Array<string | null> = [];
      service.onDidChangeActiveTab((uri) => activeChanges.push(uri));
      service.onDidChangeMainTab((uri) => mainChanges.push(uri));

      const renameRes = service.renameTab(
        'file:///workspace/old_name.py',
        'file:///workspace/new_name.py',
      );
      expect(renameRes.ok).toBe(true);

      expect(activeChanges).toContain('file:///workspace/new_name.py');
      expect(mainChanges).toContain('file:///workspace/new_name.py');
      expect(activeContent()).toBe("print('hello')");

      const tabBarContainer = document.getElementById(DOM_IDS.TAB_BAR)!;
      const titles = Array.from(tabBarContainer.children).map(
        (tab) => tab.querySelector('.tab-title')?.textContent,
      );
      expect(titles).toContain('new_name.py');
      expect(titles).not.toContain('old_name.py');

      // Renaming into an already open active tab URI disposes the collided tab's model and attaches the editor to the renamed tab's model
      service.openTab('file:///workspace/collided.py', 'old collided', 'python', false);
      const collidedModel = modelsMap.get('file:///workspace/collided.py')!;
      expect(collidedModel.isDisposed()).toBe(false);
      expect(service.getActiveUri()).toBe('file:///workspace/collided.py');

      const overwriteRename = service.renameTab(
        'file:///workspace/new_name.py',
        'file:///workspace/collided.py',
      );
      expect(overwriteRename.ok).toBe(true);
      expect(collidedModel.isDisposed()).toBe(true);
      expect(service.getActiveUri()).toBe('file:///workspace/collided.py');
      expect(activeContent()).toBe("print('hello')");
      expect(editorState.lastCreatedEditor?.currentModel?.isDisposed()).toBe(false);

      // Renaming a non-main tab onto the main tab inherits isMain = true
      service.openTab('file:///workspace/main_target.py', 'main code', 'python', true);
      service.openTab('file:///workspace/other.py', 'other code', 'python', false);
      expect(service.getMainUri()).toBe('file:///workspace/main_target.py');

      const mainOverwrite = service.renameTab(
        'file:///workspace/other.py',
        'file:///workspace/main_target.py',
      );
      expect(mainOverwrite.ok).toBe(true);
      expect(service.getMainUri()).toBe('file:///workspace/main_target.py');
      expect(mainContent()).toBe('other code');
      const mainState = service.getTabs().find((t) => t.uri === 'file:///workspace/main_target.py');
      expect(mainState?.isMain).toBe(true);

      // Renaming non-existent tab returns Result.err
      const invalidRename = service.renameTab(
        'file:///workspace/fake.py',
        'file:///workspace/fake2.py',
      );
      expect(invalidRename.ok).toBe(false);
    });

    it('renameTab() on a clean tab keeps it clean and marks the next edit dirty', () => {
      const oldUri = 'file:///workspace/clean_old.py';
      const newUri = 'file:///workspace/clean_new.py';
      service.openTab(oldUri, 'v1', 'python', false).unwrap();
      editActiveTab('v2');
      service.markTabSaved(oldUri, oldUri);

      service.renameTab(oldUri, newUri).unwrap();
      expect(service.getTabs().find((t) => t.uri === newUri)?.isDirty).toBe(false);

      editActiveTab('v3');
      expect(service.getTabs().find((t) => t.uri === newUri)?.isDirty).toBe(true);
    });

    it('renameTab() on a dirty tab keeps it dirty after more edits', () => {
      const oldUri = 'file:///workspace/dirty_old.py';
      const newUri = 'file:///workspace/dirty_new.py';
      service.openTab(oldUri, 'v1', 'python', false).unwrap();
      editActiveTab('v2');
      service.markTabSaved(oldUri, oldUri);
      editActiveTab('v3');

      service.renameTab(oldUri, newUri).unwrap();
      expect(service.getTabs().find((t) => t.uri === newUri)?.isDirty).toBe(true);

      editActiveTab('v4');
      expect(service.getTabs().find((t) => t.uri === newUri)?.isDirty).toBe(true);
    });

    it('markTabSaved() clears dirty flag on tab and handles URI updates', () => {
      service.openTab('file:///workspace/save_test.py', 'original', 'python', false);
      editActiveTab('modified text');

      const tabBarContainer = document.getElementById(DOM_IDS.TAB_BAR)!;
      let activeTabEl = tabBarContainer.querySelector('.tab.active')!;
      expect(activeTabEl.classList.contains('dirty')).toBe(true);

      service.markTabSaved('file:///workspace/save_test.py', 'file:///workspace/save_test.py');
      activeTabEl = tabBarContainer.querySelector('.tab.active')!;
      expect(activeTabEl.classList.contains('dirty')).toBe(false);

      // Test markTabSaved with rename mapping
      editActiveTab('another change');
      expect(tabBarContainer.querySelector('.tab.active')!.classList.contains('dirty')).toBe(true);

      service.markTabSaved('file:///workspace/saved_as.py', 'file:///workspace/save_test.py');
      const savedTabEl = tabBarContainer.querySelector('.tab.active')!;
      expect(savedTabEl.querySelector('.tab-title')?.textContent).toBe('saved_as.py');
      expect(savedTabEl.classList.contains('dirty')).toBe(false);
    });
  });

  describe('Versioned save', () => {
    const uri = 'file:///workspace/versioned.py';

    it.each([
      { case: 'no edit during the save', typedDuringSave: false, dirty: false },
      { case: 'an edit during the save', typedDuringSave: true, dirty: true },
    ])('markTabSaved() with the saved version: $case', ({ typedDuringSave, dirty }) => {
      service.openTab(uri, 'v1', 'python', false).unwrap();
      editActiveTab('v2');
      const saved = service.getActiveSnapshot()!;
      if (typedDuringSave) editActiveTab('v3');

      service.markTabSaved(uri, uri, saved.versionId);

      expect(tabState(uri)?.isDirty).toBe(dirty);
    });

    it('markTabSaved() to a new path keeps edits made during the save dirty', () => {
      const oldUri = 'file:///workspace/untitled_ab12.py';
      const newUri = 'file:///workspace/chosen.py';
      service.openTab(oldUri, 'v1', 'python', false).unwrap();
      editActiveTab('v2');
      const saved = service.getActiveSnapshot()!;
      editActiveTab('v3');

      service.markTabSaved(newUri, oldUri, saved.versionId);

      expect(tabState(oldUri)).toBeUndefined();
      expect(tabState(newUri)).toEqual({ isDirty: true, content: 'v3' });
    });

    it('markTabSaved() for a closed tab renames no other tab', () => {
      service.openTab('file:///workspace/other.py', 'other', 'python', false).unwrap();
      editActiveTab('other edited');

      service.markTabSaved('file:///workspace/new.py', 'file:///workspace/closed.py', 1);

      expect(service.getTabs().map((t) => [t.uri, t.isDirty])).toEqual([
        ['file:///workspace/script.py', false],
        ['file:///workspace/other.py', true],
      ]);
    });
  });

  describe('Format results', () => {
    const uriA = 'file:///workspace/a.py';
    const uriB = 'file:///workspace/b.py';

    it('applyFormat() changes the tab it was computed from, not the active tab', () => {
      service.openTab(uriA, 'a=1\nb=2\n', 'python', false).unwrap();
      const snapshot = service.getActiveSnapshot()!;
      service.openTab(uriB, 'b content', 'python', false).unwrap();

      const result = service.applyFormat(uriA, snapshot.versionId, 'a = 1\nb = 2\n');

      expect(result.unwrap()).toBe(true);
      expect(tabState(uriA)).toEqual({ isDirty: true, content: 'a = 1\nb = 2\n' });
      expect(tabState(uriB)).toEqual({ isDirty: false, content: 'b content' });
      expect(activeContent()).toBe('b content');
    });

    it('applyFormat() drops a result for a tab edited since the snapshot', () => {
      service.openTab(uriA, 'a=1', 'python', false).unwrap();
      const snapshot = service.getActiveSnapshot()!;
      editActiveTab('a=12');

      const result = service.applyFormat(uriA, snapshot.versionId, 'a = 1');

      expect(result.unwrap()).toBe(false);
      expect(tabState(uriA)?.content).toBe('a=12');
    });

    it('applyFormat() drops a result for a closed tab', () => {
      service.openTab(uriA, 'a=1', 'python', false).unwrap();
      const snapshot = service.getActiveSnapshot()!;
      service.closeTab(uriA).unwrap();

      const result = service.applyFormat(uriA, snapshot.versionId, 'a = 1');

      expect(result.unwrap()).toBe(false);
      expect(tabState(uriA)).toBeUndefined();
    });
  });

  describe('Borrowed models', () => {
    const libUri = 'file:///lib/module.pyi';

    beforeEach(() => {
      vi.useFakeTimers();
    });

    afterEach(() => {
      vi.useRealTimers();
    });

    function openBorrowedTab(): InstanceType<typeof FakeTextModel> {
      const borrowed = monaco.editor.createModel(
        'lib code',
        'python',
        FakeUri.parse(libUri) as unknown as monaco.Uri,
      ) as unknown as InstanceType<typeof FakeTextModel>;
      service.openTab(libUri, 'lib code', 'python', false).unwrap();
      return borrowed;
    }

    it.each([
      { case: 'clean', edit: undefined, expected: { isDirty: false, content: 'lib code' } },
      { case: 'dirty', edit: 'lib edited', expected: { isDirty: true, content: 'lib edited' } },
    ])('reattaches a $case tab whose model VS Code released', ({ edit, expected }) => {
      const borrowed = openBorrowedTab();
      if (edit) editActiveTab(edit);

      borrowed.dispose();
      expect(tabState(libUri)?.content).toBeUndefined();
      vi.runAllTimers();

      expect(tabState(libUri)).toEqual(expected);
      const shownModel = editorState.lastCreatedEditor!.getModel()!;
      expect(shownModel).not.toBe(borrowed);
      expect(shownModel.getValue()).toBe(expected.content);
    });

    it.each([
      {
        case: 'save ack',
        reply: (versionId: number) => service.markTabSaved(libUri, libUri, versionId),
      },
      {
        case: 'format result',
        reply: (versionId: number) => service.applyFormat(libUri, versionId, 'formatted').unwrap(),
      },
    ])('ignores a late $case for the model VS Code released', ({ reply }) => {
      const borrowed = openBorrowedTab();
      editActiveTab('lib edited');
      const requested = service.getActiveSnapshot()!;
      borrowed.dispose();
      vi.runAllTimers();
      editActiveTab('typed after the reattach');

      reply(requested.versionId);

      expect(tabState(libUri)).toEqual({ isDirty: true, content: 'typed after the reattach' });
    });

    it('does not bring back a tab closed before the reattach', () => {
      const borrowed = openBorrowedTab();

      borrowed.dispose();
      service.closeTab(libUri).unwrap();
      vi.runAllTimers();

      expect(tabState(libUri)).toBeUndefined();
    });
  });

  describe('Python sync', () => {
    function spyOnTabState(): Array<Array<{ uri: string; content?: string; versionId?: number }>> {
      const sent: Array<Array<{ uri: string; content?: string; versionId?: number }>> = [];
      const fakeBridge = {
        sendEvent: (command: string, payload: { tabs: (typeof sent)[number] }) => {
          if (command === 'editor.tabStateChanged') sent.push(payload.tabs);
        },
      };
      vi.spyOn(BridgeService, 'active', 'get').mockReturnValue(
        Result.ok(fakeBridge as unknown as BridgeService),
      );
      return sent;
    }

    it('sends the text of a tab only when it changed since the last message', () => {
      const uri = 'file:///workspace/sync.py';
      const sent = spyOnTabState();
      const sentForUri = () => sent.flatMap((tabs) => tabs.filter((t) => t.uri === uri));

      service.openTab(uri, 'first', 'python', false).unwrap();
      service.updateTabBar();
      const repeated = sentForUri().at(-1);
      editActiveTab('second');

      expect(
        sentForUri()
          .filter((t) => t.content !== undefined)
          .map((t) => [t.content, t.versionId]),
      ).toEqual([
        ['first', 1],
        ['second', 2],
      ]);
      expect(repeated).toEqual({
        uri,
        title: 'sync.py',
        isMain: false,
        isDirty: false,
        language: 'python',
      });
    });

    it('takeChangedMainSnapshot() answers only after the main text or main tab changed', () => {
      const other = 'file:///workspace/other_main.py';
      const first = service.takeChangedMainSnapshot();
      const again = service.takeChangedMainSnapshot();
      editActiveTab('edited main');
      const afterEdit = service.takeChangedMainSnapshot();
      service.openTab(other, 'other code', 'python', false).unwrap();
      service.setMainTab(other).unwrap();
      const afterSwitch = service.takeChangedMainSnapshot();

      expect(first?.uri).toBe('file:///workspace/script.py');
      expect(again).toBeUndefined();
      expect(afterEdit?.content).toBe('edited main');
      expect(afterSwitch).toEqual(expect.objectContaining({ uri: other, content: 'other code' }));
    });
  });

  describe('Editor Options Mapping', () => {
    it('updateOptionsFromMap() correctly maps configuration dictionary to Monaco editor and model options', () => {
      service.openTab('file:///workspace/opts.py', 'test', 'python', false);

      const payload: EditorOptionsPayload = {
        'editor.fontSize': 18,
        'editor.fontFamily': 'Cascadia Code, monospace',
        'editor.tabSize': 2,
        'editor.insertSpaces': false,
        'editor.wordWrap': 'on',
        'editor.lineNumbers': 'relative',
        'editor.minimap.enabled': false,
        'editor.renderWhitespace': 'all',
        'editor.cursorBlinking': 'expand',
        'editor.bracketPairColorization.enabled': false,
      };

      service.updateOptionsFromMap(payload);

      expect(editorState.lastCreatedEditor?.options.fontSize).toBe(18);
      expect(editorState.lastCreatedEditor?.options.fontFamily).toBe('Cascadia Code, monospace');
      expect(editorState.lastCreatedEditor?.options.wordWrap).toBe('on');
      expect(editorState.lastCreatedEditor?.options.lineNumbers).toBe('relative');
      expect(editorState.lastCreatedEditor?.options.minimap).toEqual({ enabled: false });
      expect(editorState.lastCreatedEditor?.options.renderWhitespace).toBe('all');
      expect(editorState.lastCreatedEditor?.options.cursorBlinking).toBe('expand');
      expect(editorState.lastCreatedEditor?.options.bracketPairColorization).toEqual({
        enabled: false,
      });

      const model = editorState.lastCreatedEditor?.currentModel;
      expect(model?.options.tabSize).toBe(2);
      expect(model?.options.insertSpaces).toBe(false);
    });

    it('applies tabSize and insertSpaces to models created later', () => {
      service.updateOptionsFromMap({ 'editor.tabSize': 2 });
      service.updateOptionsFromMap({ 'editor.insertSpaces': false });

      service.openTab('file:///workspace/later.py', 'x = 1', 'python', false).unwrap();
      expect(modelsMap.get('file:///workspace/later.py')?.options).toEqual({
        tabSize: 2,
        insertSpaces: false,
      });

      service.renameTab('file:///workspace/later.py', 'file:///workspace/renamed.py').unwrap();
      expect(modelsMap.get('file:///workspace/renamed.py')?.options).toEqual({
        tabSize: 2,
        insertSpaces: false,
      });
    });
  });

  describe('Dirty state tracking', () => {
    it('modifying model content marks tab dirty and fires onDidChangeContent', () => {
      service.openTab('file:///workspace/dirty_track.py', 'init', 'python', false);

      let contentChangedCalls = 0;
      service.onDidChangeContent(() => {
        contentChangedCalls++;
      });

      const tabBarContainer = document.getElementById(DOM_IDS.TAB_BAR)!;
      let activeTabEl = tabBarContainer.querySelector('.tab.active')!;
      expect(activeTabEl.classList.contains('dirty')).toBe(false);

      // Modify content -> marks dirty
      editActiveTab('changed content');

      expect(contentChangedCalls).toBeGreaterThanOrEqual(1);
      activeTabEl = tabBarContainer.querySelector('.tab.active')!;
      expect(activeTabEl.classList.contains('dirty')).toBe(true);

      // Save tab -> clears dirty state
      service.markTabSaved('file:///workspace/dirty_track.py', 'file:///workspace/dirty_track.py');
      activeTabEl = tabBarContainer.querySelector('.tab.active')!;
      expect(activeTabEl.classList.contains('dirty')).toBe(false);
    });
  });

  describe('General Editor Methods & Actions', () => {
    it('toggleWordWrap, focus, and layout', () => {
      expect(editorState.lastCreatedEditor?.options.wordWrap).toBe('off');
      service.toggleWordWrap();
      expect(editorState.lastCreatedEditor?.options.wordWrap).toBe('on');
      service.toggleWordWrap();
      expect(editorState.lastCreatedEditor?.options.wordWrap).toBe('off');

      service.focus();
      expect(editorState.lastCreatedEditor?.isFocused).toBe(true);

      service.layout();
      expect(editorState.lastCreatedEditor?.layoutCalled).toBe(true);
    });

    it('requestSave, requestSaveAs, and requestFormat fire corresponding events', () => {
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

  describe('Disposal', () => {
    it('dispose() cleans up all tab records, tab listeners, and owned models', () => {
      service.openTab('file:///workspace/t1.py', '1', 'python', false);
      service.openTab('file:///workspace/t2.py', '2', 'python', false);

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
