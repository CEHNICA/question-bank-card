import uuid

from django.db import models


class ActiveQuestionManager(models.Manager):
    """Normal application queries never expose cards placed in the recycle bin."""

    use_in_migrations = True

    def get_queryset(self):
        return super().get_queryset().filter(deleted_at__isnull=True)


class Paper(models.Model):
    """一份上传的试卷。status 走完 queued → parsing → reading → ready。"""

    class MaterialType(models.TextChoices):
        EXAM = "exam", "试卷"
        BOOK = "book", "书籍"

    class Status(models.TextChoices):
        QUEUED = "queued", "排队中"
        PARSING = "parsing", "MinerU 解析中"
        NEEDS_GROUPING = "needs_grouping", "等待确认资料结构"
        SEGMENTING = "segmenting", "切题中"
        READING = "reading", "AI 读题中"
        READY = "ready", "待你终审"
        FAILED = "failed", "失败"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    filename = models.CharField(max_length=255)
    # 人可修改的任务显示名；filename 始终保留最初上传的文件名，方便追溯原卷。
    task_name = models.CharField(max_length=255, blank=True, default="")
    kind = models.CharField(max_length=8)  # pdf | image | docx
    # 资料结构与文件格式分开记录：同样是 PDF，既可能是一份试卷，也可能是一本书。
    material_type = models.CharField(max_length=8, choices=MaterialType.choices, default=MaterialType.EXAM)
    sha256 = models.CharField(max_length=64, db_index=True)
    source_path = models.CharField(max_length=500)  # 上传的原文件
    render_path = models.CharField(max_length=500, blank=True)  # 用于渲染页面的 PDF/图片（docx 转成的 PDF）
    zip_path = models.CharField(max_length=500, blank=True)
    pages = models.JSONField(default=list)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED)
    progress = models.PositiveIntegerField(default=0)
    total = models.PositiveIntegerField(default=0)
    error = models.TextField(blank=True)
    notes = models.JSONField(default=list)  # 切题过程中的说明（如"第 5 题由 AI 定位"）
    # 跨文件格式的资料结构判断：suggested_groups、signals 以及人工 confirmed 结果。
    # 不放进 photos，因为 PDF 和书籍同样需要这份可追溯记录。
    structure = models.JSONField(default=dict, blank=True)
    # Local intake is independent of cloud recognition. Per-page decisions and
    # warnings stay with the original document; an empty plan is legacy MinerU.
    processing_plan = models.JSONField(default=dict, blank=True)
    # One compare-and-swap token for original-page layout topology.
    layout_revision = models.PositiveIntegerField(default=0)
    layout_page_epoch = models.PositiveIntegerField(default=0)
    # 手机照片（一张或几张合成一份卷）：
    # {"files": [{"name", "file", "taken", "straightened"}]（选择顺序）, "enhance": 是否做扫描件效果,
    #  "order": 当前第 i 页是 files[order[i]], "mineru_order": 交给 MinerU 时的页序,
    #  "check": 需要人确认页序的原因, "notes": 给人看的处理说明, "manual": 人工调整过页序}
    photos = models.JSONField(default=dict, blank=True)
    imported_from = models.CharField(max_length=80, blank=True)
    # 已入库任务不能破坏来源链；归档只让任务退出日常侧栏，不删除任何原件或题卡。
    archived = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        # Older pipeline callers hold a Paper loaded before later question
        # saves. A full save must never overwrite the shared CAS counters with
        # that object's stale values. Counter changes use explicit updates.
        if not self._state.adding and kwargs.get("update_fields") is None:
            kwargs["update_fields"] = [
                field.name
                for field in self._meta.concrete_fields
                if not field.primary_key and field.name not in {"layout_revision", "layout_page_epoch"}
            ]
        return super().save(*args, **kwargs)

    class Meta:
        ordering = ["-created_at"]

    @property
    def display_name(self) -> str:
        return self.task_name.strip() or self.filename


class QuestionGroup(models.Model):
    """一份资料中的题号作用域，例如一张试卷、一个章节或一组课后练习。"""

    class Kind(models.TextChoices):
        EXAM = "exam", "试卷"
        CHAPTER = "chapter", "章节"
        EXERCISE = "exercise", "练习"
        EXAMPLE = "example", "例题"
        OTHER = "other", "其他"

    paper = models.ForeignKey(Paper, on_delete=models.CASCADE, related_name="question_groups")
    title = models.CharField(max_length=255)
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.EXAM)
    sequence = models.PositiveIntegerField(default=0)
    # 原 PDF 的一基、闭区间页码；未知时留空，而不是猜测。
    page_start = models.PositiveIntegerField(null=True, blank=True)
    page_end = models.PositiveIntegerField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def scope_label(self) -> str:
        """题组在原卷上的位置标签，例如「第 3 页 · 第 3 组」。

        题组名必须让老师一眼看出它管的是原卷哪一块。文件名、切片序号
        这类内部标识认不出范围，同一份资料里多个组会长得一模一样。
        """
        pages = sorted({page for page in (self.metadata or {}).get("pages") or [] if type(page) is int})
        if pages:
            first, last = pages[0] + 1, pages[-1] + 1
            page_part = f"第 {first} 页" if first == last else f"第 {first}–{last} 页"
        elif self.page_start and self.page_end:
            first, last = self.page_start, self.page_end
            page_part = f"第 {first} 页" if first == last else f"第 {first}–{last} 页"
        else:
            page_part = ""
        group_part = f"第 {self.sequence + 1} 组"
        return " · ".join(part for part in (page_part, group_part) if part)

    def display_title(self) -> str:
        """显示用的题组名。

        试卷的组名以前直接甩文件名，同一份资料里几个组长得一模一样，
        认不出是哪一块，改用位置标签。教材的章节名（"第三章 · 习题"）
        老师认得出来，保留它，只在认不出时退回位置标签。
        """
        title = (self.title or "").strip()
        label = self.scope_label()
        if self.kind == self.Kind.CHAPTER and title and "章" in title:
            return title
        if label:
            return label
        return title or f"第 {self.sequence + 1} 组"

    class Meta:
        ordering = ["sequence", "id"]
        constraints = [
            models.UniqueConstraint(fields=["paper", "sequence"], name="unique_group_sequence"),
            models.CheckConstraint(
                condition=models.Q(page_start__isnull=True, page_end__isnull=True)
                | models.Q(
                    page_start__isnull=False,
                    page_end__isnull=False,
                    page_start__gte=1,
                    page_end__gte=models.F("page_start"),
                ),
                name="valid_group_page_range",
            ),
        ]


