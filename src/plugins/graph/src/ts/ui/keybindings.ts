import { type IDisposable, Result } from 'vsview-shared';

import type { KeybindingRule } from '../types';

export interface KeybindingActions {
  navigateUp: (cycle: boolean) => void;
  navigateDown: (cycle: boolean) => void;
  selectRoot: () => void;
  selectDeepestLeaf: () => void;
  centerSelected: () => void;
  escape: () => void;
  resetView: () => void;
  fit: () => void;
  toggleHeatmap: () => void;
  togglePatterns: () => void;
  toggleOverview: () => void;
  refresh: () => void;
  focusSearch: () => void;
}

export class KeybindingController implements IDisposable {
  private rules: KeybindingRule[] = [];

  constructor(private readonly actions: KeybindingActions) {
    window.addEventListener('keydown', this.boundKeyDown);
  }

  public get keybindings(): KeybindingRule[] {
    return [...this.rules];
  }

  public set keybindings(rules: readonly KeybindingRule[]) {
    this.rules = [...rules];
  }

  public getShortcut(actionName: string): string | null {
    const rule = this.rules.find((r) => r.action === actionName);
    return rule ? formatShortcut(rule) : null;
  }

  public executeAction(actionName: string, cycle = false): Result<void> {
    const action = this.actions[actionName as keyof KeybindingActions];
    if (typeof action !== 'function') {
      const err = new Error(`Unknown keybinding action: '${actionName}'`);
      console.warn(err.message);
      return Result.err(err);
    }

    return Result.fromThrowable(() => {
      if (actionName === 'navigateUp' || actionName === 'navigateDown') {
        action(cycle);
      } else {
        (action as () => void)();
      }
    }).mapErr((err) => {
      console.error(`Error executing keybinding action '${actionName}':`, err);
      return err;
    });
  }

  public dispose(): void {
    window.removeEventListener('keydown', this.boundKeyDown);
  }

  public [Symbol.dispose](): void {
    this.dispose();
  }

  private handleKeyDown(event: KeyboardEvent): void {
    if (event.defaultPrevented || event.isComposing) return;

    const target = event.target;
    if (
      target instanceof HTMLElement &&
      (target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName))
    ) {
      return;
    }

    if (event.key === 'Escape' && !event.ctrlKey && !event.altKey && !event.metaKey) {
      event.preventDefault();
      this.actions.escape();
      return;
    }

    const onActionable =
      event.target instanceof HTMLElement && ['BUTTON', 'A'].includes(event.target.tagName);
    if (onActionable && (event.key === 'Enter' || event.key === ' ')) {
      return;
    }

    for (const rule of this.rules) {
      if (matchesKey(event, rule)) {
        event.preventDefault();
        const cycle =
          (rule.action === 'navigateUp' || rule.action === 'navigateDown') && event.shiftKey;
        this.executeAction(rule.action, cycle);
        return;
      }
    }
  }

  private boundKeyDown = (event: KeyboardEvent) => this.handleKeyDown(event);
}

const KEY_DISPLAY_OVERRIDES: Record<string, string> = {
  ' ': 'Space',
  Escape: 'Esc',
  ArrowUp: '↑',
  ArrowDown: '↓',
  ArrowLeft: '←',
  ArrowRight: '→',
};

/** Format a KeybindingRule into a human-readable display string (e.g. 'Ctrl+R', 'F', '0'). */
export function formatShortcut(rule: KeybindingRule): string {
  const parts: string[] = [];
  if (rule.ctrl) parts.push('Ctrl');
  if (rule.alt) parts.push('Alt');
  if (rule.shift) parts.push('Shift');
  if (rule.meta) parts.push('Meta');

  const override = KEY_DISPLAY_OVERRIDES[rule.key];
  if (override) {
    parts.push(override);
  } else if (rule.key.length === 1) {
    parts.push(rule.key.toUpperCase());
  } else {
    parts.push(rule.key);
  }

  return parts.join('+');
}

/** Test whether a KeyboardEvent matches a preconfigured keybinding rule. */
export function matchesKey(event: KeyboardEvent, rule: KeybindingRule): boolean {
  const eventCtrl = event.ctrlKey || event.metaKey;
  if (rule.ctrl !== eventCtrl) return false;
  if (rule.alt !== event.altKey) return false;

  // For navigateUp and navigateDown without explicit Shift in the shortcut,
  // allow event.shiftKey so Shift+Arrow can pass cycle = true.
  const isNavAction = rule.action === 'navigateUp' || rule.action === 'navigateDown';
  if (rule.shift) {
    if (!event.shiftKey) return false;
  } else if (!isNavAction && event.shiftKey) {
    return false;
  }

  const ruleKey = rule.key.toLowerCase();
  switch (ruleKey) {
    case 'arrowup':
      return event.key === 'ArrowUp' || event.key === 'ArrowLeft';
    case 'arrowdown':
      return event.key === 'ArrowDown' || event.key === 'ArrowRight';
    default:
      return ruleKey === event.key.toLowerCase();
  }
}
