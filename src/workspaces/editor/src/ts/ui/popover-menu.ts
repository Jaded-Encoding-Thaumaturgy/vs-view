import './popover-menu.css';
import { type TemplateResult, html, render } from 'lit-html';
import { ifDefined } from 'lit-html/directives/if-defined.js';
import { repeat } from 'lit-html/directives/repeat.js';
import { styleMap } from 'lit-html/directives/style-map.js';
import { when } from 'lit-html/directives/when.js';
import { type IDisposable, Result } from 'vsview-shared';

export interface MenuActionItem {
  id: string;
  kind: 'action';
  label: string;
  shortcut?: string;
  action: () => void;
  disabled?: boolean;
}

export interface MenuSeparatorItem {
  id: string;
  kind: 'separator';
}

export type MenuItem = MenuActionItem | MenuSeparatorItem;

interface PopoverMenuOptions {
  /** Element id of the open menu. */
  id: string;
  /** Accessible name of the menu. */
  label: string;
}

/** A context menu opened at a pointer position. */
export class PopoverMenu implements IDisposable {
  private host: HTMLElement | null = null;

  constructor(private readonly opts: PopoverMenuOptions) {}

  public get isOpen(): boolean {
    return this.host !== null;
  }

  public show(x: number, y: number, items: MenuItem[]): void {
    this.close();

    const host = document.createElement('div');
    host.className = 'context-menu-host';
    render(this.renderMenu(x, y, items), host);
    document.body.appendChild(host);
    this.host = host;

    const anchor = host.querySelector<HTMLElement>('.context-menu-anchor');
    const menu = host.querySelector<HTMLElement>('.context-menu');
    if (!anchor || !menu) {
      this.remove();
      return;
    }

    void Result.fromThrowable(() => menu.showPopover({ source: anchor })).mapErr((e) => {
      console.warn('Failed to open context menu:', e);
      this.remove();
    });
  }

  public close(): void {
    const menu = this.host?.querySelector<HTMLElement>('.context-menu');
    if (menu) {
      void Result.fromThrowable(() => menu.hidePopover());
    }
    this.remove();
  }

  public dispose(): void {
    this.close();
  }

  private remove(): void {
    this.host?.remove();
    this.host = null;
  }

  private activate(item: MenuActionItem): void {
    if (item.disabled) return;

    this.close();
    item.action();
  }

  private handleToggle(e: Event): void {
    const isCurrent = e.currentTarget instanceof Node && this.host?.contains(e.currentTarget);
    if (isCurrent && 'newState' in e && e.newState === 'closed') {
      this.remove();
    }
  }

  private renderMenu(x: number, y: number, items: MenuItem[]): TemplateResult {
    return html`
      <span
        class="context-menu-anchor"
        aria-hidden="true"
        style=${styleMap({ left: `${x}px`, top: `${y}px` })}
      ></span>
      <div
        id=${this.opts.id}
        class="context-menu"
        popover="auto"
        role="menu"
        aria-label=${this.opts.label}
        @toggle=${(e: Event) => this.handleToggle(e)}
      >
        ${repeat(
          items,
          (item) => item.id,
          (item) => this.renderItem(item),
        )}
      </div>
    `;
  }

  private renderItem(item: MenuItem): TemplateResult {
    if (item.kind === 'separator') {
      return html`<hr class="context-menu-separator" />`;
    }

    return html`
      <button
        type="button"
        class="context-menu-item"
        role="menuitem"
        aria-disabled=${ifDefined(item.disabled ? 'true' : undefined)}
        @click=${() => this.activate(item)}
      >
        <span class="context-menu-label">${item.label}</span>
        ${when(item.shortcut, () => html`<span class="context-menu-shortcut">${item.shortcut}</span>`)}
      </button>
    `;
  }
}
