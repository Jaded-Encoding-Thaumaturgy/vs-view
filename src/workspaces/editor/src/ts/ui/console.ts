import { FitAddon } from '@xterm/addon-fit';
import { type ITheme, Terminal } from '@xterm/xterm';
import { type TemplateResult, html, render } from 'lit-html';
import { choose } from 'lit-html/directives/choose.js';
import { classMap } from 'lit-html/directives/class-map.js';
import { repeat } from 'lit-html/directives/repeat.js';
import { styleMap } from 'lit-html/directives/style-map.js';
import * as vscode from 'vscode';
import { DisposableStore, Result, toDisposable } from 'vsview-shared';

import { BridgeService } from '../bridge/python';
import { getInitialTheme, getThemeDefinition } from '../utils/theme';
import { DOM_IDS } from './constants';

interface MenuActionItem {
  id: string;
  kind: 'action';
  label: string;
  shortcut: string;
  action: () => void;
  disabled?: boolean;
}

interface MenuSeparatorItem {
  id: string;
  kind: 'separator';
}

type MenuItem = MenuActionItem | MenuSeparatorItem;
type CssPropertyLookup = (...names: string[]) => string | undefined;

export class ConsolePanelService implements vscode.Disposable {
  private static readonly ANSI_COLOR_KEYS = [
    'black',
    'red',
    'green',
    'yellow',
    'blue',
    'magenta',
    'cyan',
    'white',
    'brightBlack',
    'brightRed',
    'brightGreen',
    'brightYellow',
    'brightBlue',
    'brightMagenta',
    'brightCyan',
    'brightWhite',
  ] as const;

  public readonly onDidToggle: vscode.Event<boolean>;
  public readonly onDidResize: vscode.Event<number>;

  private readonly disposables = new DisposableStore();
  private readonly terminal: Terminal;
  private readonly fitAddon: FitAddon;

  private readonly panelElement: HTMLElement;
  private readonly resizerElement: HTMLElement;
  private readonly xtermContainer: HTMLElement;

  private readonly onDidToggleEmitter = new vscode.EventEmitter<boolean>();
  private readonly onDidResizeEmitter = new vscode.EventEmitter<number>();

  private isVisible = true;
  private isDragging = false;
  private startY = 0;
  private startHeight = 200;
  private themeTimer: ReturnType<typeof setTimeout> | null = null;
  private fitFrameId: number | null = null;
  private contextMenuDisposables: DisposableStore | null = null;

  constructor() {
    this.onDidToggle = this.onDidToggleEmitter.event;
    this.onDidResize = this.onDidResizeEmitter.event;

    this.panelElement = document.getElementById(DOM_IDS.CONSOLE_PANEL)!;
    this.resizerElement = document.getElementById(DOM_IDS.CONSOLE_RESIZER)!;
    this.xtermContainer = document.getElementById(DOM_IDS.XTERM_CONTAINER)!;

    this.terminal = new Terminal({ cursorBlink: false, disableStdin: true });

    this.fitAddon = new FitAddon();
    this.terminal.loadAddon(this.fitAddon);
    this.terminal.open(this.xtermContainer);

    this.setTheme(getInitialTheme());

    this.setupUIListeners();
    this.disposables.add(this.onDidToggleEmitter);
    this.disposables.add(this.onDidResizeEmitter);
    this.disposables.add(toDisposable(() => this.terminal.dispose()));

    this.disposables.add(
      vscode.workspace.onDidChangeConfiguration((e) => {
        if (e.affectsConfiguration('workbench.colorTheme')) {
          const currentTheme = vscode.workspace
            .getConfiguration()
            .get<string>('workbench.colorTheme')!;
          this.setTheme(currentTheme);
        }
      }),
    );

    this.toggle(this.isVisible);
  }

  public get visible(): boolean {
    return this.isVisible;
  }

  public append(text: string): void {
    const formatted = text.replace(/\r?\n/g, '\r\n');
    this.terminal.write(formatted);
  }

  public clear(): void {
    this.terminal.clear();
  }

  public copySelection(): void {
    const selection = this.terminal.getSelection();
    if (!selection) return;
    BridgeService.active.unwrapOr(undefined)?.copyToClipboard(selection);
  }

