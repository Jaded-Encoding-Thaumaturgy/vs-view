import { type TemplateResult, html, nothing, render } from 'lit-html';
import { classMap } from 'lit-html/directives/class-map.js';
import { repeat } from 'lit-html/directives/repeat.js';
import { styleMap } from 'lit-html/directives/style-map.js';
import { unsafeHTML } from 'lit-html/directives/unsafe-html.js';
import { when } from 'lit-html/directives/when.js';
import type * as vscode from 'vscode';
import { DisposableStore, toDisposable } from 'vsview-shared';

import xIconSvg from '../assets/icons/lucide/x.svg?raw';
import type { TabInfo } from '../types';
import { DOM_IDS } from './constants';

export type TabBarCallbacks = {
  onSelectTab: (uri: string) => void;
  onCloseTab: (uri: string) => void;
  onCloseOtherTabs: (uri: string) => void;
  onCloseSavedTabs: () => void;
  onSetMainTab: (uri: string) => void;
};

interface TabMenuActionItem {
  id: string;
  kind: 'action';
  label: string;
  action: () => void;
  disabled?: boolean;
}

interface TabMenuSeparatorItem {
  id: string;
  kind: 'separator';
}

type TabMenuItem = TabMenuActionItem | TabMenuSeparatorItem;

export class TabBarView implements vscode.Disposable {
  private readonly disposables = new DisposableStore();
  private readonly container: HTMLElement;
  private contextMenuDisposables: DisposableStore | null = null;
  private currentTabs: TabInfo[] = [];
  private lastActiveUri: string | null = null;

  constructor(private readonly callbacks: TabBarCallbacks) {
    this.container = document.getElementById(DOM_IDS.TAB_BAR)!;

    const onWheel = (e: WheelEvent) => this.onWheel(e);
    this.container.addEventListener('wheel', onWheel, { passive: false });
    this.disposables.add(toDisposable(() => this.container.removeEventListener('wheel', onWheel)));
  }

  public render(tabs: TabInfo[], activeUri: string | null): void {
    this.closeContextMenu();
    this.currentTabs = tabs;
    const activeChanged = activeUri !== this.lastActiveUri;
    this.lastActiveUri = activeUri;

    render(
      repeat(
        tabs,
        (tab) => tab.uri,
        (tab) => this.renderTab(tab, activeUri),
      ),
      this.container,
    );

    if (activeChanged && activeUri !== null) {
      const activeEl = this.container.querySelector<HTMLElement>('.tab.active');
      activeEl?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    }
  }

  public dispose(): void {
    this.closeContextMenu();
    render(nothing, this.container);
    this.disposables.dispose();
  }

  public [Symbol.dispose](): void {
    this.dispose();
  }

  private onWheel(e: WheelEvent): void {
    if (e.ctrlKey || e.deltaY === 0 || this.container.scrollWidth <= this.container.clientWidth) {
      return;
    }
    e.preventDefault();
    this.container.scrollLeft += e.deltaY;
  }

  private renderTab(tab: TabInfo, activeUri: string | null): TemplateResult {
    const classes = {
      tab: true,
      active: tab.uri === activeUri,
      main: tab.isMain,
      dirty: tab.isDirty,
    };

    return html`
      <div
        class=${classMap(classes)}
        @click=${() => this.callbacks.onSelectTab(tab.uri)}
        @mousedown=${(e: MouseEvent) => this.handleTabMouseDown(e)}
        @auxclick=${(e: MouseEvent) => this.handleTabAuxClick(e, tab.uri)}
        @contextmenu=${(e: MouseEvent) => this.handleTabContextMenu(e, tab)}
      >
        ${when(tab.isMain, () => this.renderMainBadge())}${this.renderTabTitle(
          tab,
        )}${this.renderTabIndicator(tab)}
      </div>
    `;
  }

  private renderMainBadge(): TemplateResult {
    return html`<span class="tab-main-badge">MAIN</span>`;
  }

