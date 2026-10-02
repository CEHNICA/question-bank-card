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
    kind = models.CharField(max_length=8)                   # pdf | image | docx
    # 资料结构与文件格式分开记录：同样是 PDF，既可能是一份试卷，也可能是一本书。
    material_type = models.CharField(
        max_length=8, choices=MaterialType.choices, default=MaterialType.EXAM,
    )
    sha256 = models.CharField(max_length=64, db_index=True)
    source_path = models.CharField(max_length=500)           # 上传的原文件
    render_path = models.CharField(max_length=500, blank=True)  # 用于渲染页面的 PDF/图片（docx 转成的 PDF）
    zip_path = models.CharField(max_length=500, blank=True)
    pages = models.JSONField(default=list)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED)
    progress = models.PositiveIntegerField(default=0)
    total = models.PositiveIntegerField(default=0)
    error = models.TextField(blank=True)
    notes = models.JSONField(default=list)                   # 切题过程中的说明（如"第 5 题由 AI 定位"）
    # 跨文件格式的资料结构判断：suggested_groups、signals 以及人工 confirmed 结果。
    # 不放进 photos，因为 PDF 和书籍同样需要这份可追溯记录。
    structure = models.JSONField(default=dict, blank=True)
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
                condition=models.Q(
                    source_page_start__gte=1,
                    source_page_end__gte=models.F("source_page_start"),
                ),
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
        GREEN = "green", "两次识读一致"
        YELLOW = "yellow", "请看一眼"
        RED = "red", "识读失败"

    class SourceKind(models.TextChoices):
        EXAMPLE = "example", "例题"
        EXERCISE = "exercise", "练习"
        MANUAL = "manual", "人工补录"
        UNKNOWN = "unknown", "未分类"

    paper = models.ForeignKey(Paper, on_delete=models.CASCADE, related_name="questions")
    # 题号只在题组中用于展示；真正稳定的题源身份与题号、排序和任务改名无关。
    group = models.ForeignKey(
        QuestionGroup, on_delete=models.SET_NULL, null=True, blank=True, related_name="questions",
    )
    source_key = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    number = models.PositiveIntegerField()
    section = models.CharField(max_length=120, blank=True)
    question_type = models.CharField(max_length=24, default="unknown")
    regions = models.JSONField(default=list)             # [{page_idx, bbox}]，按阅读顺序
    regions_auto = models.JSONField(default=list)
    start_source = models.CharField(max_length=16, default="mineru")
    # 题号会在书籍中反复从 1 开始，不能单独充当身份。来源类型与 MinerU
    # 起始块序号共同提供一次解析内稳定、无需模型的匹配锚点。
    source_kind = models.CharField(
        max_length=16, choices=SourceKind.choices, default=SourceKind.UNKNOWN,
    )
    source_anchor_seq = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    figure_candidates = models.JSONField(default=list)   # [{label, page_idx, bbox, seq}]
    figures = models.JSONField(default=list)             # [{slot, page_idx, bbox, source}]
    # 零额外识别调用的配图核查结果。自动判断和人工“确认无图”都保留理由，便于撤销与追溯。
    figure_review = models.JSONField(default=dict)
    # 识读记录：甲（MiniMax）、乙（第二位读者）、丙（仅分歧时的裁决）。
    read_a = models.JSONField(default=dict)
    read_b = models.JSONField(default=dict)
    read_c = models.JSONField(default=dict)
    stem = models.TextField(blank=True)
    options = models.JSONField(default=dict)
    text_source = models.CharField(max_length=16, blank=True)   # agree | witness | majority | arbiter | single | human
    state = models.CharField(max_length=10, choices=State.choices, default=State.WAITING)
    flags = models.JSONField(default=list)               # 请看一眼的具体原因
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
        "QuestionDeletionBatch", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="questions",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ActiveQuestionManager()
    # Cascades, migrations and the recycle-bin API need an unfiltered manager.
    all_objects = models.Manager()

    class Meta:
        ordering = ["number", "id"]
        default_manager_name = "objects"
        base_manager_name = "all_objects"
        indexes = [
            models.Index(fields=["paper", "group", "number"], name="question_source_lookup"),
            models.Index(
                fields=["paper", "group", "source_kind", "source_anchor_seq"],
                name="question_source_anchor",
            ),
        ]


class QuestionDeletionBatch(models.Model):
    """One user deletion gesture; it is the unit used by the Undo action."""

    class Origin(models.TextChoices):
        USER = "user", "人工删除"
        RESEGMENT = "resegment", "重新切题自动排除"

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


class PublishedQuestion(models.Model):
    """正式题库中的一条不可变快照。修改后再入库会生成新版本，旧版本标记为已替代。"""

    class Status(models.TextChoices):
        PUBLISHED = "published", "Published"
        SUPERSEDED = "superseded", "Superseded"
        WITHDRAWN = "withdrawn", "Withdrawn"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    question = models.ForeignKey(Question, on_delete=models.SET_NULL, null=True, blank=True, related_name="publications")
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
    # 改了不出新版本、不用重审；再入库出新版本时原样带过去。
    extras = models.JSONField(default=dict, blank=True)
    # “|集合间的基本关系|全称量词与存在量词|”：按知识点筛选用。
    tags_text = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-published_at"]
        constraints = [models.UniqueConstraint(fields=["question", "version"], name="unique_question_version")]


class LibraryJob(models.Model):
    """题库里排队给读题模型做的事：补知识点、做 AI 参考答案。

    网页进程拿不到密钥，所以网页只排队，后台工作者调用模型、写回结果。
    """

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
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.QUEUED, db_index=True)
    error = models.CharField(max_length=300, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at"]


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
