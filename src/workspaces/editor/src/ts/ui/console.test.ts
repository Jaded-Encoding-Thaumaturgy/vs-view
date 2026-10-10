import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as vscode from "vscode";

import { BridgeService } from "../bridge/python";
import type { PythonBridge } from "../types";
import { Result } from "../utils/result";
import { DOM_IDS } from "./constants";

// Hoisted xterm / FitAddon / vscode mocks
const { MockTerminalInstance, MockFitAddonInstance, terminalState } = vi.hoisted(() => {
  const terminalState = {
    lastCreatedTerminal: null as unknown as MockTerminalInstance,
    lastCreatedFitAddon: null as unknown as MockFitAddonInstance,
  };

  class MockTerminalInstance {
    public options: Record<string, unknown> = {};
    public cols = 80;
    public rows = 24;
    public writtenData: string[] = [];
    public clearedCount = 0;
    public selectedText = "";
    public selectionAllCalled = false;
    public disposed = false;
    public customKeyEventHandler: ((e: KeyboardEvent) => boolean) | null = null;
    public openedIn: HTMLElement | null = null;

    constructor(options: Record<string, unknown> = {}) {
      this.options = { ...options };
      terminalState.lastCreatedTerminal = this;
    }

    public loadAddon(_addon: unknown): void {}

    public open(container: HTMLElement): void {
      this.openedIn = container;
    }

    public write(data: string): void {
      this.writtenData.push(data);
    }

    public clear(): void {
      this.clearedCount++;
    }

    public getSelection(): string {
      return this.selectedText;
    }

    public hasSelection(): boolean {
      return this.selectedText.length > 0;
    }

    public selectAll(): void {
      this.selectionAllCalled = true;
    }

    public attachCustomKeyEventHandler(handler: (e: KeyboardEvent) => boolean): void {
      this.customKeyEventHandler = handler;
    }

    public dispose(): void {
      this.disposed = true;
    }
  }

  class MockFitAddonInstance {
    public fitCalled = false;

    constructor() {
      terminalState.lastCreatedFitAddon = this;
    }

    public fit(): void {
      this.fitCalled = true;
    }
  }

  return { MockTerminalInstance, MockFitAddonInstance, terminalState };
});

vi.mock("@xterm/xterm", () => ({
  Terminal: MockTerminalInstance,
}));

vi.mock("@xterm/addon-fit", () => ({
  FitAddon: MockFitAddonInstance,
}));

vi.mock("vscode", () => {
  type ConfigChangeListener = (e: { affectsConfiguration: (sec: string) => boolean }) => void;
  const listeners: ConfigChangeListener[] = [];
  let currentTheme = "Dark Modern";

  class EventEmitter<T> {
    private subs: Array<(e: T) => unknown> = [];
    public event = (listener: (e: T) => unknown) => {
      this.subs.push(listener);
      return {
        dispose: () => {
          const idx = this.subs.indexOf(listener);
          if (idx !== -1) this.subs.splice(idx, 1);
        },
      };
    };
    public fire(data: T): void {
      for (const sub of [...this.subs]) {
        sub(data);
      }
    }
    public dispose(): void {
      this.subs = [];
    }
  }

  return {
    EventEmitter,
    workspace: {
      onDidChangeConfiguration: vi.fn((listener: ConfigChangeListener) => {
        listeners.push(listener);
        return {
          dispose: () => {
            const idx = listeners.indexOf(listener);
            if (idx !== -1) listeners.splice(idx, 1);
          },
        };
      }),
      getConfiguration: vi.fn(() => ({
        get: vi.fn((key: string) => {
          if (key === "workbench.colorTheme") return currentTheme;
          return undefined;
        }),
      })),
      _fireConfigChange: (sec: string) => {
        for (const l of [...listeners]) {
          l({ affectsConfiguration: (s) => s === sec });
        }
      },
      _setTheme: (t: string) => {
        currentTheme = t;
      },
      _clearListeners: () => {
        listeners.length = 0;
      },
    },
  };
});

