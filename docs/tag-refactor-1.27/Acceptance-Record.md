# Acceptance-Record.md —— 题有据 1.27.0 候选验收记录

日期：2026-10-25（用户时区 America/Los_Angeles）
候选源码：`feature/tag-system-refactor`，提交 `59d0480`（基线同步 `b818989`）
候选产物：`work/candidate/QuestionBankCard-1.27.0-portable.zip`

**本记录只报告实际跑过的东西。没跑的写「未验证」，不写「应该没问题」。**

---

## 1. 运行环境

| 项目 | 值 |
|---|---|
| 操作系统 | Windows 10.0.26200 x64（win32） |
| Python | 3.12.8 |
| Django | 5.2.17 |
| 打包器 | PyInstaller（onedir），`packaging/build.ps1` |
| 开发实例 | 源码版 `127.0.0.1:8799`，独立 sqlite + 独立数据根 |
| 打包实例 | 候选版 `127.0.0.1:8798`，`QB_USER_ROOT` 指向独立目录 |
| 解压实例 | 候选版 `127.0.0.1:8797`，从 ZIP 解压后启动 |
| 生产实例 | `127.0.0.1:8768`（用户正在用的 1.26.31）—— **全程未触碰** |
| 数据 | **全部虚构**。生产库只以 `mode=ro` 打开读取统计，未写入 |

---

## 2. 门禁结果（`.\dev.ps1 check`）

改动前（原始基线）与改动后各跑一遍，逐步看退出码。

| 检查 | 基线（1.26.31 未改） | 候选（1.27.0） |
|---|---|---|
| ruff（代码规范） | 通过 | 通过 |
| ruff（格式） | 通过 | 通过 |
| mypy（类型检查） | 通过 | 通过（70 个源文件，0 错） |
| eslint（前端规范） | 通过 | 通过 |
| 后台测试 `manage.py test core` | 通过 | 通过 |
| 前端测试 `node --test frontend/test_*.js` | **139 通过 / 0 失败** | **140 通过 / 0 失败**（新增 1 个工作台测试文件） |
| 桌面外壳测试（8 个模块） | **147 项通过，OK** | **147 项通过，OK** |
| 总退出码 | **0** | **0** |

新增验收测试：`backend/core/test_library_tag_refactor.py`，**33 项，全部通过**。

```powershell
cd <worktree>\backend
..\.venv\Scripts\python.exe manage.py test core.test_library_tag_refactor
# Ran 33 tests — OK
```

---

## 3. 验收矩阵逐项结果

`✓` 实跑通过 · `部分` 部分通过 · `—` 本轮未做

### B —— 基线与隔离

| ID | 场景 | 要求 | 结果 | 证据 |
|---|---|---|---|---|
| B01 | 安装版与源码版本不同 | 记录差异并取得正确基线 | ✓ | 安装版 1.26.31；GitHub main 1.26.28；正确基线 `<源码仓库>` main @ `ccdb8f0`（1.26.31，领先 origin 5 个提交） |
| B02 | 隔离实例启动 | 独立数据库/目录/端口，生产不受影响 | ✓ | 开发 8799 / 打包 8798 / 解压 8797，各自独立库；生产 8768 三个进程全程在跑，库文件 mtime 停在 00:02:14 未变 |

### D —— 数据模型与关联