class ImportChunk(models.Model):
    """超长 PDF 的本地分片；分片是处理单元，不是用户侧的新任务。"""

    class Status(models.TextChoices):
        QUEUED = "queued", "等待解析"
        PARSING = "parsing", "解析中"
        PARSED = "parsed", "解析完成"
        FAILED = "failed", "解析失败"

    paper = models.ForeignKey(Paper, on_delete=models.CASCADE, related_name="import_chunks")
    sequence = models.PositiveIntegerField()
    # 原 PDF 的一基、闭区间页码。
    source_page_start = models.PositiveIntegerField()
    source_page_end = models.PositiveIntegerField()
    # 第 i 项是分片内第 i 页对应的原 PDF 页码（一基）；用于合并与来源追溯。
    page_map = models.JSONField(default=list)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED)
    sha256 = models.CharField(max_length=64, blank=True, default="")
    artifact_path = models.CharField(max_length=500, blank=True, default="")
    error = models.TextField(blank=True, default="")
    attempts = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sequence", "id"]
        constraints = [
            models.UniqueConstraint(fields=["paper", "sequence"], name="unique_import_chunk_sequence"),
            models.CheckConstraint(
                condition=models.Q(source_page_start__gte=1, source_page_end__gte=models.F("source_page_start")),
                name="valid_import_chunk_range",
            ),
            models.CheckConstraint(condition=models.Q(sequence__gte=1), name="valid_import_chunk_sequence"),
        ]


class Block(models.Model):
    """MinerU 的原始内容块。只用于找题号、大题标题和候选配图，不作为归题单位。"""

    paper = models.ForeignKey(Paper, on_delete=models.CASCADE, related_name="blocks")
    seq = models.PositiveIntegerField()
    type = models.CharField(max_length=40)
    page_idx = models.PositiveIntegerField()
    bbox = models.JSONField(null=True, blank=True)
    text = models.TextField(blank=True)
    # A table MinerU recognised, as its HTML (see core/tables.py).
    html = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["seq"]
        constraints = [models.UniqueConstraint(fields=["paper", "seq"], name="unique_block_seq")]