  private renderTabTitle(tab: TabInfo): TemplateResult {
    return html`<span class="tab-title" title=${tab.uri}>${tab.title}</span>`;
  }

  private renderTabIndicator(tab: TabInfo): TemplateResult {
    return html`
      <span class="tab-indicator">
        ${when(tab.isDirty, () => html`<span class="tab-dirty-dot"></span>`)}
        <button
          type="button"
          class="tab-close"
          title="Close tab"
          aria-label="Close tab"
          @click=${(e: MouseEvent) => this.handleCloseClick(e, tab.uri)}
        >
          ${unsafeHTML(xIconSvg)}
        </button>
      </span>
    `;
  }

  private handleTabMouseDown(e: MouseEvent): void {
    if (e.button === 1) {
      e.preventDefault();
    }
  }

  private handleTabAuxClick(e: MouseEvent, uri: string): void {
    if (e.button === 1) {
      this.handleCloseClick(e, uri);
    }
  }

  private handleCloseClick(e: MouseEvent, uri: string): void {
    e.stopPropagation();
    this.callbacks.onCloseTab(uri);
  }

  private handleTabContextMenu(e: MouseEvent, tab: TabInfo): void {
    e.preventDefault();
    this.showContextMenu(e.clientX, e.clientY, tab);
  }

  private getContextMenuItems(tab: TabInfo): TabMenuItem[] {
    return [
      {
        id: 'set-main',
        kind: 'action',
        label: '⚡ Set as Main Script',
        disabled: tab.isMain,
        action: () => this.callbacks.onSetMainTab(tab.uri),
      },
      { id: 'sep-1', kind: 'separator' },
      {
        id: 'close',
        kind: 'action',
        label: 'Close',
        action: () => this.callbacks.onCloseTab(tab.uri),
      },
      {
        id: 'close-others',
        kind: 'action',
        label: 'Close Others',
        disabled: this.currentTabs.length <= 1,
        action: () => this.callbacks.onCloseOtherTabs(tab.uri),
      },
      {
        id: 'close-saved',
        kind: 'action',
        label: 'Close Saved',
        disabled: !this.currentTabs.some((t) => !t.isDirty),
        action: () => this.callbacks.onCloseSavedTabs(),
      },
    ];
  }

  private showContextMenu(x: number, y: number, tab: TabInfo): void {
    this.closeContextMenu();

    const fragment = document.createDocumentFragment();
    render(this.renderContextMenu(x, y, this.getContextMenuItems(tab)), fragment);
    document.body.appendChild(fragment);

    const menu = document.getElementById(DOM_IDS.TAB_CONTEXT_MENU);
    if (menu) {
      this.clampContextMenuPosition(menu, x, y);
    }

    this.bindContextMenuDismissListeners();
  }

  private renderContextMenu(x: number, y: number, items: TabMenuItem[]): TemplateResult {
    return html`
      <div
        id=${DOM_IDS.TAB_CONTEXT_MENU}
        class="tab-context-menu"
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

  private renderContextMenuEntry(item: TabMenuItem): TemplateResult {
    if (item.kind === 'separator') {
      return html`<div class="tab-context-menu-separator"></div>`;
    }
    return this.renderContextMenuAction(item);
  }

  private renderContextMenuAction(item: TabMenuActionItem): TemplateResult {
    const classes = {
      'tab-context-menu-item': true,
      disabled: Boolean(item.disabled),
    };

    return html`
      <div
        class=${classMap(classes)}
        @click=${(e: MouseEvent) => this.handleContextMenuItemClick(e, item)}
      >
        ${item.label}
      </div>
    `;
  }

  private handleContextMenuItemClick(e: MouseEvent, item: TabMenuActionItem): void {
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
    document.getElementById(DOM_IDS.TAB_CONTEXT_MENU)?.remove();
    if (this.contextMenuDisposables) {
      this.contextMenuDisposables.dispose();
      this.contextMenuDisposables = null;
    }
  }
}
