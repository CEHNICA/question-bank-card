import uuid

from django.db import models


class Paper(models.Model):
    """一份上传的试卷。status 走完 queued → parsing → reading → ready。"""

    class Status(models.TextChoices):
        QUEUED = "queued", "排队中"
        PARSING = "parsing", "MinerU 解析中"
        SEGMENTING = "segmenting", "切题中"
        READING = "reading", "AI 读题中"
        READY = "ready", "待你终审"
        FAILED = "failed", "失败"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    filename = models.CharField(max_length=255)
    # 人可修改的任务显示名；filename 始终保留最初上传的文件名，方便追溯原卷。
    task_name = models.CharField(max_length=255, blank=True, default="")
    kind = models.CharField(max_length=8)                   # pdf | image | docx
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
    # 手机照片（一张或几张合成一份卷）：
    # {"files": [{"name", "file", "taken", "straightened"}]（选择顺序）, "enhance": 是否做扫描件效果,
    #  "order": 当前第 i 页是 files[order[i]], "mineru_order": 交给 MinerU 时的页序,
    #  "check": 需要人确认页序的原因, "notes": 给人看的处理说明, "manual": 人工调整过页序}
    photos = models.JSONField(default=dict, blank=True)
    imported_from = models.CharField(max_length=80, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    @property
    def display_name(self) -> str:
        return self.task_name.strip() or self.filename


class Block(models.Model):
    """MinerU 的原始内容块。只用于找题号、大题标题和候选配图，不作为归题单位。"""

    paper = models.ForeignKey(Paper, on_delete=models.CASCADE, related_name="blocks")
    seq = models.PositiveIntegerField()
    type = models.CharField(max_length=40)
    page_idx = models.PositiveIntegerField()
    bbox = models.JSONField(null=True, blank=True)
    text = models.TextField(blank=True)

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

    paper = models.ForeignKey(Paper, on_delete=models.CASCADE, related_name="questions")
    number = models.PositiveIntegerField()
    section = models.CharField(max_length=120, blank=True)
    question_type = models.CharField(max_length=24, default="unknown")
    regions = models.JSONField(default=list)             # [{page_idx, bbox}]，按阅读顺序
    regions_auto = models.JSONField(default=list)
    start_source = models.CharField(max_length=16, default="mineru")
    figure_candidates = models.JSONField(default=list)   # [{label, page_idx, bbox, seq}]
    figures = models.JSONField(default=list)             # [{slot, page_idx, bbox, source}]
    # 识读记录：甲（MiniMax）、乙（第二位读者）、丙（仅分歧时的裁决）。
    read_a = models.JSONField(default=dict)
    read_b = models.JSONField(default=dict)
    read_c = models.JSONField(default=dict)
    stem = models.TextField(blank=True)
    options = models.JSONField(default=dict)
    text_source = models.CharField(max_length=16, blank=True)   # agree | majority | arbiter | single | human
    state = models.CharField(max_length=10, choices=State.choices, default=State.WAITING)
    flags = models.JSONField(default=list)               # 请看一眼的具体原因
    error = models.CharField(max_length=300, blank=True)
    edited = models.BooleanField(default=False)
    approved = models.BooleanField(default=False)
    approved_at = models.DateTimeField(null=True, blank=True)
    # 审批必须绑定到当时实际看过的题面、配图和原卷范围。
    # 只看 approved 布尔值会让“通过后再改字/换图”绕过复核。
    approved_content_hash = models.CharField(max_length=64, blank=True, default="")
    answer = models.TextField(blank=True, default="")
    analysis = models.TextField(blank=True, default="")
    reread_requested = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["number", "id"]


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
    published_at = models.DateTimeField(auto_now_add=True)
    withdrawn_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-published_at"]
        constraints = [models.UniqueConstraint(fields=["question", "version"], name="unique_question_version")]
