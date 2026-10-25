# Migration-Guide.md —— 旧标签数据迁移说明

适用版本：题有据 1.26.31 → 1.27.0（候选）
日期：2026-10-25

**这份说明是给将来执行迁移的人看的。本次没有对真实题库执行迁移。**

---

## 0. 三句话

1. 迁移分三步：**先跑 Django 结构迁移（0030）→ 再跑迁移工具（apply）→ 最后核对（verify）**。
2. 迁移**不删除**任何旧表、旧 ID 或原始内容；只新增结构、建立映射、标注状态。
3. 迁移**不提升**任何数学可信度 —— 旧知识点不会自动变成「主要考查」，旧方法不会自动变成某条具体解法。

---

## 1. 迁移前要确认的三件事

| 要确认的 | 怎么查 | 本次实测 |
|---|---|---|
| 主库的真实路径 | `start_question_bank.py` 的 `USER_ROOT` → `DATABASE`。**不是**看哪个文件名叫 `db.sqlite3` | `<本机路径>\AppData\Local\QuestionBankCard\db.sqlite3`（53.5 MB） |
| 当前有没有进程在用 | `Get-NetTCPConnection -LocalPort 8768 -State Listen` + 进程列表 | 三个进程：主窗口、`run_worker`、`runserver 8768` |
| 备份能不能用 | 「一致性备份 + SHA-256 校验」——在线 SQLite 不能只复制主文件，WAL 一致性要一起考虑 | 软件自带 `data\backups\` 每日自动备份；另有 `backups\pre-migrate-*.sqlite3` 手动备份 |

**本机实测发现**：数据根目录下同时存在 `db.sqlite3`、`db.sqlite3.备份-20261024-220140`、`backups\` 下十余个历史备份，以及 `data\backups\` 下的自动备份 zip。**光看文件名判断不出哪个是当前主库**，必须按上面的路径逻辑确认。

---

## 2. 迁移分三步

### 第 1 步：Django 结构迁移（只改结构，不搬数据）

```powershell
cd <安装目录>\backend
..\.venv\Scripts\python.exe manage.py migrate core
```

`0030_tag_system_refactor` 做的是：

- 新增 12 张表（教材目录、专题、小问、解法分支、依据、整理程度、保存搜索、迁移映射等）
- 给既有表加字段（概念的规范身份键 / 范围说明 / 类型提示 / 合并目标；关联的用途 / 范围键 / 依据状态 / 核对状态 / 小问 / 分支外键）
- 换两条唯一约束：
  - `LibraryTag`：`(kind, name)` → `canonical_key`（条件唯一，`canonical_key != ""`）
  - `LibraryTagAssignment`：`(publication, tag)` → `(publication, tag, role, scope_key)`

**这一步不加数据迁移** —— 旧数据拿到字段默认值（`role=legacy`、`scope_key="whole"`、`evidence_status=unbound`），语义由第 2 步落实。

**回滚**：这个迁移是可逆的（`migrate core 0029`），因为它没做数据搬迁。

### 第 2 步：迁移工具（业务映射）

```powershell
# 先看一眼要做什么（不改任何数据）
python manage.py shell -c "from core import library_tag_migration as m; import json; print(json.dumps(m.plan_migration(), ensure_ascii=False, indent=2))"

# 真做（按批次）
python manage.py shell -c "from core import library_tag_migration as m; import json; print(json.dumps(m.apply_migration('tag-refactor-1'), ensure_ascii=False))"

