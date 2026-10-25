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
      // catch 里的错误参数有时就是不用（错误在别处已经记过了），不查。
      "no-unused-vars": ["error", { args: "none", varsIgnorePattern: "^_", caughtErrors: "none" }],
      "no-var": "error",
      "prefer-const": "error",
      eqeqeq: ["error", "always", { null: "ignore" }],
      "no-implicit-globals": "off",
      // JS 的 for(let …) 本来就是每轮一份绑定，这条规则在 JS 里几乎全是误报
      //（它是从 Python 的闭包坑照搬来的）。真正会出事的是 var 循环变量，
      // 那个由上面的 no-var 挡着。
      "no-loop-func": "off",
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
      // 中文排版里全角空格（　）是故意用的，不算「不规则空白」。
      // 正则里那几条也全是故意要匹配全角/零宽字符的。
      "no-irregular-whitespace": ["error", { skipStrings: true, skipTemplates: true, skipComments: true, skipRegExps: true }],
      "no-empty": ["error", { allowEmptyCatch: true }],
    },
  },
  {
    // 测试文件：可以用 node 专有东西，也可以有没用的参数。
    // 测试里的假 DOM 故意叫 Element / document / Node —— 那是在沙箱里替身，
    // 不是「不小心把浏览器全局重定义了一遍」，所以 builtinGlobals 关掉。
    files: ["frontend/test_*.js"],
    languageOptions: {
      globals: { ...globals.node },
    },
    rules: {
      "no-unused-vars": ["error", { args: "none", varsIgnorePattern: "^_", caughtErrors: "none" }],
      "no-redeclare": ["error", { builtinGlobals: false }],
    },
  },
];