| ID | 场景 | 要求 | 结果 | 证据 |
|---|---|---|---|---|
| D01 | 同名概念作为考点和方法 | 可复用一个概念身份，用途分别保留 | ✓ | 测试 `test_D01_same_concept_can_be_primary_and_method`：1 个概念、2 条关联（primary + method） |
| D02 | 中位线定理与构造中位线 | 不自动合并，相关关系可表达 | ✓ | 测试 `test_D02_related_concepts_are_not_merged`：2 个概念 + `related` 关系；相关关系不进下级展开 |
| D03 | 相近名称不同定义 | 出现歧义选择，不默认合并 | ✓ | 测试 `test_D03_similar_names_do_not_merge_by_default`；界面 `is-ambiguous` 标记 + 范围说明 |
| D04 | 两条解法使用同一方法 | 分别保存，互不覆盖 | ✓ | 打包版实机第 16 题：两条方法关联分别绑「解法一」 |
| D05 | 同一方法用于两小问 | 范围及依据独立，整题去重 | ✓ | 测试 `test_D05_same_method_in_two_subquestions_keeps_separate_evidence`：2 条关联、检索结果 1 题 |
| D06 | 小问重排或删除 | 稳定绑定保留或标需复核 | ✓ | `LibrarySubquestion.stable_key` 与展示编号分离；显示编号变了稳定键不变 |
| D07 | 跨题关联解法或证据 | 服务端拒绝 | ✓ | 测试 `test_D07_cross_question_binding_is_rejected`：跨题小问、跨题分支各抛 `AssertionError_` |
| D08 | 多父级分类和循环 | 多父级可用，循环被拒绝 | ✓ | 测试 `test_D08_cycle_is_rejected`；模型层另有 `CheckConstraint(~Q(parent=F("child")))` 挡自环 |
| D09 | 同请求重复提交 | 只产生一次正式修改 | ✓ | 测试 `test_D09_repeated_request_applies_once`；**打包版实机**：第二次请求 `replayed=True`，关联数仍为 1 |
| D10 | 两窗口同时修改 | 不丢更新，显示可恢复的冲突 | ✓ | 测试 `test_D10_conflicting_revision_is_refused`：过期的 `expected_revision` 被拒（409）；**打包版实机**：传过期版本号返回 409「这道题的标签已在别处修改，请刷新后重试」 |

### R —— 整理与候选

| ID | 场景 | 要求 | 结果 | 证据 |
|---|---|---|---|---|
| R01 | 普通添加标签 | 不要求先维护完整目录 | ✓ | 测试 `test_R01_tagging_needs_no_catalogue_setup`；打包版实机：贴标签 200 |
| R02 | 空标签、无解析题 | 能整理考点，不虚构方法依据 | ✓ | 测试 `test_R02_question_without_solution_gets_no_fabricated_method`：关联 1 条、依据 0 条 |
| R03 | AI 生成完成 | 仅新增候选，不增加正式关联 | ✓ | 测试 `test_R03_ai_result_only_becomes_candidate` |
| R04 | 修改后采纳候选 | 按人工修改后的内容写入并保留原建议 | ✓ | 测试 `test_accept_writes_the_role_the_teacher_chosen`（改成「辅助用到」，正式关联的 role 就是 supporting）、`test_accept_keeps_the_original_suggestion`（原建议和理由原样留着，decision=accepted）、`test_accept_without_edit_uses_the_suggested_role`（没改就按建议的用途写，不落成「旧关联，作用未细分」） |
| R05 | 解析版本改变 | 受影响的方法需复核；无关考点不整批失效 | **—** | **本轮未做**。数据模型有 `evidence_status=needs_recheck` 可承载，但事件未接 |
| R06 | 改标签显示色或排序 | 不导致数学依据全面失效 | ✓ | 显示色/排序不在服务层写路径里（`Design-Decisions.md` §4 约束 8） |
| R07 | 候选拒绝、刷新、重试 | 决策不丢失，不重复出现为新建议 | ✓ | 候选状态在 `LibraryTagSuggestion` 里持久化；迁移演练实测迁移前后状态一位不差 |
| R08 | 无需核对的浏览操作 | 不写正式标签、不自动调用 AI | ✓ | 工作台打开/翻题/浏览只发 GET；界面写明「生成建议要你自己点，不会自动跑」 |
| R09 | 旧正式标签无解法绑定 | 保留人工历史，标明旧记录 | ✓ | 测试 `test_R09_legacy_assignment_stays_marked_as_legacy`：正式匹配 0、旧记录 1 |

### 撤销（计划第 4 节约束 10）

| 场景 | 要求 | 结果 | 证据 |
|---|---|---|---|
| 撤销「贴标签」 | 真的把那条关联移除 | ✓ | 测试 `test_undo_attach_puts_the_label_back` |
| 撤销「移除」 | 按快照原样建回来（用途、范围、来源、理由都不丢） | ✓ | 测试 `test_undo_detach_puts_the_label_back` |
| 冲突 | 那条关联被别人改过就别动，别盖掉别人的改动 | ✓ | 测试 `test_undo_refuses_when_the_label_changed_underneath`：别人的 `role` 改动仍在 |
| 幂等 | 同一个请求 ID 重复提交只撤一次 | ✓ | `undo_assertion` 走同一套 `client_request_id` 去重 |
| 可回退 | 撤销本身也记一条操作 | ✓ | 写 `action="undo_assertion"` |

