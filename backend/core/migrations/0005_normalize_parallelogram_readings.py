"""补齐旧识读记录中的平行四边形符号，并再次清理最终题面。

read_a/read_b/read_c 的 raw 字段是模型原始返回，必须保留；只规范化供界面比较的
结构化 stem/options。若最终题面本身发生变化，则旧审批失效，必须重新人工确认。
"""

from django.db import migrations


def forwards(apps, schema_editor):
    from core.textnorm import fix_reading_symbols, fix_symbols

    Question = apps.get_model("core", "Question")
    for question in Question.objects.all().iterator():
        update_fields = []
        stem = fix_symbols(question.stem)
        options = {key: fix_symbols(value) for key, value in (question.options or {}).items()}
        answer = fix_symbols(question.answer)
        analysis = fix_symbols(question.analysis)
        final_changed = (stem, options, answer, analysis) != (
            question.stem, question.options, question.answer, question.analysis,
        )
        if final_changed:
            question.stem, question.options = stem, options
            question.answer, question.analysis = answer, analysis
            question.approved = False
            question.approved_at = None
            question.approved_content_hash = ""
            update_fields.extend([
                "stem", "options", "answer", "analysis",
                "approved", "approved_at", "approved_content_hash",
            ])

        for field in ("read_a", "read_b", "read_c"):
            old = getattr(question, field)
            fixed = fix_reading_symbols(old)
            if fixed != old:
                setattr(question, field, fixed)
                update_fields.append(field)

        if update_fields:
            question.save(update_fields=update_fields)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0004_approval_content_hash"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