class Question(models.Model):
    """一张题卡：原卷范围 + AI 最终题面 + 人工终审状态。"""

    class State(models.TextChoices):
        WAITING = "waiting", "等待识读"
        READING = "reading", "识读中"
        GREEN = "green", "自动检查无明显异常"
        YELLOW = "yellow", "请看一眼"
        RED = "red", "识读失败"

    class SourceKind(models.TextChoices):
        EXAMPLE = "example", "例题"
        EXERCISE = "exercise", "练习"
        MANUAL = "manual", "人工补录"
        UNKNOWN = "unknown", "未分类"

    paper = models.ForeignKey(Paper, on_delete=models.CASCADE, related_name="questions")
    # 题号只在题组中用于展示；真正稳定的题源身份与题号、排序和任务改名无关。
    group = models.ForeignKey(QuestionGroup, on_delete=models.SET_NULL, null=True, blank=True, related_name="questions")
    source_key = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    # Presentation only: never part of OCR, approval or publication identity.
    color_index = models.PositiveSmallIntegerField(null=True, blank=True, default=None)
    number = models.PositiveIntegerField()
    section = models.CharField(max_length=120, blank=True)
    question_type = models.CharField(max_length=24, default="unknown")
    regions = models.JSONField(default=list)  # [{page_idx, bbox}]，按阅读顺序
    regions_auto = models.JSONField(default=list)
    body_mode = models.CharField(max_length=16, default="text")
    processing_mode = models.CharField(max_length=16, default="auto")
    # Every content gesture invalidates in-flight OCR, including a range edit
    # followed by an edit back to the same coordinates.
    content_revision = models.PositiveIntegerField(default=0)
    ocr_suggestion = models.JSONField(default=dict, blank=True)
    ocr_pending = models.BooleanField(default=False)
    start_source = models.CharField(max_length=16, default="mineru")
    # 题号会在书籍中反复从 1 开始，不能单独充当身份。来源类型与 MinerU
    # 起始块序号共同提供一次解析内稳定、无需模型的匹配锚点。
    source_kind = models.CharField(max_length=16, choices=SourceKind.choices, default=SourceKind.UNKNOWN)
    source_anchor_seq = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    figure_candidates = models.JSONField(default=list)  # [{label, page_idx, bbox, seq}]
    figures = models.JSONField(default=list)  # [{slot, page_idx, bbox, source}]
    # 零额外识别调用的配图核查结果。自动判断和人工“确认无图”都保留理由，便于撤销与追溯。
    figure_review = models.JSONField(default=dict)
    # 识读记录：甲（MiniMax）、乙（第二位读者）、丙（仅分歧时的裁决）。
    read_a = models.JSONField(default=dict)
    read_b = models.JSONField(default=dict)
    read_c = models.JSONField(default=dict)
    stem = models.TextField(blank=True)
    options = models.JSONField(default=dict)
    text_source = models.CharField(max_length=16, blank=True)  # agree | witness | majority | arbiter | single | human
    state = models.CharField(max_length=10, choices=State.choices, default=State.WAITING)
    flags = models.JSONField(default=list)  # 请看一眼的具体原因
    error = models.CharField(max_length=300, blank=True)
    edited = models.BooleanField(default=False)
    approved = models.BooleanField(default=False)
    approved_at = models.DateTimeField(null=True, blank=True)
    # 审批必须绑定到当时实际看过的题面、配图和原卷范围。
    # 只看 approved 布尔值会让“通过后再改字/换图”绕过复核。
    approved_content_hash = models.CharField(max_length=64, blank=True, default="")
    # 谁打的勾：human（人对照原卷确认）或 ai（AI 助手用 tiyouju 命令行打的勾）。
    # AI 通过照常能入库，但在审核页和题库里都和人工通过分开显示。
    approval_source = models.CharField(max_length=8, blank=True, default="")
    approval_agent = models.CharField(max_length=40, blank=True, default="")
    answer = models.TextField(blank=True, default="")
    analysis = models.TextField(blank=True, default="")
    # 题源：教辅里印在题前的出处（“2026山东枣庄滕州二中月考”）。不是题目文字，
    # 组卷打印时不印；为空时不参与审批校验，老题的审批因此不受影响。
    origin = models.CharField(max_length=120, blank=True, default="")
    # 题型是人（或 AI 助手）选定的：重新识读不改它。调整原卷范围时清掉。
    type_locked = models.BooleanField(default=False)
    reread_requested = models.BooleanField(default=False)
    # Review-time deletion is deliberately reversible.  The card itself stays
    # intact so manual edits, approval evidence and publication links survive
    # deletion and undo byte-for-byte.
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)
    deletion_batch = models.ForeignKey(
        "QuestionDeletionBatch", on_delete=models.SET_NULL, null=True, blank=True, related_name="questions"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ActiveQuestionManager()
    # Cascades, migrations and the recycle-bin API need an unfiltered manager.
    all_objects = models.Manager()

    def save(self, *args, **kwargs):
        old = None
        topology = ("regions", "group_id", "number", "deleted_at")
        if self.pk and not self._state.adding:
            old = type(self).all_objects.filter(pk=self.pk).values(*topology).first()
        if self._state.adding and self.color_index is None:
            from .question_layout import choose_color

            self.color_index = choose_color(self.paper_id, self.regions)
        super().save(*args, **kwargs)
        written = kwargs.get("update_fields")
        changed = old is None or any(
            (written is None or name in written or name.removesuffix("_id") in written)
            and old[name] != getattr(self, name)
            for name in topology
        )
        if changed:
            Paper.objects.filter(pk=self.paper_id).update(layout_revision=models.F("layout_revision") + 1)

    class Meta:
        ordering = ["number", "id"]
        default_manager_name = "objects"
        base_manager_name = "all_objects"
        indexes = [
            models.Index(fields=["paper", "group", "number"], name="question_source_lookup"),
            models.Index(fields=["paper", "group", "source_kind", "source_anchor_seq"], name="question_source_anchor"),
        ]


class QuestionDeletionBatch(models.Model):
    """One user deletion gesture; it is the unit used by the Undo action."""

    class Origin(models.TextChoices):
        USER = "user", "人工删除"
        RESEGMENT = "resegment", "重新切题自动排除"
        LAYOUT = "layout", "原卷布局历史"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    paper = models.ForeignKey(Paper, on_delete=models.CASCADE, related_name="question_deletion_batches")
    # Kept even after restoration so retrying the same undo remains idempotent.
    question_ids = models.JSONField(default=list)
    origin = models.CharField(max_length=16, choices=Origin.choices, default=Origin.USER)
    reason = models.CharField(max_length=300, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    restored_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class QuestionLayoutOperation(models.Model):
    """A durable, atomic original-page edit and its guarded inverse."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    paper = models.ForeignKey(Paper, on_delete=models.CASCADE, related_name="layout_operations")
    client_request_id = models.UUIDField()
    request_hash = models.CharField(max_length=64)
    kind = models.CharField(max_length=16)
    source_ids = models.JSONField(default=list)
    target_ids = models.JSONField(default=list)
    before_snapshot = models.JSONField(default=dict)
    after_snapshot = models.JSONField(default=dict)
    after_fingerprints = models.JSONField(default=dict)
    after_layout_revision = models.PositiveIntegerField(default=0)
    page_epoch = models.PositiveIntegerField(default=0)
    source_epoch = models.CharField(max_length=64, blank=True)
    deletion_batch = models.ForeignKey(QuestionDeletionBatch, on_delete=models.SET_NULL, null=True, blank=True)
    blocked_reason = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    undone_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [models.UniqueConstraint(fields=["paper", "client_request_id"], name="unique_layout_request")]


class PublishedQuestion(models.Model):
    """正式题库中的一条不可变快照。修改后再入库会生成新版本，旧版本标记为已替代。"""

    class Status(models.TextChoices):
        PUBLISHED = "published", "Published"
        SUPERSEDED = "superseded", "Superseded"
        WITHDRAWN = "withdrawn", "Withdrawn"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    question = models.ForeignKey(
        Question, on_delete=models.SET_NULL, null=True, blank=True, related_name="publications"
    )
    paper = models.ForeignKey(Paper, on_delete=models.SET_NULL, null=True, blank=True, related_name="publications")
    source_filename = models.CharField(max_length=255)
    number = models.PositiveIntegerField()
    question_type = models.CharField(max_length=24)
    version = models.PositiveIntegerField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PUBLISHED)
    content = models.JSONField(default=dict)
    content_hash = models.CharField(max_length=64)
    search_text = models.TextField(blank=True)
    # 入库时这一版是谁审核通过的：human 或 ai（见 Question.approval_source）。
    review_source = models.CharField(max_length=8, default="human")
    review_agent = models.CharField(max_length=40, blank=True, default="")
    published_at = models.DateTimeField(auto_now_add=True)
    withdrawn_at = models.DateTimeField(null=True, blank=True)
    # 题面以外、可以随时补的东西：知识点标签、AI 参考答案。它们不属于快照，
    # 改了不出新版本；仅在题面和配图指纹一致时继承，AI 结果仍须人工核对。
    extras = models.JSONField(default=dict, blank=True)
    # “|集合间的基本关系|全称量词与存在量词|”：按知识点筛选用。
    tags_text = models.TextField(blank=True, default="")
    # Compare-and-swap revision for tag assignments only.
    tag_revision = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-published_at"]
        constraints = [models.UniqueConstraint(fields=["question", "version"], name="unique_question_version")]


class LibraryJob(models.Model):
    """绑定入库版本的附加任务；当前助手和可选 API 分开执行。"""

    class Executor(models.TextChoices):
        ASSISTANT = "assistant", "当前 AI 助手"
        API = "api", "独立 API"

    class Kind(models.TextChoices):
        TAGS = "tags", "知识点"
        ANSWER = "answer", "AI 参考答案"

    class Status(models.TextChoices):
        QUEUED = "queued", "排队中"
        RUNNING = "running", "进行中"
        DONE = "done", "完成"
        FAILED = "failed", "失败"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    publication = models.ForeignKey(PublishedQuestion, on_delete=models.CASCADE, related_name="jobs")
    kind = models.CharField(max_length=8, choices=Kind.choices)
    # API default keeps pre-migration automatic jobs out of the assistant inbox.
    executor = models.CharField(max_length=12, choices=Executor.choices, default=Executor.API, db_index=True)
    fingerprint = models.CharField(max_length=64, blank=True, default="")
    api_snapshot = models.JSONField(default=dict, blank=True)
    # Explicit one-off exam suggestions never change the global AI switches or
    # overwrite an existing library answer. The user saves the suggestion later.
    solution_scope = models.BooleanField(default=False)
    # User initiated tag proposals are separate from the legacy global feature
    # switch and from automatic-on-intake tasks.
    tag_scope = models.BooleanField(default=False)
    result = models.JSONField(default=dict, blank=True)
    agent = models.CharField(max_length=120, blank=True, default="")
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.QUEUED, db_index=True)
    error = models.CharField(max_length=300, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at"]


class LibraryTagGroup(models.Model):
    """A user-facing collection of tags; groups do not imply question tags."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, unique=True)
    color = models.CharField(max_length=16, blank=True, default="")
    position = models.PositiveIntegerField(default=0)
    system_key = models.CharField(max_length=64, null=True, blank=True, default=None, unique=True)
    # 个人专题：老师自己组织题目的方式，和教材目录、数学概念彼此独立。
    description = models.CharField(max_length=300, blank=True, default="")
    kind = models.CharField(max_length=16, default="personal")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["position", "name", "id"]


class LibraryTag(models.Model):
    """Stable identity for knowledge, method and user-defined tags."""

    class Kind(models.TextChoices):
        KNOWLEDGE = "knowledge", "知识点"
        METHOD = "method", "解题方法"
        CUSTOM = "custom", "自定义"
        LEGACY = "legacy", "历史标签"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.KNOWLEDGE)
    description = models.CharField(max_length=300, blank=True, default="")
    color = models.CharField(max_length=16, blank=True, default="")
    aliases = models.JSONField(default=list, blank=True)
    levels = models.JSONField(default=list, blank=True)
    conditions = models.TextField(blank=True, default="")
    active = models.BooleanField(default=True, db_index=True)
    favorite = models.BooleanField(default=False)
    system_key = models.CharField(max_length=100, null=True, blank=True, default=None, unique=True)
    groups = models.ManyToManyField(LibraryTagGroup, related_name="tags", blank=True)
    # 统一概念身份：规范名的归一化形式。别名、类型提示、显示名都不参与身份。
    # 身份一旦建立就不随「这条是考点还是方法」改变 —— 用途记在关联上。
    canonical_key = models.CharField(max_length=120, blank=True, default="", db_index=True)
    # 范围说明：同名但含义不同时用它区分，界面显示范围，不自动合并。
    scope_note = models.CharField(max_length=300, blank=True, default="")
    # 类型提示可多选（知识点／解题方法／自定义……），只用于推荐和浏览。
    type_hints = models.JSONField(default=list, blank=True)
    # 合并后的重定向目标：旧 ID 保留，历史名称与引用来源可追溯。
    merged_into = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="merged_from"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "id"]
        constraints = [
            # 身份只认规范名。kind 不再参与 —— 「分类讨论」当考点还是当方法是用途问题。
            models.UniqueConstraint(
                fields=["canonical_key"], condition=~models.Q(canonical_key=""), name="unique_library_tag_canonical_key"
            )
        ]
        indexes = [
            models.Index(fields=["kind", "active", "name"], name="library_tag_kind_name"),
            models.Index(fields=["canonical_key", "active"], name="library_tag_canonical_active"),
        ]


