import './tabbar.css';
import { type TemplateResult, html, nothing, render } from 'lit-html';
import { classMap } from 'lit-html/directives/class-map.js';
import { repeat } from 'lit-html/directives/repeat.js';
import { unsafeHTML } from 'lit-html/directives/unsafe-html.js';
import { when } from 'lit-html/directives/when.js';
import type * as vscode from 'vscode';
import { DisposableStore, toDisposable } from 'vsview-shared';

import xIconSvg from '../assets/icons/lucide/x.svg?raw';
import type { TabInfo } from '../types';
import { DOM_IDS } from './constants';
import { type MenuItem, PopoverMenu } from './popover-menu';

export type TabBarCallbacks = {
  onSelectTab: (uri: string) => void;
  onCloseTab: (uri: string) => void;
  onCloseOtherTabs: (uri: string) => void;
  onCloseSavedTabs: () => void;
  onSetMainTab: (uri: string) => void;
};

export class TabBarView implements vscode.Disposable {
  private readonly disposables = new DisposableStore();
  private readonly container: HTMLElement;
  private readonly contextMenu = new PopoverMenu({
    id: DOM_IDS.TAB_CONTEXT_MENU,
    label: 'Tab actions',
  });
  private readonly tabIds = new Map<string, string>();
  private nextTabId = 0;
  private currentTabs: TabInfo[] = [];
  private lastActiveUri: string | null = null;

  constructor(private readonly callbacks: TabBarCallbacks) {
    this.container = document.getElementById(DOM_IDS.TAB_BAR)!;
    this.container.setAttribute('role', 'tablist');
    this.container.setAttribute('aria-label', 'Open scripts');

    const onWheel = (e: WheelEvent) => this.onWheel(e);
    this.container.addEventListener('wheel', onWheel, { passive: false });
    this.disposables.add(toDisposable(() => this.container.removeEventListener('wheel', onWheel)));
  }

  public render(tabs: TabInfo[], activeUri: string | null): void {
    this.contextMenu.close();
    this.currentTabs = tabs;
    for (const uri of this.tabIds.keys()) {
      if (!tabs.some((tab) => tab.uri === uri)) this.tabIds.delete(uri);
    }
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
    this.syncTabPanel(activeUri);

    if (activeChanged && activeUri !== null) {
      const activeEl = this.container.querySelector<HTMLElement>('.tab.active');
      activeEl?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    }
  }

  public dispose(): void {
    this.contextMenu.dispose();
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

  private tabId(uri: string): string {
    let id = this.tabIds.get(uri);
    if (id === undefined) {
      id = `${DOM_IDS.TAB_BAR}-tab-${this.nextTabId++}`;
      this.tabIds.set(uri, id);
    }
    return id;
  }

  private syncTabPanel(activeUri: string | null): void {
    const panel = document.getElementById(DOM_IDS.EDITOR);
    if (!panel) return;

    panel.setAttribute('role', 'tabpanel');
    if (activeUri === null) {
      panel.removeAttribute('aria-labelledby');
    } else {
      panel.setAttribute('aria-labelledby', this.tabId(activeUri));
    }
  }

  private tabLabel(tab: TabInfo): string {
    const notes = [tab.isMain ? 'main script' : null, tab.isDirty ? 'unsaved changes' : null];
    return [tab.title, ...notes.filter((note) => note !== null)].join(', ');
  }

  private renderTab(tab: TabInfo, activeUri: string | null): TemplateResult {
    const isActive = tab.uri === activeUri;
    const classes = {
      tab: true,
      active: isActive,
      main: tab.isMain,
      dirty: tab.isDirty,
    };

    return html`
      <div
        class=${classMap(classes)}
        id=${this.tabId(tab.uri)}
        role="tab"
        aria-selected=${isActive}
        aria-label=${this.tabLabel(tab)}
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
    return html`<span class="tab-main-badge" aria-hidden="true">MAIN</span>`;
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
    this.contextMenu.show(e.clientX, e.clientY, this.getContextMenuItems(tab));
  }

  private getContextMenuItems(tab: TabInfo): MenuItem[] {
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
}
