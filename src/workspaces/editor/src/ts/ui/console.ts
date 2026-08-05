import { FitAddon } from "@xterm/addon-fit";
import { type ITheme, Terminal } from "@xterm/xterm";
import * as vscode from "vscode";

import { BridgeService } from "../bridge/python";
import { DisposableStore, toDisposable } from "../utils/disposables";
import { Result } from "../utils/result";
import { getThemeDefinition } from "../utils/theme";
import { DOM_IDS } from "./constants";

type MenuItem =
  | { label: string; shortcut: string; action: () => void; disabled?: boolean }
  | "separator";

export class ConsolePanelService implements vscode.Disposable {
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

    const initialTheme = new URLSearchParams(window.location.search).get("initialTheme")!;
    this.setTheme(initialTheme);

    this.setupUIListeners();
    this.disposables.add(this.onDidToggleEmitter);
    this.disposables.add(this.onDidResizeEmitter);
    this.disposables.add(toDisposable(() => this.terminal.dispose()));

    this.disposables.add(
      vscode.workspace.onDidChangeConfiguration((e) => {
        if (e.affectsConfiguration("workbench.colorTheme")) {
          const currentTheme = vscode.workspace
            .getConfiguration()
            .get<string>("workbench.colorTheme")!;
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
    const formatted = text.replace(/\r?\n/g, "\r\n");
    this.terminal.write(formatted);
  }

  public clear(): void {
    this.terminal.clear();
  }

  public copySelection(): void {
    const selection = this.terminal.getSelection();
    if (!selection) return;
    Result.tap(BridgeService.getActiveBridge(), (bridge) => bridge.copyToClipboard(selection));
  }

  public toggle(forceState?: boolean): void {
    this.isVisible = forceState !== undefined ? forceState : !this.isVisible;

    if (this.isVisible) {
      this.panelElement.classList.remove("hidden");
      this.resizerElement.classList.remove("hidden");
      requestAnimationFrame(() => this.fit());
    } else {
      this.panelElement.classList.add("hidden");
      this.resizerElement.classList.add("hidden");
    }

    this.onDidToggleEmitter.fire(this.isVisible);
  }

  public fit(): void {
    if (this.isVisible) {
      const resFit = Result.fromThrowable(() => this.fitAddon.fit());
      if (!resFit.ok) {
        console.warn("Failed to fit xterm viewport:", resFit.error);
      } else {
        Result.tap(BridgeService.getActiveBridge(), (bridge) =>
          bridge.onConsoleResized(this.terminal.cols),
        );
      }
    }
  }

  public setTheme(theme: string): void {
    const resDef = getThemeDefinition(theme);
    if (!resDef.ok) {
      console.warn(`Theme is undefined ${resDef.error}`);
      return;
    }
    const def = resDef.value;
    document.documentElement.dataset.theme = def.id;
    document.documentElement.setAttribute("data-theme-kind", def.kind.toString());

    if (this.themeTimer !== null) {
      clearTimeout(this.themeTimer);
    }
    this.updateTerminalTheme();
    this.themeTimer = setTimeout(() => {
      this.themeTimer = null;
      this.updateTerminalTheme();
    }, 100);
  }

  public updateTerminalTheme(): void {
    const styles = getComputedStyle(document.body);

    /**
     * Helper to safely extract non-empty CSS variable values
     */
    const getProp = (...names: string[]): string | undefined => {
      for (const name of names) {
        const val = styles.getPropertyValue(name).trim();
        if (val.length > 0) return val;
      }
      return undefined;
    };

    // Font Options
    const fontFamily = getProp("--console-font-family");
    const fontSizeStr = getProp("--console-font-size");
    const fontSize = fontSizeStr ? Number.parseFloat(fontSizeStr) : undefined;

    if (fontFamily) this.terminal.options.fontFamily = fontFamily;
    if (fontSize && !Number.isNaN(fontSize)) this.terminal.options.fontSize = fontSize;

    const bg = getProp(
      "--vscode-terminal-background",
      "--vscode-panel-background",
      "--vscode-editor-background",
    );
    const fg = getProp("--vscode-terminal-foreground", "--vscode-foreground");
    const cursor =
      getProp("--vscode-terminalCursor-foreground", "--vscode-editorCursor-foreground") || fg;
    const selectionBackground = getProp(
      "--vscode-terminal-selectionBackground",
      "--vscode-editor-selectionBackground",
    );

    const themeObj: ITheme = {};
    if (bg) themeObj.background = bg;
    if (fg) themeObj.foreground = fg;
    if (cursor) themeObj.cursor = cursor;
    if (selectionBackground) themeObj.selectionBackground = selectionBackground;

    // 16 Standard ANSI Colors Mapping
    const colorKeys = [
      "black",
      "red",
      "green",
      "yellow",
      "blue",
      "magenta",
      "cyan",
      "white",
      "brightBlack",
      "brightRed",
      "brightGreen",
      "brightYellow",
      "brightBlue",
      "brightMagenta",
      "brightCyan",
      "brightWhite",
    ] as const;

    for (const key of colorKeys) {
      // Capitalize first letter to build VS Code variable name: --vscode-terminal-ansiXxx
      const val = getProp(`--vscode-terminal-ansi${key.charAt(0).toUpperCase()}${key.slice(1)}`);
      if (val) themeObj[key] = val;
    }

    this.terminal.options.theme = themeObj;

    if (this.fitFrameId !== null) cancelAnimationFrame(this.fitFrameId);
    this.fitFrameId = requestAnimationFrame(() => {
      this.fitFrameId = null;
      this.fit();
    });
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

  private setupUIListeners(): void {
    this.terminal.attachCustomKeyEventHandler((event: KeyboardEvent) => {
      if (event.type === "keydown") {
        const isModifier = event.ctrlKey || event.metaKey;
        const key = event.key.toLowerCase();

        if (isModifier && key === "c") {
          if (this.terminal.hasSelection()) {
            this.copySelection();
            return false;
          }
        } else if (isModifier && key === "a") {
          this.terminal.selectAll();
          return false;
        } else if (isModifier && key === "k") {
          this.clear();
          return false;
        }
      }
      return true;
    });

    const handleMouseEvents = (e: MouseEvent) => {
      if (e.button === 2) e.stopPropagation();
    };

    this.xtermContainer.addEventListener("mousedown", handleMouseEvents, true);
    this.xtermContainer.addEventListener("mouseup", handleMouseEvents, true);
    this.disposables.add(
      toDisposable(() => {
        this.xtermContainer.removeEventListener("mousedown", handleMouseEvents, true);
        this.xtermContainer.removeEventListener("mouseup", handleMouseEvents, true);
      }),
    );

    const handleContextMenu = (e: MouseEvent) => {
      e.preventDefault();
      e.stopPropagation();
      this.showContextMenu(e.clientX, e.clientY);
    };

    this.xtermContainer.addEventListener("contextmenu", handleContextMenu);
    this.disposables.add(
      toDisposable(() => this.xtermContainer.removeEventListener("contextmenu", handleContextMenu)),
    );

    const clearBtn = document.getElementById(DOM_IDS.CONSOLE_CLEAR_BTN);
    if (clearBtn) {
      const handleClear = () => this.clear();
      clearBtn.addEventListener("click", handleClear);
      this.disposables.add(toDisposable(() => clearBtn.removeEventListener("click", handleClear)));
    } else {
      console.warn("Can't find clearBtn");
    }

    const closeBtn = document.getElementById(DOM_IDS.CONSOLE_CLOSE_BTN);
    if (closeBtn) {
      const handleClose = () => this.toggle(false);
      closeBtn.addEventListener("click", handleClose);
      this.disposables.add(toDisposable(() => closeBtn.removeEventListener("click", handleClose)));
    } else {
      console.warn("Can't find closeBtn");
    }

    const cleanupDrag = () => {
      if (this.isDragging) {
        this.isDragging = false;
        this.resizerElement.classList.remove("dragging");
        document.body.style.userSelect = "";
        window.removeEventListener("mousemove", onMouseMove);
        window.removeEventListener("mouseup", onMouseUp);
      }
    };

    const onMouseMove = (e: MouseEvent) => {
      if (!this.isDragging) return;

      const deltaY = this.startY - e.clientY;
      const newHeight = Math.max(80, Math.min(600, this.startHeight + deltaY));

      document.documentElement.style.setProperty("--console-height", `${newHeight}px`);

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

      this.resizerElement.classList.add("dragging");
      document.body.style.userSelect = "none";

      window.addEventListener("mousemove", onMouseMove);
      window.addEventListener("mouseup", onMouseUp);
    };

    this.resizerElement.addEventListener("mousedown", onMouseDown);
    this.disposables.add(
      toDisposable(() => {
        this.resizerElement.removeEventListener("mousedown", onMouseDown);
        cleanupDrag();
      }),
    );

    const onWindowResize = () => this.fit();
    window.addEventListener("resize", onWindowResize);
    this.disposables.add(toDisposable(() => window.removeEventListener("resize", onWindowResize)));
  }

  private showContextMenu(x: number, y: number): void {
    this.closeContextMenu();

    this.contextMenuDisposables = new DisposableStore();
    const menu = document.createElement("div");
    menu.id = DOM_IDS.CONSOLE_CONTEXT_MENU;
    menu.className = "console-context-menu";

    const items: MenuItem[] = [
      {
        label: "Copy",
        shortcut: "Ctrl+C",
        action: () => this.copySelection(),
        disabled: !this.terminal.hasSelection(),
      },
      "separator",
      {
        label: "Select All",
        shortcut: "Ctrl+A",
        action: () => this.terminal.selectAll(),
      },
      "separator",
      {
        label: "Clear Console",
        shortcut: "Ctrl+K",
        action: () => this.clear(),
      },
    ];

    for (const item of items) {
      if (item === "separator") {
        const separator = document.createElement("div");
        separator.className = "console-context-menu-separator";
        menu.appendChild(separator);
        continue;
      }

      const itemEl = document.createElement("div");
      itemEl.className = "console-context-menu-item";
      if (item.disabled) itemEl.classList.add("disabled");

      const labelSpan = document.createElement("span");
      labelSpan.className = "console-context-menu-label";
      labelSpan.textContent = item.label;
      itemEl.appendChild(labelSpan);

      const shortcutSpan = document.createElement("span");
      shortcutSpan.className = "console-context-menu-shortcut";
      shortcutSpan.textContent = item.shortcut;
      itemEl.appendChild(shortcutSpan);

      if (!item.disabled) {
        const onItemClick = (e: MouseEvent) => {
          e.stopPropagation();
          this.closeContextMenu();
          item.action();
        };
        itemEl.addEventListener("click", onItemClick);
        this.contextMenuDisposables.add(
          toDisposable(() => itemEl.removeEventListener("click", onItemClick)),
        );
      }

      menu.appendChild(itemEl);
    }

    document.body.appendChild(menu);

    const rect = menu.getBoundingClientRect();

    let finalX = x;
    let finalY = y;

    if (x + rect.width > window.innerWidth) {
      finalX = Math.max(0, window.innerWidth - rect.width - 4);
    }
    if (y + rect.height > window.innerHeight) {
      finalY = Math.max(0, window.innerHeight - rect.height - 4);
    }

    menu.style.left = `${finalX}px`;
    menu.style.top = `${finalY}px`;

    const onWindowClick = () => this.closeContextMenu();
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        this.closeContextMenu();
      }
    };

    window.addEventListener("click", onWindowClick, { once: true });
    window.addEventListener("keydown", onKeyDown);

    this.contextMenuDisposables.add(
      toDisposable(() => {
        window.removeEventListener("click", onWindowClick);
        window.removeEventListener("keydown", onKeyDown);
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
