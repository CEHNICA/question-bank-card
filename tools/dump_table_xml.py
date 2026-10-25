"""Dump the real tblPr / sectPr of an exported docx so Word's layout can be read back."""

import re
import sys
import zipfile

path = sys.argv[1]
with zipfile.ZipFile(path) as z:
    xml = z.read("word/document.xml").decode("utf-8")

print("== section page margins ==")
for m in re.finditer(r"<w:pgMar[^/]*/>", xml):
    print(" ", m.group(0))

print("\n== every tblPr, in document order ==")
for i, m in enumerate(re.finditer(r"<w:tblPr>(.*?)</w:tblPr>", xml, re.S)):
    body = m.group(1)
    has_pic = "drawing" in xml[m.end() : xml.find("</w:tbl>", m.end())]
    print(f"\n--- tblPr #{i}  (contains picture: {has_pic}) ---")
    print(" ", body.replace("><", ">\n  <")[:1400])