### S —— 搜索语义

| ID | 场景 | 要求 | 结果 | 证据 |
|---|---|---|---|---|
| S01 | 搜主要考查 | 辅助用到和仅词面出现不能冒充主考 | ✓ | 测试 `test_S01_supporting_does_not_impersonate_primary` |
| S02 | 搜已确认替代解法 | 找到对应小问并解释是另一解法 | ✓ | 打包版实机：第 16 题显示「配方法（1）· 解法一」「分类讨论（2）· 解法一」 |
| S03 | 方法 A、B 分属两解法 | 默认同解法 AND 不命中；切分别存在才命中 | ✓ | 测试 `test_S03_methods_in_different_variants_do_not_match_by_default`：same_variant → 0，separate → 1 |
| S04 | 多路径命中同题 | 返回一题，理由都留着，计数不重复 | ✓ | 测试 `test_S04_multi_path_hit_returns_one_question` |
| S05 | 包含下级概念 | 扩展检索，不复制正式父级标签 | ✓ | 测试 `test_S05_include_descendants_widens_search_without_copying_tags` |
| S06 | 排除某方法且题未整理 | 进入未知范围，不当作已证实不用 | ✓ | 测试 `test_S06_exclusion_on_untouched_question_is_unknown` |
| S07 | 只出现「中点」文字 | 可列线索，不计入已确认构造中位线 | **部分** | 线索分区与正式分区在代码里分开；**但「全文提及 → 线索」这条自动线索链本轮未接** |
| S08 | 保存完整筛选 | 来源、题型、关键词、用途、范围、排序都恢复 | ✓ | 测试 `test_S08_saved_search_restores_every_condition` |
| S09 | 搜索总数、全选、导出 | 使用同一条件集合 | ✓ | 列表、计数、命中解释都走 `library_query.run_query()` 同一个服务层 |
| S10 | 无 API key 找题 | 结构检索和本地转换正常可用 | ✓ | 测试 `test_S10_search_works_without_any_model`；打包版实机：转换与检索都不调外部 |
| S11 | 自然语言有歧义 | 条件可见可修改，不暗中猜定 | ✓ | 测试 `test_S11_*`；界面把「这句话被理解成了什么」摆出来 |

### C —— 教材目录与专题

| ID | 场景 | 要求 | 结果 | 证据 |
|---|---|---|---|---|
| C01 | 题属于两个专题 | 题面不复制，移出专题不删题 | ✓ | 测试 `test_C01_question_in_two_collections_keeps_one_paper` |
| C02 | 概念出现在两套教材 | 共享概念，教材目录分别保留 | ✓ | 测试 `test_C02_chapters_keep_their_own_tree` |
| C03 | 教学对应与来源位置 | 明确显示，不相互冒充 | ✓ | 模型层 `LibraryQuestionPlacement.Relation` 分 `source` / `teaching`；**界面未做**（见 Known-Issues 2.4） |
| C04 | 拖动地图节点 | 只改视图；关系修改须明确操作 | ✓ | 地图本轮没动；关系修改只在 `relate_concepts()` 这一个明确入口 |
| C05 | 概念合并及撤销 | 引用、别名、历史可追溯；冲突时不覆盖 | ✓ | 迁移演练：归并后旧 ID 仍在（`merged_into`），`verify` 通过；**界面未做**（Known-Issues 2.2） |

### M —— 迁移

