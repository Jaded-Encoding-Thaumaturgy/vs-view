import './console.css';
import { FitAddon } from '@xterm/addon-fit';
import { type ITheme, Terminal } from '@xterm/xterm';
import * as vscode from 'vscode';
import { DisposableStore, Result, toDisposable } from 'vsview-shared';

import { BridgeService } from '../bridge/python';
import { applyDocumentTheme, getInitialTheme, getThemeDefinition } from '../utils/theme';
import { DOM_IDS } from './constants';
import { type MenuItem, PopoverMenu } from './popover-menu';

type CssPropertyLookup = (...names: string[]) => string | undefined;

export class ConsolePanelService implements vscode.Disposable {
  private static readonly MIN_HEIGHT = 80;
  private static readonly MAX_HEIGHT = 600;
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
  private readonly contextMenu = new PopoverMenu({
    id: DOM_IDS.CONSOLE_CONTEXT_MENU,
    label: 'Console actions',
  });

  private isVisible = true;
  private dragPointerId: number | null = null;
  private startY = 0;
  private startHeight = 200;
  private themeTimer: ReturnType<typeof setTimeout> | null = null;
  private fitFrameId: number | null = null;

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
    this.disposables.add(this.contextMenu);
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
    BridgeService.active.unwrapOr(undefined)?.sendEvent('clipboard.copy', { text: selection });
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
        ok: () =>
          void BridgeService.active
            .unwrapOr(undefined)
            ?.sendEvent('console.resized', { cols: this.terminal.cols }),
        err: (e) => console.warn('Failed to fit xterm viewport:', e),
      });
    }
  }

  public setTheme(theme: string): void {
    getThemeDefinition(theme).match({
      ok: (def) => {
        applyDocumentTheme(def);

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
      this.contextMenu.show(e.clientX, e.clientY, this.getContextMenuItems());
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
    const finishDrag = (): void => {
      if (this.dragPointerId === null) return;

      this.dragPointerId = null;
      this.resizerElement.classList.remove('dragging');
      document.body.style.userSelect = '';
      window.removeEventListener('pointermove', onPointerMove);
      window.removeEventListener('pointerup', onPointerEnd);
      window.removeEventListener('pointercancel', onPointerEnd);
    };

    const onPointerMove = (e: PointerEvent): void => {
      if (e.pointerId !== this.dragPointerId) return;

      this.setHeight(this.startHeight + this.startY - e.clientY);
    };

    const onPointerEnd = (e: PointerEvent): void => {
      if (e.pointerId !== this.dragPointerId) return;

      finishDrag();
      this.fit();
    };

    const onPointerDown = (e: PointerEvent): void => {
      if (e.button !== 0) return;

      e.preventDefault();
      this.dragPointerId = e.pointerId;
      this.startY = e.clientY;
      this.startHeight = this.panelElement.getBoundingClientRect().height;

      this.resizerElement.classList.add('dragging');
      document.body.style.userSelect = 'none';

      window.addEventListener('pointermove', onPointerMove);
      window.addEventListener('pointerup', onPointerEnd);
      window.addEventListener('pointercancel', onPointerEnd);
    };

    this.resizerElement.addEventListener('pointerdown', onPointerDown);
    this.disposables.add(
      toDisposable(() => {
        this.resizerElement.removeEventListener('pointerdown', onPointerDown);
        finishDrag();
      }),
    );

    const onWindowResize = () => this.fit();
    window.addEventListener('resize', onWindowResize);
    this.disposables.add(toDisposable(() => window.removeEventListener('resize', onWindowResize)));
  }

  private setHeight(requested: number): void {
    const height = Math.max(
      ConsolePanelService.MIN_HEIGHT,
      Math.min(ConsolePanelService.MAX_HEIGHT, requested),
    );

    document.documentElement.style.setProperty('--console-height', `${height}px`);

    this.fit();
    this.onDidResizeEmitter.fire(height);
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
}