  public toggle(forceState?: boolean): void {
    this.isVisible = forceState ?? !this.isVisible;

    if (this.isVisible) {
      this.panelElement.classList.remove('hidden');
      this.resizerElement.classList.remove('hidden');
      requestAnimationFrame(() => this.fit());
    } else {
      this.panelElement.classList.add('hidden');
      this.resizerElement.classList.add('hidden');
    }

    this.onDidToggleEmitter.fire(this.isVisible);
  }

  public fit(): void {
    if (this.isVisible) {
      Result.fromThrowable(() => this.fitAddon.fit()).match({
        ok: () => BridgeService.active.unwrapOr(undefined)?.onConsoleResized(this.terminal.cols),
        err: (e) => console.warn('Failed to fit xterm viewport:', e),
      });
    }
  }

  public setTheme(theme: string): void {
    getThemeDefinition(theme).match({
      ok: (def) => {
        document.documentElement.dataset.theme = def.id;
        document.documentElement.setAttribute('data-theme-kind', def.kind.toString());

        if (this.themeTimer !== null) {
          clearTimeout(this.themeTimer);
        }
        this.updateTerminalTheme();
        this.themeTimer = setTimeout(() => {
          this.themeTimer = null;
          this.updateTerminalTheme();
        }, 100);
      },
      err: (e) => console.warn(`Theme is undefined ${e}`),
    });
  }

  public updateTerminalTheme(): void {
    const styles = getComputedStyle(document.body);
    const getProp = (...names: string[]): string | undefined => {
      for (const name of names) {
        const val = styles.getPropertyValue(name).trim();
        if (val.length > 0) return val;
      }
      return undefined;
    };

    this.applyTerminalFontOptions(getProp);
    this.terminal.options.theme = this.buildTerminalTheme(getProp);
    this.scheduleFit();
  }

  public dispose(): void {
    this.closeContextMenu();
    if (this.themeTimer !== null) {
      clearTimeout(this.themeTimer);
      this.themeTimer = null;
    }
    if (this.fitFrameId !== null) {
      cancelAnimationFrame(this.fitFrameId);
      this.fitFrameId = null;
    }
    this.disposables.dispose();
  }

  public [Symbol.dispose](): void {
    this.dispose();
  }

  private applyTerminalFontOptions(getProp: CssPropertyLookup): void {
    const fontFamily = getProp('--console-font-family');
    const fontSizeStr = getProp('--console-font-size');
    const fontSize = fontSizeStr ? Number.parseFloat(fontSizeStr) : undefined;

    if (fontFamily) this.terminal.options.fontFamily = fontFamily;
    if (fontSize && !Number.isNaN(fontSize)) this.terminal.options.fontSize = fontSize;
  }

  private buildTerminalTheme(getProp: CssPropertyLookup): ITheme {
    const bg = getProp(
      '--vscode-terminal-background',
      '--vscode-panel-background',
      '--vscode-editor-background',
    );
    const fg = getProp('--vscode-terminal-foreground', '--vscode-foreground');
    const cursor =
      getProp('--vscode-terminalCursor-foreground', '--vscode-editorCursor-foreground') ?? fg;
    const selectionBackground = getProp(
      '--vscode-terminal-selectionBackground',
      '--vscode-editor-selectionBackground',
    );

    const themeObj: ITheme = {};
    if (bg) themeObj.background = bg;
    if (fg) themeObj.foreground = fg;
    if (cursor) themeObj.cursor = cursor;
    if (selectionBackground) themeObj.selectionBackground = selectionBackground;

    for (const key of ConsolePanelService.ANSI_COLOR_KEYS) {
      const val = getProp(`--vscode-terminal-ansi${key.charAt(0).toUpperCase()}${key.slice(1)}`);
      if (val) themeObj[key] = val;
    }

    return themeObj;
  }

  private scheduleFit(): void {
    if (this.fitFrameId !== null) cancelAnimationFrame(this.fitFrameId);
    this.fitFrameId = requestAnimationFrame(() => {
      this.fitFrameId = null;
      this.fit();
    });
  }