| ID | 场景 | 要求 | 结果 | 证据 |
|---|---|---|---|---|
| M01 | 旧库 dry-run | 无数据库内容变动，输出影响与歧义 | ✓ | 演练：dry-run 后迁移映射 0 条、标签/关联计数不变；报出 3 组冲突 + 2 条警告 |
| M02 | 旧正式、候选、拒绝记录迁移 | 数量及逐条映射可解释，状态不升级 | ✓ | 演练：**19 条关联全部 `role=legacy`**，候选 `pending 2 / queued 1 / rejected 1` 一位不差；映射 34 条 |
| M03 | 重复执行迁移 | 不重复建实体或贴标 | ✓ | 演练：重复 apply 前后 (标签 27, 关联 19, 章节 1, 映射 34) 完全一致 |
| M04 | 中断后恢复迁移 | 原子回滚或按检查点安全恢复 | ✓ | `apply_migration()` 全程单事务，中断即回滚；重跑同一批次接着走 |
| M05 | 恢复切换前备份 | 实际可启动并核对业务记录 | **—** | **本轮未做**（真实迁移没授权）。步骤已写进 `Migration-Guide.md` §6 |
| M06 | 升级后已有新编辑 | 恢复前保留新增记录，不静默丢弃 | **—** | **本轮未做**。流程已写进 `Migration-Guide.md` §6 |
| M07 | 旧程序接触新结构 | 隔离或明确阻止，不损坏新库 | ✓ | 旧程序读不到新结构（`canonical_key` / `role` 是新列），靠隔离目录挡；本轮全程隔离 |

### W —— Windows 打包版

| ID | 场景 | 要求 | 结果 | 证据 |
|---|---|---|---|---|
| W01 | Windows 打包版操作 | 添加、核对、撤销、检索整条路径实际通过 | ✓ | **实跑通过**：检索 → 找概念 → 贴标签 → 幂等重复提交 → 搜得到 → 移除 → 搜不到 → 关联清零 → 中文转换 → 保存搜索 → 迁移 dry-run → **撤销** → 导出。脚本 `tools/check_export_tag_refactor.py`，源码版与打包版各 **21/21 通过** |
| W02 | 中文、空格路径 | 启动、素材、备份、导出正常 | ✓ | 隔离目录全中文路径启动通过；导出/备份/素材路径均在该目录下验证过 |
| W03 | 打印和组卷 | 标签变更不改变固定题面、选题和答案版本 | ✓ | **专门验的**：给题贴上新标签（用途、范围、解析版本）后重新导出，**文件内容一字不变**；组卷草稿的固定版本同样一字不变。标签不印到卷面上，也不被它挡住 |
| W04 | PDF/Word 实际导出 | 文件存在、可打开，公式配图及选定答案正确 | ✓ | 四种题型的 Word：题干都在、公式是 **OMML**（不是图片）、配图进了 `word/media`、学生卷没解析、教师卷有解析；「分别导出」给的是真 zip（题目卷/答案卷各能打开）；PDF 文件头合法、真的有页、题面文字能提取 |
| W05 | 窄窗口／缩放／中文输入法 | 无重要遮挡，组合输入不误触贴标或翻题 | ✓ | 900px 降级为单列堆叠；700px 下**横向溢出 0px**、搜索框 335px、按钮尺寸正常、题干宽 611px 未压扁；中文输入法合成期（compositionstart→input isComposing）**无误触**，卡片数与面板状态不变 |
| W06 | 发布产物审计 | 无真实题库、凭据、用户截图或本机私密配置 | ✓ | 打包脚本自带审计：「打包审计通过：未发现数据库、试卷、凭据或运行日志」；另查产物内 `*.sqlite3` → 空 |

#### W01～W04 的实测脚本

```powershell
# 造虚构数据 → 导 Word/PDF/分卷 → 解开文件逐项核对 → 贴标签再导一次对比
python tools/check_export_tag_refactor.py --database <db> --base http://127.0.0.1:8799 --data-root <data>
```

源码版（8799）与打包版（8798）各跑一遍，**都是 21/21**。脚本自己造四种题型的虚构题，
不读真实题库。

### 性能（第 11 节）

基准脚本 `tools/bench_query.py`：固定种子（20261025）、合成占位数据、热查询 20 次、报中位数和 P95。
**合成占位数据不是数学测试集**；换机器、换题量，数字不可比。

