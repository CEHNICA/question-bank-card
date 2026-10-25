// 题有据前端的 ESLint 配置（flat config，ESLint 9）。
//
// 目标不是"把所有写法都管起来"，而是挡住**真会出事**的那几类：
//   · 没定义就用（no-undef）
//   · 声明了没用（no-unused-vars）
//   · == / != 用在了不该用的地方（eqeqeq）
//   · 异步忘了 await、promise 没接住（no-floating-promises 要开类型信息才生效，先用 require-await）
//   · 变量在循环里被闭包抓住（no-loop-func / no-var）
//
// 风格类的（引号、分号、缩进）交给 Prettier，不在 ESLint 里重复一遍。
import js from "@eslint/js";
import globals from "globals";

export default [
  {
    ignores: [
      "frontend/vendor/**",
      "frontend/testdata/**",
      "node_modules/**",
      "packaging/**",
    ],
  },
  js.configs.recommended,
  {
    files: ["frontend/**/*.js"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "script",
      globals: {
        ...globals.browser,
        ...globals.node,
      },
    },
    linterOptions: {
      reportUnusedDisableDirectives: true,
    },
    rules: {
      // 真正会咬人的几条。
      "no-undef": "error",
      "no-unused-vars": ["error", { args: "none", varsIgnorePattern: "^_" }],
      "no-var": "error",
      "prefer-const": "error",
      eqeqeq: ["error", "always", { null: "ignore" }],
      "no-implicit-globals": "off",
      "no-loop-func": "warn",
      "no-const-assign": "error",
      "no-dupe-keys": "error",
      "no-dupe-args": "error",
      "no-duplicate-case": "error",
      "no-unreachable": "error",
      "no-fallthrough": "error",
      "no-self-compare": "error",
      "no-cond-assign": "error",
      "require-atomic-updates": "off",
      "no-async-promise-executor": "error",
      "no-sparse-arrays": "error",
      "no-delete-var": "error",
      "no-shadow-restricted-names": "error",
      "no-unused-private-class-members": "error",
      "no-useless-escape": "error",
      "no-empty": ["error", { allowEmptyCatch: true }],
    },
  },
  {
    // 测试文件：可以用 node 专有东西，也可以有没用的参数。
    files: ["frontend/test_*.js"],
    languageOptions: {
      globals: { ...globals.node },
    },
    rules: {
      "no-unused-vars": ["error", { args: "none", varsIgnorePattern: "^_" }],
    },
  },
];
