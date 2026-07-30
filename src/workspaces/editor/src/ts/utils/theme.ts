import githubDarkColorblind from "../assets/themes/github/dark-colorblind.json?url";
import githubDarkDefault from "../assets/themes/github/dark-default.json?url";
import githubDarkDimmed from "../assets/themes/github/dark-dimmed.json?url";
import githubDarkHighContrast from "../assets/themes/github/dark-high-contrast.json?url";
import githubDark from "../assets/themes/github/dark.json?url";
import githubLightColorblind from "../assets/themes/github/light-colorblind.json?url";
import githubLightDefault from "../assets/themes/github/light-default.json?url";
import githubLightHighContrast from "../assets/themes/github/light-high-contrast.json?url";
import githubLight from "../assets/themes/github/light.json?url";
import { Result } from "./result";

type Kind = "light" | "dark" | "hc-black" | "hc-light";
type UiTheme = "vs-dark" | "vs" | "hc-black" | "hc-light";

class ThemeKind {
  constructor(public readonly value: Kind) {}

  get uiTheme(): UiTheme {
    switch (this.value) {
      case "dark":
        return "vs-dark";
      case "light":
        return "vs";
      default:
        return this.value;
    }
  }

  toString(): string {
    return this.value;
  }
}

const KIND = {
  DARK: new ThemeKind("dark"),
  LIGHT: new ThemeKind("light"),
  HC_BLACK: new ThemeKind("hc-black"),
  HC_LIGHT: new ThemeKind("hc-light"),
} as const;

export interface ThemeDefinition {
  id: string;
  kind: ThemeKind;
}

export interface GithubThemeConfig extends ThemeDefinition {
  path: string;
  url: string;
}

export const GITHUB_THEMES: readonly GithubThemeConfig[] = [
  {
    id: "GitHub Dark Default",
    kind: KIND.DARK,
    path: "./themes/github-dark-default.json",
    url: githubDarkDefault,
  },
  {
    id: "GitHub Dark",
    kind: KIND.DARK,
    path: "./themes/github-dark.json",
    url: githubDark,
  },
  {
    id: "GitHub Dark Colorblind",
    kind: KIND.DARK,
    path: "./themes/github-dark-colorblind.json",
    url: githubDarkColorblind,
  },
  {
    id: "GitHub Dark Dimmed",
    kind: KIND.DARK,
    path: "./themes/github-dark-dimmed.json",
    url: githubDarkDimmed,
  },
  {
    id: "GitHub Dark High Contrast",
    kind: KIND.HC_BLACK,
    path: "./themes/github-dark-high-contrast.json",
    url: githubDarkHighContrast,
  },
  {
    id: "GitHub Light Default",
    kind: KIND.LIGHT,
    path: "./themes/github-light-default.json",
    url: githubLightDefault,
  },
  {
    id: "GitHub Light",
    kind: KIND.LIGHT,
    path: "./themes/github-light.json",
    url: githubLight,
  },
  {
    id: "GitHub Light Colorblind",
    kind: KIND.LIGHT,
    path: "./themes/github-light-colorblind.json",
    url: githubLightColorblind,
  },
  {
    id: "GitHub Light High Contrast",
    kind: KIND.HC_LIGHT,
    path: "./themes/github-light-high-contrast.json",
    url: githubLightHighContrast,
  },
] as const;

export const DEFAULT_THEMES: Record<string, ThemeDefinition> = {
  "Dark Modern": { id: "Dark Modern", kind: KIND.DARK },
  "Light Modern": { id: "Light Modern", kind: KIND.LIGHT },
  "Dark+": { id: "Dark+", kind: KIND.DARK },
  "Light+": { id: "Light+", kind: KIND.LIGHT },
  "Dark 2026": { id: "Dark 2026", kind: KIND.DARK },
  "Light 2026": { id: "Light 2026", kind: KIND.LIGHT },
  "Default High Contrast": { id: "Default High Contrast", kind: KIND.HC_BLACK },
  "Default High Contrast Light": { id: "Default High Contrast Light", kind: KIND.HC_LIGHT },
  "Visual Studio Dark": { id: "Visual Studio Dark", kind: KIND.DARK },
  "Visual Studio Light": { id: "Visual Studio Light", kind: KIND.LIGHT },
};

export const SUPPORTED_THEMES: Record<string, ThemeDefinition> = {
  ...DEFAULT_THEMES,
  ...Object.fromEntries(
    GITHUB_THEMES.map((theme) => [theme.id, { id: theme.id, kind: theme.kind }]),
  ),
};

/**
 * Gets the canonical theme definition from a theme name.
 */
export function getThemeDefinition(themeName: string): Result<ThemeDefinition, Error> {
  const matched = SUPPORTED_THEMES[themeName];
  if (matched) {
    return Result.ok(matched);
  }
  return Result.err(new Error(`Unsupported theme: '${themeName}'`));
}