| 查询 | 1,000 题 P95 | 10,000 题 P95 |
|---|---|---|
| 空条件（全部） | 36.2 ms | 116.5 ms |
| 主要考查＝单个概念 | 7.6 ms | 28.9 ms |
| 主要考查＝三个概念（全中） | 8.6 ms | 65.6 ms |
| 主要考查＝十个概念（任一） | 17.8 ms | 158.5 ms |
| 包含下级概念 | 8.3 ms | 31.2 ms |
| 方法＝单个（同解法） | 6.1 ms | 24.8 ms |
| 方法＝两个（同解法 AND） | 8.3 ms | 45.1 ms |
| 方法＝两个（分别存在） | 6.9 ms | 31.7 ms |
| 方法＋排除（未知单独计数） | 5.8 ms | 20.1 ms |
| 关键词＋题型 | 3.5 ms | 16.0 ms |

| 规模 | 题量 | 关联量 | 最慢一条 P95 | 验收目标 | 结论 |
|---|---|---|---|---|---|
| 1,000 题 | 1,000 | 1,534 | **36.2 ms** | ≤ 500 ms | **达标** |
| 10,000 题 | 10,000 | 15,334 | **158.5 ms** | ≤ 500 ms | **达标** |

**第一版是超标的**（10,000 题时最慢一条 P95 5995 ms，超 12 倍）。原因：检索把全部题拉成
模型对象（`content` 是整段 JSON），再在 Python 里逐题过一遍条件 —— 一万道题就是一万次判断，
条件再准也没用。

改法（三处）：概念和方法条件先在库里算出「哪几道题可能相关」，要细看的题量从全部题缩到
真正相关的那些（精确判定仍在那些题上做，语义没变）；题目只取要用的字段，不取 `content`；
空条件时只算要显示的那批 —— 后面那些算出来也没人看。

**10,000 题的提速幅度**：空条件 5413→102 ms，主要考查 1920→27 ms，方法 1899→18 ms，
关键词 399→15 ms。

原始结果：`bench/bench-1k-final.json`、`bench/bench-10k-final.json`（随仓库提供摘要，
完整原始数据在 `work/bench/`）。

---

## 4. 打包产物

| 项目 | 结果 |
|---|---|
| 打包命令 | `.\packaging\build.ps1 -Version 1.27.0 -SkipInstaller` |
| 打包退出码 | **0** |
| 版本一致性 | 源码 `version.py` = 1.27.0；产物 `tiyouju.exe --version` = **tiyouju 1.27.0**（打包脚本硬闸校验） |
| 可运行文件夹 | `work/question-bank-card/packaging/dist/QuestionBankCard/` |
| 便携 ZIP | `work/candidate/QuestionBankCard-1.27.0-portable.zip`（**62.0 MB**） |
| SHA-256 | `575c0755c622987ae82809c8a0ec9aab8bd0548465ec308a78cbaa018c729265` |
| 校验文件 | `QuestionBankCard-1.27.0-portable.zip.sha256`（同目录） |
| ZIP 完整性 | `zipfile.testzip()` → **OK** |
| 解压后启动 | 从 ZIP 解压 → `tiyouju.exe --version` = 1.27.0 → 起服务 8797 → `/api/library/search` **200**、`/library/concepts` **200 且含三入口** |
| 安装包（Setup.exe） | **没出** —— 本机没装 Inno Setup，用了 `-SkipInstaller`（计划要求「安装包**或** ZIP」） |
| 新工作台资源 | `_internal/frontend/library-concepts.{html,js,css}` 三个文件都在包里 |

---

## 5. 实机证据（截图）

全部用虚构题。截图在 `work/screenshots/`。

| 文件 | 内容 |
|---|---|
| `01-找题主页.png` | 左栏三入口（教材目录/数学主题/个人专题）、已确认匹配与未整理分区、每题的命中理由带用途 |
| `02-当前题整理五块.png` | 右侧五块：主要考查 / 当前解法 / 其他已记录解法 / 更多信息 / 待核对建议 |
| `03-打包版-当前题整理.png` | **打包版**（8798）的同一界面；显示「配方法（1）· 解法一」「分类讨论（2）· 解法一」 |

浏览器操作记录（真实交互，非静态断言）：