class LibraryTagRelation(models.Model):
    """Knowledge/method ancestry, distinct from personal tag collections."""

    class Type(models.TextChoices):
        HIERARCHY = "hierarchy", "上下级"
        RELATED_METHOD = "related_method", "相关解题方法"
        # 通用「相关／使用」关系。分类从属之外的语义都走它，不参与父级继承。
        # 「中位线定理」与「构造中位线」不等价，保留两个概念并用 RELATED 相连。
        RELATED = "related", "相关／使用"

    parent = models.ForeignKey(LibraryTag, on_delete=models.CASCADE, related_name="outgoing_relations")
    child = models.ForeignKey(LibraryTag, on_delete=models.CASCADE, related_name="incoming_relations")
    relation_type = models.CharField(max_length=20, choices=Type.choices, default=Type.HIERARCHY)
    # 这条关系是谁定的：人工、目录迁移、AI 采纳。用于合并和停用时的影响预览。
    source = models.CharField(max_length=24, default="human")
    revision = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["relation_type", "parent_id", "child_id"]
        constraints = [
            models.UniqueConstraint(fields=["parent", "child", "relation_type"], name="unique_library_tag_relation"),
            models.CheckConstraint(condition=~models.Q(parent=models.F("child")), name="library_tag_relation_not_self"),
        ]
        indexes = [models.Index(fields=["relation_type", "parent"], name="tag_relation_type_parent")]


