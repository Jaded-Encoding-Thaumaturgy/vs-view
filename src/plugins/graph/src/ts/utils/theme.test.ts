import { describe, expect, it, vi } from 'vite-plus/test';

import { ThemeManager, type ThemeTokens } from './theme';

const TOKENS: ThemeTokens = {
  isDark: true,
  bg: '#101010',
  surface: '#202020',
  surfaceElevated: '#303030',
  surfaceHover: '#404040',
  border: '#505050',
  textPrimary: '#f0f0f0',
  textSecondary: '#c0c0c0',
  textMuted: '#808080',
  accent: '#00aaff',
  accentAmber: '#ffaa00',
  accentEmerald: '#00ff88',
  danger: '#ff0000',
};

describe('ThemeManager', () => {
  it('applies theme tokens to CSS custom properties and manages subscriber lifecycle', () => {
    const manager = new ThemeManager();
    expect(manager.tokens).toBeNull();

    const listener = vi.fn();
    const subscription = manager.onThemeChanged(listener);

    manager.updateTheme(TOKENS);

    expect(manager.tokens).toEqual(TOKENS);
    expect(document.documentElement.style.getPropertyValue('--bg-color')).toBe(TOKENS.bg);
    expect(document.documentElement.style.getPropertyValue('--danger')).toBe(TOKENS.danger);
    expect(listener).toHaveBeenCalledTimes(1);
    expect(listener).toHaveBeenCalledWith(TOKENS);

    subscription.dispose();
    manager.updateTheme({ ...TOKENS, bg: '#ffffff' });
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it('safely handles throwing listeners without interrupting others', () => {
    const manager = new ThemeManager();
    const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    const throwingListener = vi.fn().mockImplementation(() => {
      throw new Error('Listener boom');
    });
    const succeedingListener = vi.fn();

    manager.onThemeChanged(throwingListener);
    manager.onThemeChanged(succeedingListener);

    manager.updateTheme(TOKENS);

    expect(throwingListener).toHaveBeenCalledWith(TOKENS);
    expect(succeedingListener).toHaveBeenCalledWith(TOKENS);
    expect(consoleSpy).toHaveBeenCalledWith('Error in theme change listener:', expect.any(Error));

    consoleSpy.mockRestore();
  });
});
