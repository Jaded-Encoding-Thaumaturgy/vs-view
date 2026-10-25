import { defineConfig } from 'vite-plus';

export default defineConfig({
  lint: {
    plugins: ['typescript'],
    categories: {
      correctness: 'error',
    },
    rules: {
      'prefer-const': 'error',
      'no-var': 'error',
      'prefer-rest-params': 'error',
      'prefer-spread': 'error',
      'sort-imports': [
        'error',
        {
          ignoreDeclarationSort: true,
          ignoreMemberSort: false,
        },
      ],
      'vite-plus/prefer-vite-plus-imports': 'error',
    },
    options: {
      typeAware: true,
      typeCheck: true,
    },
    ignorePatterns: [
      'submodules/**',
      'docs/**',
      'src/workspaces/editor/src/python/vsview_editor/web_dist/**',
    ],
    overrides: [
      {
        files: ['src/workspaces/editor/src/ts/**'],
        env: {
          browser: true,
          builtin: true,
        },
      },
      {
        files: ['**/*.test.ts'],
        rules: {
          'typescript/unbound-method': 'off',
          'typescript/restrict-template-expressions': 'off',
        },
      },
    ],
    jsPlugins: [
      {
        name: 'vite-plus',
        specifier: 'vite-plus/oxlint-plugin',
      },
    ],
  },
  fmt: {
    printWidth: 100,
    tabWidth: 2,
    singleQuote: true,
    ignorePatterns: [
      'submodules/*',
      'docs/**/*.md',
      '*.qss',
      '**/*.toml',
      'src/vsview/assets/',
      'src/workspaces/editor/src/python/vsview_editor/web_dist/',
    ],
    sortImports: true,
    proseWrap: 'preserve',
    overrides: [
      {
        files: ['*.md'],
        options: {
          tabWidth: 4,
          printWidth: 120,
        },
      },
    ],
  },
  test: {
    projects: ['src/workspaces/editor'],
    silent: true,
  },
});
