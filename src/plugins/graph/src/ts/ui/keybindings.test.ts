import { describe, expect, it, vi } from 'vite-plus/test';

import type { KeybindingRule } from '../types';
import {
  type KeybindingActions,
  KeybindingController,
  formatShortcut,
  matchesKey,
} from './keybindings';

function createMockActions(): KeybindingActions {
  return {
    navigateUp: vi.fn(),
    navigateDown: vi.fn(),
    selectRoot: vi.fn(),
    selectDeepestLeaf: vi.fn(),
    centerSelected: vi.fn(),
    escape: vi.fn(),
    resetView: vi.fn(),
    fit: vi.fn(),
    toggleHeatmap: vi.fn(),
    togglePatterns: vi.fn(),
    toggleOverview: vi.fn(),
    reload: vi.fn(),
    focusSearch: vi.fn(),
  };
}

function makeRule(
  overrides: Partial<KeybindingRule> & { action: string; key: string },
): KeybindingRule {
  return {
    ctrl: false,
    shift: false,
    alt: false,
    meta: false,
    ...overrides,
  };
}

describe('matchesKey', () => {
  it('matches case-insensitively and enforces ctrl, alt, shift, and meta modifiers', () => {
    const plainRule = makeRule({ action: 'fit', key: 'F' });
    const ctrlShiftRule = makeRule({ action: 'resetView', key: 'Z', ctrl: true, shift: true });
    const metaRule = makeRule({ action: 'reload', key: 'R', meta: true });

    expect(matchesKey(new KeyboardEvent('keydown', { key: 'f' }), plainRule)).toBe(true);
    expect(matchesKey(new KeyboardEvent('keydown', { key: 'F' }), plainRule)).toBe(true);
    expect(matchesKey(new KeyboardEvent('keydown', { key: 'f', ctrlKey: true }), plainRule)).toBe(
      false,
    );
    expect(matchesKey(new KeyboardEvent('keydown', { key: 'f', altKey: true }), plainRule)).toBe(
      false,
    );
    expect(matchesKey(new KeyboardEvent('keydown', { key: 'f', shiftKey: true }), plainRule)).toBe(
      false,
    );
    expect(matchesKey(new KeyboardEvent('keydown', { key: 'f', metaKey: true }), plainRule)).toBe(
      false,
    );

    expect(
      matchesKey(
        new KeyboardEvent('keydown', { key: 'z', ctrlKey: true, shiftKey: true }),
        ctrlShiftRule,
      ),
    ).toBe(true);
    expect(
      matchesKey(
        new KeyboardEvent('keydown', { key: 'z', metaKey: true, shiftKey: true }),
        ctrlShiftRule,
      ),
    ).toBe(false);
    expect(
      matchesKey(
        new KeyboardEvent('keydown', { key: 'z', ctrlKey: true, shiftKey: false }),
        ctrlShiftRule,
      ),
    ).toBe(false);

    expect(matchesKey(new KeyboardEvent('keydown', { key: 'r', metaKey: true }), metaRule)).toBe(
      true,
    );
    expect(matchesKey(new KeyboardEvent('keydown', { key: 'r', ctrlKey: true }), metaRule)).toBe(
      false,
    );
    expect(matchesKey(new KeyboardEvent('keydown', { key: 'r' }), metaRule)).toBe(false);
  });

  it('handles directional navigation aliases (ArrowLeft/ArrowRight) and permits Shift for cycling', () => {
    const navUpRule = makeRule({ action: 'navigateUp', key: 'ArrowUp' });
    const navDownRule = makeRule({ action: 'navigateDown', key: 'ArrowDown' });

    expect(matchesKey(new KeyboardEvent('keydown', { key: 'ArrowUp' }), navUpRule)).toBe(true);
    expect(matchesKey(new KeyboardEvent('keydown', { key: 'ArrowLeft' }), navUpRule)).toBe(true);
    expect(
      matchesKey(new KeyboardEvent('keydown', { key: 'ArrowLeft', shiftKey: true }), navUpRule),
    ).toBe(true);

    expect(matchesKey(new KeyboardEvent('keydown', { key: 'ArrowDown' }), navDownRule)).toBe(true);
    expect(matchesKey(new KeyboardEvent('keydown', { key: 'ArrowRight' }), navDownRule)).toBe(true);
    expect(
      matchesKey(new KeyboardEvent('keydown', { key: 'ArrowRight', shiftKey: true }), navDownRule),
    ).toBe(true);
  });
});

