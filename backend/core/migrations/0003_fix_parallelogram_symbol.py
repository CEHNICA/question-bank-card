"""已有题卡里被写成方框的平行四边形符号（$\\square ABCD$、□ABCD）改回 ▱。

只改题卡的最终文字（题干、选项、答案、解析）；已入库的快照不动（显示时同样会显示成 ▱）。
"""

from django.db import migrations


def forwards(apps, schema_editor):
    from core.textnorm import fix_symbols

    Question = apps.get_model("core", "Question")
    for question in Question.objects.all().iterator():
        stem = fix_symbols(question.stem)
        options = {key: fix_symbols(value) for key, value in (question.options or {}).items()}
        answer = fix_symbols(question.answer)
        analysis = fix_symbols(question.analysis)
        if (stem, options, answer, analysis) != (question.stem, question.options, question.answer, question.analysis):
            question.stem, question.options, question.answer, question.analysis = stem, options, answer, analysis
            question.save(update_fields=["stem", "options", "answer", "analysis"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0002_paper_photos"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