# 核对
python manage.py shell -c "from core import library_tag_migration as m; import json; print(json.dumps(m.verify_migration('tag-refactor-1'), ensure_ascii=False, indent=2))"
```

或者走 HTTP（只在本机、只从题库页面发起）：

```
GET  /api/library/migration?action=plan
POST /api/library/migration   {"action":"apply","batch":"tag-refactor-1"}
GET  /api/library/migration?action=verify&batch=tag-refactor-1
GET  /api/library/migration?action=report&batch=tag-refactor-1
```

**批次号**自己取，建议带日期（`tag-refactor-20261025`）。同一个批次重复执行只会补齐没做完的部分。

### 第 3 步：核对

`verify_migration()` 检查的是业务不变量，不是「数据库能打开」：

1. 旧人工关联的用途**没有被偷偷改成**「主要考查」；
2. 每条候选都有对应的迁移映射，数量对得上；
3. 被合并的旧 ID 还在，且指向存在的目标（没有悬空引用）；
4. 每条关联的范围键非空；
5. 每个章节都有规范化键。

---

## 3. 映射规则（迁移实际做了什么）

| 旧内容 | 新处理 | 迁移映射的 disposition |
|---|---|---|
| 章节容器（`system_key` 以 `section:` 开头） | 转成 `LibraryChapter`，教材为「通用教学目录／版本未指定」 | `converted` |
| 章节上的直接人工贴标 | 记成 `LibraryChapterConcept`（未确认）+ 保留旧标签快照 | `needs_review` |
| 旧知识点正式关联 | `role = legacy`（旧关联，作用未细分） | `carried` |
| 旧方法正式关联 | 有可靠依据才 `evidence_status = current`，否则 `unbound` | `carried` |
| 历史标签（`kind=legacy`）撞上正式概念 | **归并到正式概念**，旧 ID 保留（`merged_into`） | `converted` |
| 两个正式概念归一化后同名 | **不自动合并**，各自保留、标歧义 | `ambiguous` |
| 自定义标签 | 保留，等人工决定是概念还是专题 | `carried` / `needs_review` |
| 旧上下级关系 | 保留，`source = legacy`，不参与新语义推断 | `carried` |
| AI 候选 | **一条不动**，状态、依据、版本、草稿全留着 | `carried` |
| 排队中（从未生成）的候选 | 不算「待核对」，也不替用户触发 | `carried`（报告里单列说明） |

### 「可靠依据」的判据

收紧的：人工确认 + 写了理由 + 有对应的解析版本。三条缺一不可。

**实测**：读 1.26.29 迁移前的备份库，21 条正式关联的 `scope` **全部**是 `{"whole": true}`，且没有一条绑定了解析版本 → 全部判为 `unbound`。这正是要的效果：旧记录不能冒充精确的方法证明。

---

## 4. 章节容器怎么认（不靠名字前缀）

**判据：`system_key` 以 `section:` 开头。**

这是 0028 迁移留下的系统来源键（旧的「分组」被搬成章节标签时打的）。实测 1.26.29 迁移前的备份库：17 个章节容器全部带这个前缀（13 个 knowledge + 4 个 method）。

用户自己建的标签 `system_key` 是 `knowledge:正数与负数` 这种，**不带** `section:` 前缀 —— 所以用户建的「函数」不会被当成章节搬走。

---

## 5. 演练记录（虚构旧库，2026-10-25 实测）

演练库：`work/isolated/rehearsal.sqlite3`，虚构数据由 `tools/make_legacy_fixture.py` 生成。

造出来的旧数据：27 个标签、19 条正式关联、4 条候选、1 个章节容器、2 条上下级关系（含 1 组父子类型不一致）。

### DRY-RUN

```
counts: {"tags": 27, "tags_missing_canonical_key": 8, "sections": 1, "assignments": 19,
         "assignments_with_evidence": 2, "assignments_without_evidence": 17,
         "section_assignments": 1, "custom_tags": 1, "legacy_tags": 1,
         "relations": 2, "suggestions": 4}
suggestion_status: {"rejected": 1, "queued": 1, "pending": 2}
conflicts:
  - {"canonical_key": "三角函数", "kinds": ["knowledge","legacy"], "resolvable": "legacy_into_formal"}
  - {"canonical_key": "反证法", "kinds": ["method"], "resolvable": "ambiguous"}
  - {"canonical_key": "配方法", "kinds": ["method"], "resolvable": "ambiguous"}
warnings:
  - 1 条候选处于排队中，从未真正生成过。迁移不把它们算作待核对，也不替用户触发。
  - 旧章节不知道教材版次，迁到「通用教学目录／版本未指定」，不编造教材版本。
