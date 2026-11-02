import { defineConfig } from 'vite-plus';

export default defineConfig({
  lint: {
    categories: {
      correctness: 'error',
      perf: 'error',
      suspicious: 'error',
    },
    ignorePatterns: [
      'submodules/**',
      'docs/**',
      'src/workspaces/editor/src/python/vsview_editor/web_dist/**',
      'src/plugins/graph/src/python/vsview_graph/web_dist/**',
    ],
    jsPlugins: [
      {
        name: 'vite-plus',
        specifier: 'vite-plus/oxlint-plugin',
      },
    ],
    options: {
      typeAware: true,
      typeCheck: true,
    },
    overrides: [
      {
        env: {
          browser: true,
          builtin: true,
        },
        files: [
          'src/workspaces/editor/src/ts/**',
          'src/plugins/graph/src/ts/**',
          'src/shared/ts/src/**',
        ],
      },
      {
        files: ['**/*.test.ts'],
        rules: {
          'typescript/restrict-template-expressions': 'off',
          'typescript/unbound-method': 'off',
          'vitest/require-mock-type-parameters': 'off',
        },
      },
    ],
    plugins: ['eslint', 'import', 'typescript', 'oxc', 'node', 'promise', 'vitest'],
    rules: {
      'eslint/no-new': 'off',
      'eslint/no-underscore-dangle': 'off',
      'eslint/no-shadow': 'off',
      'import/consistent-type-specifier-style': ['warn', 'prefer-top-level-if-only-type-imports'],
      'import/default': 'off',
      'no-empty': ['error', { allowEmptyCatch: false }],
      'no-unsafe-optional-chaining': 'error',
      'no-var': 'error',
      'prefer-const': 'error',
      'prefer-rest-params': 'error',
      'prefer-spread': 'error',
      'sort-imports': [
        'error',
        {
          ignoreDeclarationSort: true,
          ignoreMemberSort: false,
        },
      ],
      'typescript/consistent-type-exports': 'warn',
      'typescript/consistent-type-imports': 'warn',
      'typescript/no-extraneous-class': 'off',
      'typescript/no-unnecessary-boolean-literal-compare': 'warn',
      'typescript/no-unnecessary-condition': 'warn',
      'typescript/no-unnecessary-type-assertion': 'warn',
      'typescript/no-unsafe-type-assertion': 'off',
      'typescript/no-useless-default-assignment': 'error',
      'typescript/prefer-nullish-coalescing': 'error',
      'vite-plus/prefer-vite-plus-imports': 'error',
    },
  },
  fmt: {
    ignorePatterns: [
      'submodules/*',
      'docs/**/*.md',
      '*.qss',
      '**/*.toml',
      'src/vsview/assets/',
      'src/workspaces/**/web_dist/',
    ],
    overrides: [
      {
        files: ['*.md'],
        options: {
          printWidth: 120,
          tabWidth: 4,
        },
      },
    ],
    printWidth: 100,
    proseWrap: 'preserve',
    singleQuote: true,
    sortImports: true,
    tabWidth: 2,
  },
  test: {
    projects: ['src/workspaces/editor', 'src/plugins/graph', 'src/shared/ts'],
    silent: true,
  },
});