class LibraryTagCatalogue(models.Model):
    """Compare-and-swap counter for catalogue edits and AI task snapshots."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    revision = models.PositiveIntegerField(default=1)
    initialized = models.BooleanField(default=False)


class LibraryTagAssignment(models.Model):
    """A formal tag attached to one immutable publication, with its provenance."""

    class Source(models.TextChoices):
        HUMAN = "human", "人工添加"
        AI_ACCEPTED = "ai_accepted", "人工采纳 AI 建议"
        LEGACY_HUMAN = "legacy_human", "旧版人工编辑"
        LEGACY_UNKNOWN = "legacy_unknown", "旧标签待核对"

    class Role(models.TextChoices):
        # 这条关联在这道题里扮演什么角色。用途记在关联上，不记在概念身份上。
        PRIMARY = "primary", "主要考查"
        SUPPORTING = "supporting", "辅助用到"
        METHOD = "method", "解法采用"
        FEATURE = "feature", "题面特征"
        # 旧人工关联：保留原样，不替用户编造用途。
        LEGACY = "legacy", "旧关联，作用未细分"

    class ReviewStatus(models.TextChoices):
        CANDIDATE = "candidate", "候选"
        CONFIRMED = "confirmed", "已确认"
        REJECTED = "rejected", "已拒绝"
        ARCHIVED = "archived", "已归档"

    class EvidenceStatus(models.TextChoices):
        # 依据是否还适用于当前题面。人工确认过 ≠ 一定还适用。
        CURRENT = "current", "当前有效"
        NEEDS_RECHECK = "needs_recheck", "需复核"
        # 旧记录没有绑定到具体依据（例如没绑定具体解法）。
        UNBOUND = "unbound", "旧记录未绑定具体依据"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    publication = models.ForeignKey(PublishedQuestion, on_delete=models.CASCADE, related_name="tag_assignments")
    tag = models.ForeignKey(LibraryTag, on_delete=models.PROTECT, related_name="assignments")
    # {"whole": true} or {"subquestions": ["1", "2"]} for method tags.
    scope = models.JSONField(default=dict, blank=True)
    # 规范化后的范围键。SQLite 的 NULL 不参与唯一约束，所以范围必须落成非空串：
    #   whole                      整题
    #   sub:<uuid>                 某个小问
    #   variant:<uuid>             某个解法分支
    #   sub:<uuid>+variant:<uuid>  某小问的某解法
    scope_key = models.CharField(max_length=160, default="whole")
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.LEGACY)
    # 解法采用必须落到具体分支；小问可选。没有可靠拆分时按整题记录，不虚构编号。
    variant = models.ForeignKey(
        "LibrarySolutionVariant", on_delete=models.SET_NULL, null=True, blank=True, related_name="assignments"
    )
    subquestion = models.ForeignKey(
        "LibrarySubquestion", on_delete=models.SET_NULL, null=True, blank=True, related_name="assignments"
    )
    review_status = models.CharField(
        max_length=16, choices=ReviewStatus.choices, default=ReviewStatus.CONFIRMED, db_index=True
    )
    evidence_status = models.CharField(max_length=16, choices=EvidenceStatus.choices, default=EvidenceStatus.CURRENT)
    source = models.CharField(max_length=24, choices=Source.choices, default=Source.HUMAN)
    generation_source = models.CharField(max_length=80, blank=True, default="")
    rationale = models.CharField(max_length=500, blank=True, default="")
    evidence = models.CharField(max_length=500, blank=True, default="")
    needs_review = models.BooleanField(default=False, db_index=True)
    source_snapshot = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at", "id"]
        # 一条关联的唯一性必须包含用途和范围。同一个概念可以在同一道题上
        # 既「主要考查」又「辅助用到」，也可以在两个解法分支里各记一次。
        constraints = [
            models.UniqueConstraint(
                fields=["publication", "tag", "role", "scope_key"], name="unique_publication_tag_role_scope_assignment"
            )
        ]
        indexes = [
            models.Index(fields=["publication", "needs_review"], name="tag_assignment_review"),
            models.Index(fields=["publication", "role", "scope_key"], name="tag_assignment_role_scope"),
            models.Index(fields=["tag", "role", "review_status"], name="tag_assignment_tag_role"),
        ]


class LibraryTagSuggestion(models.Model):
    """A saved AI proposal. Completion never makes its tags formal by itself."""

    class Status(models.TextChoices):
        QUEUED = "queued", "排队中"
        RUNNING = "running", "生成中"
        PENDING = "pending", "待核对"
        ACCEPTED = "accepted", "已采纳"
        REJECTED = "rejected", "已拒绝"
        NO_MATCH = "no_match", "无合适标签"
        REVIEW = "review", "暂不能判断"
        FAILED = "failed", "生成失败"
        STALE = "stale", "依据已变化"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.OneToOneField(
        LibraryJob, null=True, blank=True, on_delete=models.SET_NULL, related_name="tag_suggestion"
    )
    publication = models.ForeignKey(PublishedQuestion, on_delete=models.CASCADE, related_name="tag_suggestions")
    question_fingerprint = models.CharField(max_length=64, blank=True, default="")
    taxonomy_revision = models.PositiveIntegerField(default=1)
    catalogue_snapshot = models.JSONField(default=dict, blank=True)
    assignment_revision = models.PositiveIntegerField(default=0)
    prompt_version = models.CharField(max_length=32, default="tags-v2")
    method_source = models.JSONField(default=dict, blank=True)
    suggestions = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    message = models.CharField(max_length=500, blank=True, default="")
    generation_source = models.CharField(max_length=80, blank=True, default="")
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["publication", "status", "created_at"], name="tag_suggest_pub_state")]


class LibraryTagOperation(models.Model):
    """Auditable tag edit with a guarded inverse for Undo."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    client_request_id = models.UUIDField(unique=True)
    request_hash = models.CharField(max_length=64)
    action = models.CharField(max_length=32)
    publication_ids = models.JSONField(default=list, blank=True)
    before_snapshot = models.JSONField(default=dict, blank=True)
    after_snapshot = models.JSONField(default=dict, blank=True)
    catalogue_revision = models.PositiveIntegerField(default=1)
    undone_at = models.DateTimeField(null=True, blank=True)
    blocked_reason = models.CharField(max_length=300, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]


