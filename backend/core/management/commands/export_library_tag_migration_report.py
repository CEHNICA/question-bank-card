"""Export the legacy-tag migration summary without question text or IDs."""

from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from core.library_tag_reports import legacy_tag_migration_markdown, legacy_tag_migration_rows


class Command(BaseCommand):
    help = "导出旧标签的映射、待核对和未能映射清单，不包含题目正文。"

    def add_arguments(self, parser):
        parser.add_argument("--output", required=True, help="报告文件的完整路径")

    def handle(self, *args, **options):
        output = Path(options["output"]).expanduser()
        if output.exists() and output.is_dir():
            raise CommandError("输出路径必须是文件")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(legacy_tag_migration_markdown(), encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(f"已导出 {len(legacy_tag_migration_rows())} 条标签映射记录：{output}"))