  private setupUIListeners(): void {
    this.setupKeyboardShortcuts();
    this.setupMouseAndContextMenuListeners();
    this.setupHeaderButtonListeners();
    this.setupResizerDragListeners();
  }

  private setupKeyboardShortcuts(): void {
    this.terminal.attachCustomKeyEventHandler((event: KeyboardEvent) => {
      if (event.type === 'keydown') {
        const isModifier = event.ctrlKey || event.metaKey;
        const key = event.key.toLowerCase();

        if (isModifier && key === 'c') {
          if (this.terminal.hasSelection()) {
            this.copySelection();
            return false;
          }
        } else if (isModifier && key === 'a') {
          this.terminal.selectAll();
          return false;
        } else if (isModifier && key === 'k') {
          this.clear();
          return false;
        }
      }
      return true;
    });
  }

  private setupMouseAndContextMenuListeners(): void {
    const handleMouseEvents = (e: MouseEvent) => {
      if (e.button === 2) e.stopPropagation();
    };

    this.xtermContainer.addEventListener('mousedown', handleMouseEvents, true);
    this.xtermContainer.addEventListener('mouseup', handleMouseEvents, true);
    this.disposables.add(
      toDisposable(() => {
        this.xtermContainer.removeEventListener('mousedown', handleMouseEvents, true);
        this.xtermContainer.removeEventListener('mouseup', handleMouseEvents, true);
      }),
    );

    const handleContextMenu = (e: MouseEvent) => {
      e.preventDefault();
      e.stopPropagation();
      this.showContextMenu(e.clientX, e.clientY);
    };

    this.xtermContainer.addEventListener('contextmenu', handleContextMenu);
    this.disposables.add(
      toDisposable(() => this.xtermContainer.removeEventListener('contextmenu', handleContextMenu)),
    );
  }

  private setupHeaderButtonListeners(): void {
    const clearBtn = document.getElementById(DOM_IDS.CONSOLE_CLEAR_BTN);
    if (clearBtn) {
      const handleClear = () => this.clear();
      clearBtn.addEventListener('click', handleClear);
      this.disposables.add(toDisposable(() => clearBtn.removeEventListener('click', handleClear)));
    } else {
      console.warn("Can't find clearBtn");
    }

    const closeBtn = document.getElementById(DOM_IDS.CONSOLE_CLOSE_BTN);
    if (closeBtn) {
      const handleClose = () => this.toggle(false);
      closeBtn.addEventListener('click', handleClose);
      this.disposables.add(toDisposable(() => closeBtn.removeEventListener('click', handleClose)));
    } else {
      console.warn("Can't find closeBtn");
    }
  }

  private setupResizerDragListeners(): void {
    const cleanupDrag = () => {
      if (this.isDragging) {
        this.isDragging = false;
        this.resizerElement.classList.remove('dragging');
        document.body.style.userSelect = '';
        window.removeEventListener('mousemove', onMouseMove);
        window.removeEventListener('mouseup', onMouseUp);
      }
    };

    const onMouseMove = (e: MouseEvent) => {
      if (!this.isDragging) return;

      const deltaY = this.startY - e.clientY;
      const newHeight = Math.max(80, Math.min(600, this.startHeight + deltaY));

      document.documentElement.style.setProperty('--console-height', `${newHeight}px`);

      this.fit();
      this.onDidResizeEmitter.fire(newHeight);
    };

    const onMouseUp = () => {
      if (!this.isDragging) return;
      cleanupDrag();
      this.fit();
    };

    const onMouseDown = (e: MouseEvent) => {
      this.isDragging = true;
      this.startY = e.clientY;
      this.startHeight = this.panelElement.getBoundingClientRect().height;

      this.resizerElement.classList.add('dragging');
      document.body.style.userSelect = 'none';

      window.addEventListener('mousemove', onMouseMove);
      window.addEventListener('mouseup', onMouseUp);
    };

    this.resizerElement.addEventListener('mousedown', onMouseDown);
    this.disposables.add(
      toDisposable(() => {
        this.resizerElement.removeEventListener('mousedown', onMouseDown);
        cleanupDrag();
      }),
    );

    const onWindowResize = () => this.fit();
    window.addEventListener('resize', onWindowResize);
    this.disposables.add(toDisposable(() => window.removeEventListener('resize', onWindowResize)));
  }