class LibrarySolution(models.Model):
    """Immutable user-edited solution, independent of the original publication."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    publication = models.ForeignKey(PublishedQuestion, on_delete=models.CASCADE, related_name="solutions")
    parent = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True)
    # 归属的解法分支：原卷解法、教师保存解法、经确认的其他解法分别保留。
    variant = models.ForeignKey(
        "LibrarySolutionVariant", on_delete=models.SET_NULL, null=True, blank=True, related_name="solutions"
    )
    fingerprint = models.CharField(max_length=64)
    answer = models.TextField(blank=True, default="")
    analysis = models.TextField(blank=True, default="")
    figures = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]


class RegionRead(models.Model):
    """框选识读：人在原卷上框出一小块，让读题模型单独读这一块（1.10.2）。

    比如选项 A 被手写的 × 盖住、整题识读把它写错了：框住印刷的那一行，读出来
    的文字由人确认后填进选项 A。网页只排队（网页进程拿不到密钥），后台工作者
    读完写回 ``text``。每道题只保留最近一次。
    """

    class Status(models.TextChoices):
        QUEUED = "queued", "排队中"
        RUNNING = "running", "进行中"
        DONE = "done", "完成"
        FAILED = "failed", "失败"

    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="region_reads")
    page_idx = models.PositiveIntegerField()
    bbox = models.JSONField()
    target = models.CharField(max_length=8)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.QUEUED, db_index=True)
    text = models.TextField(blank=True, default="")
    error = models.CharField(max_length=300, blank=True, default="")
    engine = models.CharField(max_length=80, blank=True, default="")
    # 只读的框选识读定位：排队时的题面基线 + 待人确认的原文片段。
    # 与 text 分开存，定位失败或题面后来变化时仍保留已识读的文字。
    recommendation = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at"]


# ---------------------------------------------------------------------------
# 标签系统重构新增的结构（1.27 起）
#
# 原则：复用并扩展上述模型，不建立第二套相互竞争的正式记录。
# 教材目录（Textbook/Chapter）、数学概念（LibraryTag）、个人专题（LibraryTagGroup）
# 三者彼此独立；正式关联（LibraryTagAssignment）记录用途、范围和依据。
# ---------------------------------------------------------------------------


class LibraryTextbook(models.Model):
    """教材版本。版本未知时如实标注「版本未指定」，不编造教材版本。"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120)
    # 小学／初中／高中／空。空表示不臆断学段。
    stage = models.CharField(max_length=24, blank=True, default="")
    volume = models.CharField(max_length=60, blank=True, default="")
    edition = models.CharField(max_length=120, blank=True, default="")
    # True 表示这是「通用教学目录」，原来不知道版次的旧章节归到这里。
    unspecified = models.BooleanField(default=False)
    position = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["position", "name", "id"]
        constraints = [models.UniqueConstraint(fields=["name", "stage"], name="unique_library_textbook_name_stage")]