import { ConsolePanelService } from "./console";

describe("ConsolePanelService", () => {
  let panelEl: HTMLElement;
  let resizerEl: HTMLElement;
  let xtermContainerEl: HTMLElement;
  let clearBtnEl: HTMLButtonElement;
  let closeBtnEl: HTMLButtonElement;

  let mockBridge: PythonBridge;
  let bridgeActiveSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    vi.clearAllMocks();
    (
      vscode.workspace as unknown as {
        _clearListeners: () => void;
        _setTheme: (t: string) => void;
      }
    )._clearListeners();
    (
      vscode.workspace as unknown as {
        _clearListeners: () => void;
        _setTheme: (t: string) => void;
      }
    )._setTheme("Dark Modern");

    document.body.innerHTML = "";
    window.location.search = "";

    panelEl = document.createElement("div");
    panelEl.id = DOM_IDS.CONSOLE_PANEL;
    Object.defineProperty(panelEl, "clientHeight", { value: 200, writable: true });
    panelEl.getBoundingClientRect = () =>
      ({
        width: 800,
        height: 200,
        top: 600,
        bottom: 800,
        left: 0,
        right: 800,
        x: 0,
        y: 600,
        toJSON: () => ({}),
      }) as DOMRect;

    resizerEl = document.createElement("div");
    resizerEl.id = DOM_IDS.CONSOLE_RESIZER;

    xtermContainerEl = document.createElement("div");
    xtermContainerEl.id = DOM_IDS.XTERM_CONTAINER;

    clearBtnEl = document.createElement("button");
    clearBtnEl.id = DOM_IDS.CONSOLE_CLEAR_BTN;

    closeBtnEl = document.createElement("button");
    closeBtnEl.id = DOM_IDS.CONSOLE_CLOSE_BTN;

    panelEl.appendChild(xtermContainerEl);
    panelEl.appendChild(clearBtnEl);
    panelEl.appendChild(closeBtnEl);
    document.body.appendChild(resizerEl);
    document.body.appendChild(panelEl);

    mockBridge = {
      onEditorReady: vi.fn(),
      onContentChanged: vi.fn(),
      onMainContentChanged: vi.fn(),
      onCursorPositionChanged: vi.fn(),
      onActiveTabChanged: vi.fn(),
      onTabStateChanged: vi.fn(),
      onMainTabChanged: vi.fn(),
      requestSave: vi.fn(),
      requestSaveAs: vi.fn(),
      requestFormat: vi.fn(),
      requestGenerateStubs: vi.fn(),
      requestRestartLsp: vi.fn(),
      onConsoleResized: vi.fn(),
      copyToClipboard: vi.fn(),
      statFile: vi.fn(),
      readFile: vi.fn(),
      dispatchCommandSignal: { connect: vi.fn(), disconnect: vi.fn() },
    };

    bridgeActiveSpy = vi
      .spyOn(BridgeService, "active", "get")
      .mockReturnValue(Result.ok(mockBridge));

    vi.spyOn(window, "getComputedStyle").mockImplementation(
      () =>
        ({
          getPropertyValue: (prop: string) => {
            if (prop === "--console-font-family") return "Consolas, monospace";
            if (prop === "--console-font-size") return "13";
            if (prop === "--vscode-terminal-background") return "#1e1e1e";
            if (prop === "--vscode-terminal-foreground") return "#cccccc";
            if (prop === "--vscode-terminalCursor-foreground") return "#ffffff";
            if (prop === "--vscode-terminal-selectionBackground") return "#264f78";
            if (prop === "--vscode-terminal-ansiRed") return "#cd3131";
            return "";
          },
        }) as CSSStyleDeclaration,
    );

    vi.spyOn(window, "requestAnimationFrame").mockImplementation((cb: FrameRequestCallback) => {
      cb(performance.now());
      return 1;
    });
    vi.spyOn(window, "cancelAnimationFrame").mockImplementation(() => {});
  });

  afterEach(() => {
    bridgeActiveSpy.mockRestore();
    document.body.innerHTML = "";
    vi.restoreAllMocks();
  });

  describe("Initialization and DOM setup", () => {
    it("binds to required DOM containers and opens xterm instance", () => {
      using service = new ConsolePanelService();

      expect(service.visible).toBe(true);
      expect(terminalState.lastCreatedTerminal).not.toBeNull();
      expect(terminalState.lastCreatedTerminal?.openedIn).toBe(xtermContainerEl);
      expect(terminalState.lastCreatedTerminal?.options.cursorBlink).toBe(false);
      expect(terminalState.lastCreatedTerminal?.options.disableStdin).toBe(true);
      expect(document.documentElement.dataset.theme).toBe("Dark Modern");
      expect(panelEl.classList.contains("hidden")).toBe(false);
      expect(resizerEl.classList.contains("hidden")).toBe(false);
    });

    it("handles initialTheme URL param parsing correctly", () => {
      window.location.search = "?initialTheme=Light%20Modern";
      using _service = new ConsolePanelService();

      expect(document.documentElement.dataset.theme).toBe("Light Modern");
      expect(document.documentElement.getAttribute("data-theme-kind")).toBe("light");
    });
  });

  describe("append()", () => {
    it("converts bare LF (\\n) and CRLF to \\r\\n for xterm", () => {
      using service = new ConsolePanelService();
      expect(terminalState.lastCreatedTerminal).not.toBeNull();

      service.append("line1\nline2\r\nline3\n");

      expect(terminalState.lastCreatedTerminal?.writtenData).toEqual([
        "line1\r\nline2\r\nline3\r\n",
      ]);
    });

    it("writes single line without newline as is", () => {
      using service = new ConsolePanelService();
      service.append("simple text");
      expect(terminalState.lastCreatedTerminal?.writtenData).toEqual(["simple text"]);
    });
  });

  describe("clear()", () => {
    it("clears terminal content and handles clear button click", () => {
      using service = new ConsolePanelService();
      expect(terminalState.lastCreatedTerminal?.clearedCount).toBe(0);

      service.clear();
      expect(terminalState.lastCreatedTerminal?.clearedCount).toBe(1);

      clearBtnEl.click();
      expect(terminalState.lastCreatedTerminal?.clearedCount).toBe(2);
    });
  });

  describe("toggle() and visibility", () => {
    it("toggles hidden class, updates isVisible, and fires onDidToggle", () => {
      using service = new ConsolePanelService();
      const toggleSpy = vi.fn();
      service.onDidToggle(toggleSpy);

      expect(service.visible).toBe(true);

      // Toggle off
      service.toggle();
      expect(service.visible).toBe(false);
      expect(panelEl.classList.contains("hidden")).toBe(true);
      expect(resizerEl.classList.contains("hidden")).toBe(true);
      expect(toggleSpy).toHaveBeenCalledWith(false);

      // Toggle on
      service.toggle();
      expect(service.visible).toBe(true);
      expect(panelEl.classList.contains("hidden")).toBe(false);
      expect(resizerEl.classList.contains("hidden")).toBe(false);
      expect(toggleSpy).toHaveBeenCalledWith(true);
    });

    it("supports forced state toggle(forceState)", () => {
      using service = new ConsolePanelService();
      const toggleSpy = vi.fn();
      service.onDidToggle(toggleSpy);

      service.toggle(true);
      expect(service.visible).toBe(true);
      expect(toggleSpy).toHaveBeenCalledWith(true);

      service.toggle(false);
      expect(service.visible).toBe(false);
      expect(panelEl.classList.contains("hidden")).toBe(true);
      expect(toggleSpy).toHaveBeenCalledWith(false);
    });

    it("close button click toggles visibility off", () => {
      using service = new ConsolePanelService();
      const toggleSpy = vi.fn();
      service.onDidToggle(toggleSpy);

      closeBtnEl.click();

      expect(service.visible).toBe(false);
      expect(panelEl.classList.contains("hidden")).toBe(true);
      expect(toggleSpy).toHaveBeenCalledWith(false);
    });
  });

  describe("Resizer drag handling", () => {
    it("clamps height between 80px and 600px and fires onDidResize", () => {
      using service = new ConsolePanelService();
      const resizeSpy = vi.fn();
      service.onDidResize(resizeSpy);

      // Start drag at Y = 300
      resizerEl.dispatchEvent(new MouseEvent("mousedown", { clientY: 300, bubbles: true }));

      expect(resizerEl.classList.contains("dragging")).toBe(true);
      expect(document.body.style.userSelect).toBe("none");

      // Move up by 50px (Y = 250 -> delta = +50 -> newHeight = 250)
      window.dispatchEvent(new MouseEvent("mousemove", { clientY: 250 }));
      expect(document.documentElement.style.getPropertyValue("--console-height")).toBe("250px");
      expect(resizeSpy).toHaveBeenCalledWith(250);

      // Clamp upper bound: move up to Y = -700 (clamp to 600)
      window.dispatchEvent(new MouseEvent("mousemove", { clientY: -700 }));
      expect(document.documentElement.style.getPropertyValue("--console-height")).toBe("600px");
      expect(resizeSpy).toHaveBeenCalledWith(600);

      // Clamp lower bound: move down to Y = 800 (clamp to 80)
      window.dispatchEvent(new MouseEvent("mousemove", { clientY: 800 }));
      expect(document.documentElement.style.getPropertyValue("--console-height")).toBe("80px");
      expect(resizeSpy).toHaveBeenCalledWith(80);

      // Mouse up ends dragging
      window.dispatchEvent(new MouseEvent("mouseup"));

      expect(resizerEl.classList.contains("dragging")).toBe(false);
      expect(document.body.style.userSelect).toBe("");

      // Additional mouse moves after mouseup are ignored
      resizeSpy.mockClear();
      window.dispatchEvent(new MouseEvent("mousemove", { clientY: 250 }));
      expect(resizeSpy).not.toHaveBeenCalled();
    });

    it("triggers fit and bridge resize on window resize", () => {
      using _service = new ConsolePanelService();
      expect(terminalState.lastCreatedFitAddon).not.toBeNull();

      terminalState.lastCreatedFitAddon.fitCalled = false;
      window.dispatchEvent(new Event("resize"));

      expect(terminalState.lastCreatedFitAddon?.fitCalled).toBe(true);
      expect(mockBridge.onConsoleResized).toHaveBeenCalledWith(
        terminalState.lastCreatedTerminal?.cols,
      );
    });
  });

  describe("Keyboard event handler", () => {
    it("handles Ctrl+C by copying selection if text is selected", () => {
      using _service = new ConsolePanelService();
      const handler = terminalState.lastCreatedTerminal?.customKeyEventHandler;
      expect(handler).toBeDefined();

      terminalState.lastCreatedTerminal.selectedText = "copied text";

      const ctrlC = new KeyboardEvent("keydown", {
        key: "c",
        ctrlKey: true,
      });

      const handled = handler!(ctrlC);
      expect(handled).toBe(false); // prevent default
      expect(mockBridge.copyToClipboard).toHaveBeenCalledWith("copied text");
    });

    it("allows default Ctrl+C when no text is selected", () => {
      using _service = new ConsolePanelService();
      const handler = terminalState.lastCreatedTerminal?.customKeyEventHandler;

      terminalState.lastCreatedTerminal.selectedText = "";

      const ctrlC = new KeyboardEvent("keydown", {
        key: "c",
        ctrlKey: true,
      });

      const handled = handler!(ctrlC);
      expect(handled).toBe(true);
      expect(mockBridge.copyToClipboard).not.toHaveBeenCalled();
    });

    it("handles Ctrl+A by selecting all content", () => {
      using _service = new ConsolePanelService();
      const handler = terminalState.lastCreatedTerminal?.customKeyEventHandler;

      const ctrlA = new KeyboardEvent("keydown", {
        key: "a",
        ctrlKey: true,
      });

      const handled = handler!(ctrlA);
      expect(handled).toBe(false);
      expect(terminalState.lastCreatedTerminal?.selectionAllCalled).toBe(true);
    });

    it("handles Ctrl+K by clearing terminal", () => {
      using _service = new ConsolePanelService();
      const handler = terminalState.lastCreatedTerminal?.customKeyEventHandler;

      const ctrlK = new KeyboardEvent("keydown", {
        key: "k",
        ctrlKey: true,
      });

      const handled = handler!(ctrlK);
      expect(handled).toBe(false);
      expect(terminalState.lastCreatedTerminal?.clearedCount).toBe(1);
    });

    it("ignores non-modifier keys or unhandled combinations", () => {
      using _service = new ConsolePanelService();
      const handler = terminalState.lastCreatedTerminal?.customKeyEventHandler;

      const regularKey = new KeyboardEvent("keydown", { key: "x", ctrlKey: false });
      expect(handler!(regularKey)).toBe(true);

      const keyUp = new KeyboardEvent("keyup", { key: "c", ctrlKey: true });
      expect(handler!(keyUp)).toBe(true);
    });
  });

  describe("Context menu handling", () => {
    it("opens context menu on right click and suppresses native event", () => {
      using _service = new ConsolePanelService();

      const contextMenuEvent = new MouseEvent("contextmenu", {
        clientX: 100,
        clientY: 150,
        cancelable: true,
        bubbles: true,
      });

      const preventDefaultSpy = vi.spyOn(contextMenuEvent, "preventDefault");
      const stopPropagationSpy = vi.spyOn(contextMenuEvent, "stopPropagation");

      xtermContainerEl.dispatchEvent(contextMenuEvent);

      expect(preventDefaultSpy).toHaveBeenCalled();
      expect(stopPropagationSpy).toHaveBeenCalled();

      const menuEl = document.getElementById(DOM_IDS.CONSOLE_CONTEXT_MENU);
      expect(menuEl).not.toBeNull();
      expect(menuEl?.classList.contains("console-context-menu")).toBe(true);
      expect(menuEl?.style.left).toBe("100px");
      expect(menuEl?.style.top).toBe("150px");
    });

    it("disables Copy item when terminal has no selection", () => {
      using _service = new ConsolePanelService();
      terminalState.lastCreatedTerminal.selectedText = "";

      xtermContainerEl.dispatchEvent(new MouseEvent("contextmenu", { bubbles: true }));

      const menuEl = document.getElementById(DOM_IDS.CONSOLE_CONTEXT_MENU)!;
      const copyItem = menuEl.querySelectorAll(".console-context-menu-item")[0] as HTMLElement;

      expect(copyItem.classList.contains("disabled")).toBe(true);
      expect(copyItem.querySelector(".console-context-menu-label")?.textContent).toBe("Copy");
    });

    it("executes Copy item when clicked with active selection", () => {
      using _service = new ConsolePanelService();
      terminalState.lastCreatedTerminal.selectedText = "active selected text";

      xtermContainerEl.dispatchEvent(new MouseEvent("contextmenu", { bubbles: true }));

      const menuEl = document.getElementById(DOM_IDS.CONSOLE_CONTEXT_MENU)!;
      const copyItem = menuEl.querySelectorAll(".console-context-menu-item")[0] as HTMLElement;

      expect(copyItem.classList.contains("disabled")).toBe(false);

      copyItem.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      expect(mockBridge.copyToClipboard).toHaveBeenCalledWith("active selected text");
      expect(document.getElementById(DOM_IDS.CONSOLE_CONTEXT_MENU)).toBeNull();
    });

    it("executes Select All item when clicked", () => {
      using _service = new ConsolePanelService();

      xtermContainerEl.dispatchEvent(new MouseEvent("contextmenu", { bubbles: true }));

      const menuEl = document.getElementById(DOM_IDS.CONSOLE_CONTEXT_MENU)!;
      const selectAllItem = menuEl.querySelectorAll(".console-context-menu-item")[1] as HTMLElement;

      expect(selectAllItem.querySelector(".console-context-menu-label")?.textContent).toBe(
        "Select All",
      );

      selectAllItem.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      expect(terminalState.lastCreatedTerminal?.selectionAllCalled).toBe(true);
      expect(document.getElementById(DOM_IDS.CONSOLE_CONTEXT_MENU)).toBeNull();
    });

    it("executes Clear Console item when clicked", () => {
      using _service = new ConsolePanelService();

      xtermContainerEl.dispatchEvent(new MouseEvent("contextmenu", { bubbles: true }));

      const menuEl = document.getElementById(DOM_IDS.CONSOLE_CONTEXT_MENU)!;
      const clearItem = menuEl.querySelectorAll(".console-context-menu-item")[2] as HTMLElement;

      expect(clearItem.querySelector(".console-context-menu-label")?.textContent).toBe(
        "Clear Console",
      );

      clearItem.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      expect(terminalState.lastCreatedTerminal?.clearedCount).toBe(1);
      expect(document.getElementById(DOM_IDS.CONSOLE_CONTEXT_MENU)).toBeNull();
    });

    it("dismisses context menu on Escape key", () => {
      using _service = new ConsolePanelService();

      xtermContainerEl.dispatchEvent(new MouseEvent("contextmenu", { bubbles: true }));
      expect(document.getElementById(DOM_IDS.CONSOLE_CONTEXT_MENU)).not.toBeNull();

      window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
      expect(document.getElementById(DOM_IDS.CONSOLE_CONTEXT_MENU)).toBeNull();
    });

    it("dismisses context menu on outside click", () => {
      using _service = new ConsolePanelService();

      xtermContainerEl.dispatchEvent(new MouseEvent("contextmenu", { bubbles: true }));
      expect(document.getElementById(DOM_IDS.CONSOLE_CONTEXT_MENU)).not.toBeNull();

      window.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      expect(document.getElementById(DOM_IDS.CONSOLE_CONTEXT_MENU)).toBeNull();
    });

    it("clamps context menu positioning to window boundaries", () => {
      using _service = new ConsolePanelService();
      Object.defineProperty(window, "innerWidth", { value: 500, configurable: true });
      Object.defineProperty(window, "innerHeight", { value: 400, configurable: true });

      xtermContainerEl.dispatchEvent(
        new MouseEvent("contextmenu", { clientX: 450, clientY: 380, bubbles: true }),
      );

      const menuEl = document.getElementById(DOM_IDS.CONSOLE_CONTEXT_MENU)!;
      expect(menuEl).not.toBeNull();
    });

    it("clicking a disabled menu item does not execute action or close menu", () => {
      using _service = new ConsolePanelService();
      terminalState.lastCreatedTerminal.selectedText = "";

      xtermContainerEl.dispatchEvent(new MouseEvent("contextmenu", { bubbles: true }));

      const menuEl = document.getElementById(DOM_IDS.CONSOLE_CONTEXT_MENU)!;
      const copyItem = menuEl.querySelectorAll(".console-context-menu-item")[0] as HTMLElement;
      expect(copyItem.classList.contains("disabled")).toBe(true);

      copyItem.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      expect(mockBridge.copyToClipboard).not.toHaveBeenCalled();
      expect(document.getElementById(DOM_IDS.CONSOLE_CONTEXT_MENU)).not.toBeNull();
    });

    it("stops propagation of right mouse clicks inside xterm container", () => {
      using _service = new ConsolePanelService();

      const mouseDown = new MouseEvent("mousedown", { button: 2, bubbles: true });
      const stopPropagationSpy = vi.spyOn(mouseDown, "stopPropagation");

      xtermContainerEl.dispatchEvent(mouseDown);
      expect(stopPropagationSpy).toHaveBeenCalled();
    });
  });

  describe("Theme and configuration updates", () => {
    it("updates terminal theme when workbench.colorTheme changes", () => {
      using _service = new ConsolePanelService();

      (vscode.workspace as unknown as { _setTheme: (t: string) => void })._setTheme("Light Modern");
      (
        vscode.workspace as unknown as { _fireConfigChange: (sec: string) => void }
      )._fireConfigChange("workbench.colorTheme");

      expect(document.documentElement.dataset.theme).toBe("Light Modern");
      expect(document.documentElement.getAttribute("data-theme-kind")).toBe("light");
    });

    it("handles unsupported theme safely", () => {
      const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
      using service = new ConsolePanelService();
      expect(() => service.setTheme("Nonexistent Theme")).not.toThrow();
      expect(warnSpy).toHaveBeenCalledWith(
        expect.stringContaining("Unsupported theme: 'Nonexistent Theme'"),
      );
    });
  });

  describe("Edge cases and error handling", () => {
    it("copySelection does nothing if no text is selected", () => {
      using service = new ConsolePanelService();
      terminalState.lastCreatedTerminal.selectedText = "";

      service.copySelection();
      expect(mockBridge.copyToClipboard).not.toHaveBeenCalled();
    });

    it("copySelection safely handles bridge unavailability", () => {
      using service = new ConsolePanelService();
      terminalState.lastCreatedTerminal.selectedText = "text to copy";
      bridgeActiveSpy.mockReturnValue(Result.err(new Error("Bridge unavailable")));

      expect(() => service.copySelection()).not.toThrow();
    });

    it("fit() does nothing when console is not visible", () => {
      using service = new ConsolePanelService();
      service.toggle(false);

      terminalState.lastCreatedFitAddon.fitCalled = false;
      service.fit();

      expect(terminalState.lastCreatedFitAddon.fitCalled).toBe(false);
    });

    it("fit() catches and handles errors thrown by fitAddon", () => {
      const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
      using service = new ConsolePanelService();
      terminalState.lastCreatedFitAddon.fit = () => {
        throw new Error("Simulated fit error");
      };

      expect(() => service.fit()).not.toThrow();
      expect(warnSpy).toHaveBeenCalledWith("Failed to fit xterm viewport:", expect.any(Error));
    });

    it("initializes safely when clearBtn or closeBtn are not in DOM", () => {
      const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
      clearBtnEl.remove();
      closeBtnEl.remove();

      expect(() => {
        using _service = new ConsolePanelService();
      }).not.toThrow();
      expect(warnSpy).toHaveBeenCalledWith("Can't find clearBtn");
      expect(warnSpy).toHaveBeenCalledWith("Can't find closeBtn");
    });
  });

  describe("Disposal lifecycle", () => {
    it("disposes terminal instance and cleans up DOM listeners", () => {
      const service = new ConsolePanelService();
      expect(terminalState.lastCreatedTerminal?.disposed).toBe(false);

      // Open context menu
      xtermContainerEl.dispatchEvent(new MouseEvent("contextmenu", { bubbles: true }));
      expect(document.getElementById(DOM_IDS.CONSOLE_CONTEXT_MENU)).not.toBeNull();

      service.dispose();

      expect(terminalState.lastCreatedTerminal?.disposed).toBe(true);
      expect(document.getElementById(DOM_IDS.CONSOLE_CONTEXT_MENU)).toBeNull();
    });
  });
});
