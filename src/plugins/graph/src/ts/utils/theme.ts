import { Disposable, Result } from 'vsview-shared';

export interface ThemeTokens {
  isDark: boolean;
  bg: string;
  surface: string;
  surfaceElevated: string;
  surfaceHover: string;
  border: string;
  textPrimary: string;
  textSecondary: string;
  textMuted: string;
  accent: string;
  accentAmber: string;
  accentEmerald: string;
  danger: string;
}

export class ThemeManager extends Disposable {
  private currentTokens: ThemeTokens | null = null;
  private readonly listeners: Array<(tokens: ThemeTokens) => void> = [];

  public get tokens(): ThemeTokens | null {
    return this.currentTokens;
  }

  public onThemeChanged(cb: (tokens: ThemeTokens) => void): () => void {
    this.listeners.push(cb);
    return () => {
      const idx = this.listeners.indexOf(cb);
      if (idx !== -1) {
        this.listeners.splice(idx, 1);
      }
    };
  }

  public updateTheme(tokens: ThemeTokens): void {
    this.currentTokens = tokens;
    this.applyToCss(tokens);

    for (const listener of this.listeners) {
      void Result.fromThrowable(() => listener(tokens)).mapErr((err) => {
        console.error('Error in theme change listener:', err);
      });
    }
  }

  private applyToCss(tokens: ThemeTokens): void {
    const root = document.documentElement;
    root.style.setProperty('--bg-color', tokens.bg);
    root.style.setProperty('--surface-color', tokens.surface);
    root.style.setProperty('--surface-elevated', tokens.surfaceElevated);
    root.style.setProperty('--surface-hover', tokens.surfaceHover);
    root.style.setProperty('--border-color', tokens.border);
    root.style.setProperty('--text-primary', tokens.textPrimary);
    root.style.setProperty('--text-secondary', tokens.textSecondary);
    root.style.setProperty('--text-muted', tokens.textMuted);
    root.style.setProperty('--accent', tokens.accent);
    root.style.setProperty('--accent-amber', tokens.accentAmber);
    root.style.setProperty('--accent-emerald', tokens.accentEmerald);
    root.style.setProperty('--danger', tokens.danger);
  }
}