class LibraryChapter(models.Model):
    """教材章节。目录关系，不放进概念分类表。"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    textbook = models.ForeignKey(
        LibraryTextbook, on_delete=models.CASCADE, null=True, blank=True, related_name="chapters"
    )
    parent = models.ForeignKey("self", on_delete=models.CASCADE, null=True, blank=True, related_name="children")
    title = models.CharField(max_length=160)
    position = models.PositiveIntegerField(default=0)
    # 规范化键。SQLite 的 NULL 不参与唯一约束，父子链必须落成一个非空串：
    #   generic:<title>                     通用教学目录下的顶层章节
    #   tb:<textbook_id>:<title>            某教材下的顶层章节
    #   ...:<parent_id>:<title>             下一层
    node_key = models.CharField(max_length=400, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["position", "title", "id"]
        indexes = [models.Index(fields=["textbook", "parent"], name="library_chapter_parent")]


class LibraryChapterConcept(models.Model):
    """章节里出现的概念。人工确认过才当作已确认。"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chapter = models.ForeignKey(LibraryChapter, on_delete=models.CASCADE, related_name="concepts")
    tag = models.ForeignKey(LibraryTag, on_delete=models.PROTECT, related_name="chapter_mappings")
    confirmed = models.BooleanField(default=False)
    source = models.CharField(max_length=24, default="human")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["chapter_id", "tag_id"]
        constraints = [models.UniqueConstraint(fields=["chapter", "tag"], name="unique_chapter_concept")]