```

**dry-run 之后库里没被动过**：迁移映射 0 条，标签/关联计数不变。

> 注：演练库里的「反证法」「配方法」各出现两次，是因为造数据脚本复用了已存在的新结构标签 —— 属于演练数据本身的重复，不是真实库的形态。真实库实测（1.26.29 备份）归一化重名只有 **1 组**。

### APPLY

```
{"batch": "rehearsal-1", "tags_backfilled": 4, "tags_merged": 1, "tags_ambiguous": 4,
 "assignments_backfilled": 19, "chapters_created": 1, "chapter_concepts": 1,
 "relations_marked": 0, "skipped": 0}
```

迁移后：

| 检查 | 结果 |
|---|---|
| 标签总数 | 27（**没少也没多**） |
| 有规范身份键的 | 24 |
| 归并的旧 ID | 1（还在，指向新概念） |
| 章节 | 1，教材 = 「通用教学目录」，`unspecified=True` |
| 关联的用途分布 | **全部 19 条 `legacy`** —— 没有一条被升级成「主要考查」 |
| 候选状态 | `pending 2 / queued 1 / rejected 1` —— **一条不动** |
| 迁移映射 | 34 条 |

### VERIFY

```
ok = True    problems = []
counts = {"assignments": 19, "legacy_role_assignments": 19, "suggestions": 4,
          "chapters": 1, "concepts": 27, "merged_concepts": 1, "migration_entries": 34}
```

### 重复执行（幂等）

```
之前: (标签 27, 关联 19, 章节 1, 映射 34)
之后: (标签 27, 关联 19, 章节 1, 映射 34)
一致: True
```

### 中断恢复

`apply_migration()` 全程包在**一个事务**里。中断即整体回滚，不会留下半截状态；重跑同一个批次即可接着走。

---

## 6. 回滚

### 从新结构退回旧结构

```powershell
python manage.py migrate core 0029
```

这个迁移是可逆的（只加表加字段，没搬数据）。**但退回去之后，新结构里录入的数据会丢** —— 那是切到新结构之后新写的关联、候选、章节、专题。

### 从错误的迁移恢复

1. 先导出现在的新增操作（`LibraryTagOperation` 里 `created_at` 大于切换时间的记录），别让恢复把切换后写的编辑静默抹掉；
2. 恢复切换前的备份；
3. 校验备份的 SHA-256；
4. 启动旧程序，确认能起来、题库记录对得上。

**明确区分**：备份恢复 ≠ 操作撤销 ≠ 业务降级。

- **操作撤销**：在程序里撤销一次标签操作，有真正的逆操作和冲突检查；
- **备份恢复**：整库回到某个时间点，切换后新增的东西会被抹掉（所以要先导出）；
- **业务降级**：新版本能跑，但某些新功能用不了。

**不要承诺所有新数据都能无损降级给旧程序。** 旧程序不认识新结构（`LibraryTag.canonical_key`、`LibraryTagAssignment.role` 这些），它读新库会出问题 —— 所以隔离目录是基础边界，不要靠旧程序自己识别新版本标记。

---

## 7. 迁移后的核对清单

迁移完，人工过一遍这几条：

- [ ] 正式关联的用途分布里，`legacy`（旧关联，作用未细分）占多数 —— 说明没有把旧标签偷偷升级成「主要考查」；
- [ ] 候选计数与迁移前一致（`pending` / `queued` / `rejected` 各多少条）；
- [ ] 章节都落在「通用教学目录／版本未指定」下 —— 说明没有编造教材版本；
- [ ] 报告里 `ambiguous` 的条目逐条看过，决定是保留歧义还是人工合并；
- [ ] 报告里 `needs_review` 的条目逐条看过（章节上的直接贴标、自定义标签）；
- [ ] `tag_revision` 计数器没有因为迁移被重置（人工确认过的记录仍在）。

---

## 8. 迁移不会做的事

- 不会把旧知识点自动升级成「主要考查」；
- 不会把旧方法编造成某条具体解法；
- 不会自动合并近义概念；
- 不会把「排队中、从没生成过」的候选算成「待核对」；
- 不会替你触发任何 AI 生成；
- 不会删除旧表、旧 ID、原始内容；
- 不会改动旧候选的逐条决策、依据、版本、草稿和拒绝记录。