describe('formatShortcut', () => {
  it.each([
    [makeRule({ action: 'fit', key: 'f' }), 'F'],
    [makeRule({ action: 'resetView', key: '0' }), '0'],
    [makeRule({ action: 'reload', key: 'r', ctrl: true }), 'Ctrl+R'],
    [
      makeRule({ action: 'reload', key: 'r', ctrl: true, shift: true, alt: true, meta: true }),
      'Ctrl+Alt+Shift+Meta+R',
    ],
    [makeRule({ action: 'play', key: ' ' }), 'Space'],
    [makeRule({ action: 'escape', key: 'Escape' }), 'Esc'],
    [makeRule({ action: 'up', key: 'ArrowUp' }), '↑'],
    [makeRule({ action: 'down', key: 'ArrowDown' }), '↓'],
    [makeRule({ action: 'left', key: 'ArrowLeft' }), '←'],
    [makeRule({ action: 'right', key: 'ArrowRight' }), '→'],
    [makeRule({ action: 'reload', key: 'F5' }), 'F5'],
  ])('formats rule into %s', (rule, expected) => {
    expect(formatShortcut(rule)).toBe(expected);
  });
});

describe('KeybindingController', () => {
  it('dispatches matching keydown events, built-in Escape, and Shift-cycling', () => {
    const actions = createMockActions();
    using controller = new KeybindingController(actions);
    controller.keybindings = [
      makeRule({ action: 'fit', key: 'F' }),
      makeRule({ action: 'navigateUp', key: 'ArrowUp' }),
    ];

    expect(controller.keybindings).toHaveLength(2);
    expect(controller.getShortcut('fit')).toBe('F');
    expect(controller.getShortcut('nonExistent')).toBeNull();

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'f' }));
    expect(actions.fit).toHaveBeenCalledTimes(1);

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp', shiftKey: true }));
    expect(actions.navigateUp).toHaveBeenCalledWith(true);

    // Built-in Escape works without an explicit rule unless modified
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', ctrlKey: true }));
    expect(actions.escape).not.toHaveBeenCalled();

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    expect(actions.escape).toHaveBeenCalledTimes(1);
  });

  it('ignores composing/prevented events, editable elements, and Enter/Space on buttons', () => {
    const actions = createMockActions();
    using controller = new KeybindingController(actions);
    controller.keybindings = [
      makeRule({ action: 'fit', key: 'F' }),
      makeRule({ action: 'centerSelected', key: 'Enter' }),
    ];

    // Composing event is ignored
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'f', isComposing: true }));
    expect(actions.fit).not.toHaveBeenCalled();

    // Editable inputs are ignored completely
    const input = document.createElement('input');
    const button = document.createElement('button');
    document.body.append(input, button);

    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'f', bubbles: true }));
    expect(actions.fit).not.toHaveBeenCalled();

    // Enter on a button is ignored so native click activation isn't hijacked,
    // but non-activation shortcuts like 'f' still bubble and trigger
    button.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    expect(actions.centerSelected).not.toHaveBeenCalled();

    button.dispatchEvent(new KeyboardEvent('keydown', { key: 'f', bubbles: true }));
    expect(actions.fit).toHaveBeenCalledTimes(1);

    input.remove();
    button.remove();
  });

  it('executes actions directly, reports unknown actions, and catches throwing actions', () => {
    const actions = createMockActions();
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    using controller = new KeybindingController(actions);

    expect(controller.executeAction('navigateDown', true).ok).toBe(true);
    expect(actions.navigateDown).toHaveBeenCalledWith(true);

    const unknownResult = controller.executeAction('nonExistentAction');
    expect(unknownResult.ok).toBe(false);
    expect(warnSpy).toHaveBeenCalledWith("Unknown keybinding action: 'nonExistentAction'");

    vi.mocked(actions.fit).mockImplementationOnce(() => {
      throw new Error('Fit failed');
    });
    const failedResult = controller.executeAction('fit');
    expect(failedResult.ok).toBe(false);
    expect(errorSpy).toHaveBeenCalledWith(
      "Error executing keybinding action 'fit':",
      expect.any(Error),
    );

    warnSpy.mockRestore();
    errorSpy.mockRestore();
  });

  it('removes window listener when disposed', () => {
    const actions = createMockActions();
    const controller = new KeybindingController(actions);
    controller.keybindings = [makeRule({ action: 'fit', key: 'F' })];

    controller.dispose();

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'f' }));
    expect(actions.fit).not.toHaveBeenCalled();
  });
});