class LibraryQuestionPlacement(models.Model):
    """题目与教材章节的关联。

    来源位置（这题确实出自那本书那节）和教学对应（这题适合在那节讲）
    分开记，互不冒充 —— 不能由「某章讲了某概念」反推所有该概念的题都出自那章。
    """

    class Relation(models.TextChoices):
        SOURCE = "source", "来源位置"
        TEACHING = "teaching", "教学对应"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    publication = models.ForeignKey(PublishedQuestion, on_delete=models.CASCADE, related_name="placements")
    chapter = models.ForeignKey(LibraryChapter, on_delete=models.CASCADE, related_name="placements")
    relation = models.CharField(max_length=16, choices=Relation.choices, default=Relation.TEACHING)
    confirmed = models.BooleanField(default=False)
    basis = models.CharField(max_length=500, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["publication_id", "chapter_id"]
        constraints = [
            models.UniqueConstraint(fields=["publication", "chapter", "relation"], name="unique_question_placement")
        ]


class LibrarySubquestion(models.Model):
    """小问。稳定 ID 是真的身份，展示编号只是标签。

    编号重排后不能靠字符串「1」继续绑定 —— 展示编号变了，stable_key 不变。
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    publication = models.ForeignKey(PublishedQuestion, on_delete=models.CASCADE, related_name="subquestions")
    stable_key = models.CharField(max_length=64)
    display_label = models.CharField(max_length=32, blank=True, default="")
    position = models.PositiveIntegerField(default=0)
    # 定位信息：在题面文本里的偏移、标题行等，供依据回跳。
    locator = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["position", "id"]
        constraints = [models.UniqueConstraint(fields=["publication", "stable_key"], name="unique_subquestion_key")]


class LibrarySolutionVariant(models.Model):
    """解法分支。

    原卷解法、教师保存解法、经确认的其他解法分别保留。
    界面写「已记录的其他解法」，不写「所有解法」—— 没有任何流程能证明穷尽。
    """

    class Origin(models.TextChoices):
        SOURCE_PAPER = "source_paper", "原卷解法"
        SAVED = "saved", "教师保存解法"
        OTHER_CONFIRMED = "other_confirmed", "已确认的其他解法"

    class Status(models.TextChoices):
        ACTIVE = "active", "当前采用"
        ALTERNATIVE = "alternative", "其他已记录解法"
        SUPERSEDED = "superseded", "已替代"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    publication = models.ForeignKey(PublishedQuestion, on_delete=models.CASCADE, related_name="solution_variants")
    name = models.CharField(max_length=120)
    origin = models.CharField(max_length=20, choices=Origin.choices, default=Origin.SAVED)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    display_order = models.PositiveIntegerField(default=0)
    # 覆盖的小问：空表示整题。解法选择不得改变打印、组卷草稿里固定的答案版本。
    scope = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["display_order", "created_at", "id"]
        constraints = [models.UniqueConstraint(fields=["publication", "name"], name="unique_solution_variant_name")]


class LibraryAssertionEvidence(models.Model):
    """依据：指向题面版本、相关解析版本和关键片段。

    不只存一段与原文脱离的 AI 总结 —— 片段和定位要能回跳到题面或解析。
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    assignment = models.ForeignKey(LibraryTagAssignment, on_delete=models.CASCADE, related_name="evidences")
    publication = models.ForeignKey(PublishedQuestion, on_delete=models.CASCADE, related_name="assertion_evidences")
    solution = models.ForeignKey(
        LibrarySolution, on_delete=models.SET_NULL, null=True, blank=True, related_name="assertion_evidences"
    )
    excerpt = models.TextField(blank=True, default="")
    locator = models.JSONField(default=dict, blank=True)
    snapshot_digest = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [models.Index(fields=["assignment", "id"], name="assertion_evidence_assignment")]


class LibraryCoverage(models.Model):
    """某题、范围、用途维度上的「未整理／已检查到何种程度」及检查依据。

    没有它，「未贴标签」和「确认过没有」就分不开 —— 排除条件也不能把未知当成「确实没有」。
    """

    class Status(models.TextChoices):
        UNREVIEWED = "unreviewed", "未整理"
        CHECKED = "checked", "已检查"
        PARTIAL = "partial", "部分检查"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    publication = models.ForeignKey(PublishedQuestion, on_delete=models.CASCADE, related_name="coverages")
    scope_key = models.CharField(max_length=160, default="whole")
    dimension = models.CharField(max_length=24, default="primary")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.UNREVIEWED)
    basis = models.CharField(max_length=500, blank=True, default="")
    checked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["publication_id", "scope_key", "dimension"]
        constraints = [
            models.UniqueConstraint(fields=["publication", "scope_key", "dimension"], name="unique_coverage_scope")
        ]


class LibrarySavedSearch(models.Model):
    """保存的完整搜索条件。有版本号，不保存固定结果列表。

    来源、题型、关键词、用途、范围、排序都要能原样恢复。
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120)
    query_version = models.PositiveIntegerField(default=1)
    query = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "id"]
        constraints = [models.UniqueConstraint(fields=["name"], name="unique_saved_search_name")]


class LibraryMigrationMap(models.Model):
    """旧实体到新实体的映射、处理类型、原因、批次、原快照引用。

    每一个旧标签、旧关联、旧候选都要能在这里追溯到处理结果。
    """

    class Disposition(models.TextChoices):
        CARRIED = "carried", "保留"
        CONVERTED = "converted", "转换"
        NEEDS_REVIEW = "needs_review", "待人工决定"
        AMBIGUOUS = "ambiguous", "歧义"
        DROPPED = "dropped", "不迁移"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    batch = models.CharField(max_length=64, db_index=True)
    old_kind = models.CharField(max_length=40)
    old_key = models.CharField(max_length=200)
    new_kind = models.CharField(max_length=40, blank=True, default="")
    new_key = models.CharField(max_length=200, blank=True, default="")
    disposition = models.CharField(max_length=20, choices=Disposition.choices, default=Disposition.CARRIED)
    reason = models.CharField(max_length=500, blank=True, default="")
    snapshot = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["batch", "old_kind", "old_key"]
        constraints = [
            models.UniqueConstraint(fields=["batch", "old_kind", "old_key"], name="unique_migration_map_entry")
        ]


class LibraryCollection(models.Model):
    """个人专题：老师准备怎么使用这些题（周五小测、易漏解、一题多解）。

    跟教材目录、数学概念彼此独立 —— 一道题可以同时进两个专题，
    移出专题不删题，专题成员只存引用，不复制题面。
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120)
    description = models.CharField(max_length=300, blank=True, default="")
    color = models.CharField(max_length=16, blank=True, default="")
    position = models.PositiveIntegerField(default=0)
    system_key = models.CharField(max_length=64, null=True, blank=True, default=None, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["position", "name", "id"]
        constraints = [models.UniqueConstraint(fields=["name"], name="unique_library_collection_name")]


class LibraryCollectionMember(models.Model):
    """专题成员。只存引用 —— 题面永远只有一份。"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    collection = models.ForeignKey(LibraryCollection, on_delete=models.CASCADE, related_name="members")
    publication = models.ForeignKey(PublishedQuestion, on_delete=models.CASCADE, related_name="collection_memberships")
    note = models.CharField(max_length=300, blank=True, default="")
    position = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["position", "created_at", "id"]
        constraints = [models.UniqueConstraint(fields=["collection", "publication"], name="unique_collection_member")]
        indexes = [models.Index(fields=["publication", "collection"], name="collection_member_publication")]
