import type * as vscode from "vscode";

import type { TabInfo } from "../types";
import { DisposableStore, toDisposable } from "../utils/disposables";
import { DOM_IDS } from "./constants";

export type TabBarCallbacks = {
  onSelectTab: (uri: string) => void;
  onCloseTab: (uri: string) => void;
  onSetMainTab: (uri: string) => void;
};

export class TabBarView implements vscode.Disposable {
  private readonly disposables = new DisposableStore();
  private readonly container: HTMLElement;
  private contextMenuDisposables: DisposableStore | null = null;

  constructor(private readonly callbacks: TabBarCallbacks) {
    this.container = document.getElementById(DOM_IDS.TAB_BAR)!;
  }

  public render(tabs: TabInfo[], activeUri: string | null): void {
    this.closeContextMenu();

    const fragment = document.createDocumentFragment();

    for (const tab of tabs) {
      const tabEl = document.createElement("div");
      tabEl.className = "tab";
      if (tab.uri === activeUri) {
        tabEl.classList.add("active");
      }
      if (tab.isMain) {
        tabEl.classList.add("main");
      }
      if (tab.isDirty) {
        tabEl.classList.add("dirty");
      }

      // Main Badge
      if (tab.isMain) {
        const mainBadge = document.createElement("span");
        mainBadge.className = "tab-main-badge";
        mainBadge.textContent = "MAIN";
        tabEl.appendChild(mainBadge);
      }

      // Title
      const titleEl = document.createElement("span");
      titleEl.className = "tab-title";
      titleEl.textContent = tab.title;
      titleEl.title = tab.uri;
      tabEl.appendChild(titleEl);

      // Indicator Container (Dirty dot / Close button)
      const indicatorEl = document.createElement("span");
      indicatorEl.className = "tab-indicator";

      if (tab.isDirty) {
        const dirtyDot = document.createElement("span");
        dirtyDot.className = "tab-dirty-dot";
        indicatorEl.appendChild(dirtyDot);
      }

      const closeBtn = document.createElement("span");
      closeBtn.className = "tab-close";
      closeBtn.textContent = "\u00D7";
      closeBtn.title = "Close tab";
      closeBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        this.callbacks.onCloseTab(tab.uri);
      });
      indicatorEl.appendChild(closeBtn);

      tabEl.appendChild(indicatorEl);

      // Click to select tab
      tabEl.addEventListener("click", () => {
        this.callbacks.onSelectTab(tab.uri);
      });

      // Context menu for "Set as Main Script"
      tabEl.addEventListener("contextmenu", (e) => {
        e.preventDefault();
        if (!tab.isMain) {
          this.showContextMenu(e.clientX, e.clientY, tab.uri);
        }
      });
      fragment.appendChild(tabEl);
    }
    this.container.replaceChildren(fragment);
  }

  public dispose(): void {
    this.closeContextMenu();
    this.disposables.dispose();
  }

  public [Symbol.dispose](): void {
    this.dispose();
  }

  private showContextMenu(x: number, y: number, uri: string): void {
    this.closeContextMenu();

    this.contextMenuDisposables = new DisposableStore();

    const menu = document.createElement("div");
    menu.id = DOM_IDS.TAB_CONTEXT_MENU;
    menu.className = "tab-context-menu";
    menu.style.left = `${x}px`;
    menu.style.top = `${y}px`;

    const item = document.createElement("div");
    item.className = "tab-context-menu-item";
    item.textContent = "⚡ Set as Main Script";

    const onItemClick = () => {
      this.callbacks.onSetMainTab(uri);
      this.closeContextMenu();
    };
    item.addEventListener("click", onItemClick);
    this.contextMenuDisposables.add(
      toDisposable(() => item.removeEventListener("click", onItemClick)),
    );

    menu.appendChild(item);
    document.body.appendChild(menu);

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
    document.getElementById(DOM_IDS.TAB_CONTEXT_MENU)?.remove();
    if (this.contextMenuDisposables) {
      this.contextMenuDisposables.dispose();
      this.contextMenuDisposables = null;
    }
  }
}
