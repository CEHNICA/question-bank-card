"""Convert DOCX in the worker, not in an HTTP request."""

from pathlib import Path


def convert_docx_to_pdf(source: Path, target: Path) -> None:
    try:
        import pythoncom
        import win32com.client
    except ImportError as exc:
        raise RuntimeError("此电脑没有可用的 Word COM 转换组件") from exc

    pythoncom.CoInitialize()
    word = None
    document = None
    try:
        try:
            word = win32com.client.DispatchEx("Word.Application")
        except Exception as exc:
            raise RuntimeError("处理 DOCX 需要安装并激活桌面版 Microsoft Word；当前电脑无法启动 Word") from exc
        word.Visible = False
        word.DisplayAlerts = 0
        # Macro execution is unnecessary for document conversion.
        word.AutomationSecurity = 3
        document = word.Documents.Open(
            FileName=str(source.resolve()),
            ConfirmConversions=False,
            ReadOnly=True,
            AddToRecentFiles=False,
            Visible=False,
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        document.ExportAsFixedFormat(OutputFileName=str(target.resolve()), ExportFormat=17)
    finally:
        if document is not None:
            document.Close(SaveChanges=False)
        if word is not None:
            word.Quit(SaveChanges=False)
        pythoncom.CoUninitialize()