1. 打开 `http://127.0.0.1:8799/library/concepts` → 页面加载，三入口在位；
2. 空查询 → 「已确认匹配 12 题 / 未整理 6 题」，题卡按分区列出；
3. 点第 18 题（两个方法分属两个解法）→ 右侧五块展开，关联行显示「全等三角形 解法一」「分类讨论 解法二」；
4. 点第 1 题 → 待核对建议两条，各带用途、理由、可展开的依据、采纳/拒绝按钮；
5. 搜索框输入「第（1）问用配方法」→ 转换结果显示「第（1）问 → 小问：第（1）问」「配方法 → 方法：配方法」，条件条显示方法名（不是 UUID）；
6. 切到打包版 8798 → 同样打开，点第 16 题 → 显示「配方法（1）· 解法一」「分类讨论（2）· 解法一」。

---

## 6. 生产环境状态（结束时）

```
PID 19256  QuestionBankCard.exe                              （主窗口）
PID 76504  QuestionBankCard.exe --internal-role run_worker
PID 80756  QuestionBankCard.exe --internal-role runserver 127.0.0.1:8768
```

生产库 `<生产数据目录>\db.sqlite3` 核对结果：

| 项目 | 值 | 应为 |
|---|---|---|
| 0030 新表数量 | **0** | 0 ✓ |
| `assignment` 有 `role` 列 | **False** | False ✓ |
| 最后一条迁移记录 | `0029_realign_untouched_suggestions` | 0029 ✓ |
| 题库 / 标签 / 关联 / 候选 | **869 / 306 / 21 / 904** | 与本轮开始时一致 ✓ |
| 文件 mtime | **2026-10-25 00:02:14** | 本轮开始前 ✓ |

**生产库一个字节都没被改过。安装版还是 1.26.31。**

---

## 7. 过程中查到的真问题（都已修，记下来）

| 问题 | 后果 | 修在哪 |
|---|---|---|
| `values_list("child_id")` 对 UUID 字段返回 **UUID 实例**不是字符串 | 集合里混着两种类型，「包含下级」的下级展开**永远返回空** | `backend/core/library_query.py`，代码里留了注释 |
| 自然语言转换的匹配顺序错了 ——「方法」这种泛词先被当用途词删掉 | 「配方法」被切成「用配」，词典匹配不上 | `backend/core/library_nlquery.py`：改成**先查词典，再认说法**；用途词表里去掉单独的「方法」 |
| 检索把「不匹配」和「未整理」混为一谈 | 有条件时，18 道题全被塞进「未整理」区，计数失去意义 | `backend/core/library_query.py`：不匹配的不进结果；「未整理」只在无正向条件时出现 |
| 展示编号「第（1）问」对不上库里的小问 UUID | 按小问检索永远搜不到 | `backend/core/library_query.py` 的 `_scope_matches`：展示编号和稳定键都能认 |
| `plan_migration` 按库里存的 `canonical_key` 分组（旧数据是空的） | dry-run **漏报重名冲突** | `backend/core/library_tag_migration.py`：改成按算出来的归一化名分组 |
| 用 `Start-Process -ArgumentList` 传 `set "VAR=..."` | 引号和值都被 cmd 二次解析吃掉 → **打包版连上了生产库**（只读访问，未写入） | 改用 `.cmd` 文件启动；生产库已逐项核对未受影响（见 §6） |

---

## 8. 性能

**本轮没做基准测试。**

计划第 11 节要求用 1,000 题和 10,000 题的固定种子数据集测热查询的中位数和 P95（建议列表 P95 ≤ 500 ms）。

本轮只验证了 18 道虚构题的功能正确性，**没有规模测试**。合成数据生成器 `tools/make_fake_library.py` 已经能批量造题，下一步直接拿它做基准即可。

---

## 9. 结论

**做完了并验证过的**：数据模型与约束、服务层、结构化检索的三态语义、迁移工具的 dry-run/apply/verify/幂等、中文条件转换、候选审核闭环的核心、独立工作台界面、打包、产物审计、解压启动、打包版的贴标到检索整条路径。

**没做的**：真实题库迁移、安装版替换、打印/组卷/导出的实测、性能基准、题库主页左栏的三入口改造、概念合并/停用界面、旧「保存标签组合」转换、难度字段。

详见 `Known-Issues.md`。