  private getContextMenuItems(): MenuItem[] {
    return [
      {
        id: 'copy',
        kind: 'action',
        label: 'Copy',
        shortcut: 'Ctrl+C',
        action: () => this.copySelection(),
        disabled: !this.terminal.hasSelection(),
      },
      { id: 'sep-1', kind: 'separator' },
      {
        id: 'select-all',
        kind: 'action',
        label: 'Select All',
        shortcut: 'Ctrl+A',
        action: () => this.terminal.selectAll(),
      },
      { id: 'sep-2', kind: 'separator' },
      {
        id: 'clear',
        kind: 'action',
        label: 'Clear Console',
        shortcut: 'Ctrl+K',
        action: () => this.clear(),
      },
    ];
  }

  private showContextMenu(x: number, y: number): void {
    this.closeContextMenu();

    const fragment = document.createDocumentFragment();
    render(this.renderContextMenu(x, y, this.getContextMenuItems()), fragment);
    document.body.appendChild(fragment);

    const menu = document.getElementById(DOM_IDS.CONSOLE_CONTEXT_MENU);
    if (menu) {
      this.clampContextMenuPosition(menu, x, y);
    }

    this.bindContextMenuDismissListeners();
  }

  private renderContextMenu(x: number, y: number, items: MenuItem[]): TemplateResult {
    return html`
      <div
        id=${DOM_IDS.CONSOLE_CONTEXT_MENU}
        class="console-context-menu"
        style=${styleMap({ left: `${x}px`, top: `${y}px` })}
      >
        ${repeat(
          items,
          (item) => item.id,
          (item) => this.renderContextMenuEntry(item),
        )}
      </div>
    `;
  }

  private renderContextMenuEntry(item: MenuItem): TemplateResult {
    return html`${choose(item.kind, [
      ['separator', () => html`<div class="console-context-menu-separator"></div>`],
      ['action', () => this.renderContextMenuAction(item as MenuActionItem)],
    ])}`;
  }

  private renderContextMenuAction(item: MenuActionItem): TemplateResult {
    const classes = {
      'console-context-menu-item': true,
      disabled: Boolean(item.disabled),
    };

    return html`
      <div
        class=${classMap(classes)}
        @click=${(e: MouseEvent) => this.handleContextMenuItemClick(e, item)}
      >
        <span class="console-context-menu-label">${item.label}</span>
        <span class="console-context-menu-shortcut">${item.shortcut}</span>
      </div>
    `;
  }

  private handleContextMenuItemClick(e: MouseEvent, item: MenuActionItem): void {
    e.stopPropagation();
    if (!item.disabled) {
      this.closeContextMenu();
      item.action();
    }
  }

  private clampContextMenuPosition(menu: HTMLElement, x: number, y: number): void {
    const rect = menu.getBoundingClientRect();
    const finalX =
      x + rect.width > window.innerWidth ? Math.max(0, window.innerWidth - rect.width - 4) : x;
    const finalY =
      y + rect.height > window.innerHeight ? Math.max(0, window.innerHeight - rect.height - 4) : y;

    menu.style.left = `${finalX}px`;
    menu.style.top = `${finalY}px`;
  }

  private bindContextMenuDismissListeners(): void {
    this.contextMenuDisposables = new DisposableStore();

    const onWindowClick = () => this.closeContextMenu();
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        this.closeContextMenu();
      }
    };

    window.addEventListener('click', onWindowClick, { once: true });
    window.addEventListener('keydown', onKeyDown);

    this.contextMenuDisposables.add(
      toDisposable(() => {
        window.removeEventListener('click', onWindowClick);
        window.removeEventListener('keydown', onKeyDown);
      }),
    );
  }

  private closeContextMenu(): void {
    document.getElementById(DOM_IDS.CONSOLE_CONTEXT_MENU)?.remove();
    if (this.contextMenuDisposables) {
      this.contextMenuDisposables.dispose();
      this.contextMenuDisposables = null;
    }
  }
}
