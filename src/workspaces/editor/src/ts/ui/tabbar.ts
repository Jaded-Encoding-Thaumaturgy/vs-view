import { type TemplateResult, html, nothing, render } from 'lit-html';
import { classMap } from 'lit-html/directives/class-map.js';
import { repeat } from 'lit-html/directives/repeat.js';
import { styleMap } from 'lit-html/directives/style-map.js';
import { when } from 'lit-html/directives/when.js';
import type * as vscode from 'vscode';
import { DisposableStore, toDisposable } from 'vsview-shared';

import type { TabInfo } from '../types';
import { DOM_IDS } from './constants';

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

    render(
      repeat(
        tabs,
        (tab) => tab.uri,
        (tab) => this.renderTab(tab, activeUri),
      ),
      this.container,
    );
  }

  public dispose(): void {
    this.closeContextMenu();
    render(nothing, this.container);
    this.disposables.dispose();
  }

  public [Symbol.dispose](): void {
    this.dispose();
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
        <span
          class="tab-close"
          title="Close tab"
          @click=${(e: MouseEvent) => this.handleCloseClick(e, tab.uri)}
          >${'\u00D7'}</span
        >
      </span>
    `;
  }

  private handleCloseClick(e: MouseEvent, uri: string): void {
    e.stopPropagation();
    this.callbacks.onCloseTab(uri);
  }

  private handleTabContextMenu(e: MouseEvent, tab: TabInfo): void {
    e.preventDefault();
    if (!tab.isMain) {
      this.showContextMenu(e.clientX, e.clientY, tab.uri);
    }
  }

  private handleSetMainClick(uri: string): void {
    this.callbacks.onSetMainTab(uri);
    this.closeContextMenu();
  }

  private showContextMenu(x: number, y: number, uri: string): void {
    this.closeContextMenu();

    const fragment = document.createDocumentFragment();
    render(this.renderContextMenu(x, y, uri), fragment);
    document.body.appendChild(fragment);

    this.bindContextMenuDismissListeners();
  }

  private renderContextMenu(x: number, y: number, uri: string): TemplateResult {
    return html`
      <div
        id=${DOM_IDS.TAB_CONTEXT_MENU}
        class="tab-context-menu"
        style=${styleMap({ left: `${x}px`, top: `${y}px` })}
      >
        ${this.renderContextMenuItem(uri)}
      </div>
    `;
  }

  private renderContextMenuItem(uri: string): TemplateResult {
    const onClick = () => this.handleSetMainClick(uri);
    return html`<div class="tab-context-menu-item" @click=${onClick}>⚡ Set as Main Script</div>`;
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
