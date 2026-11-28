"""仅执行 XML 解析函数；不连接 COM，不读取真实笔记本。"""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from django.test import SimpleTestCase

from .library_onenote import BUNDLED_BRIDGE_PATH, CREATE_NO_WINDOW


@unittest.skipUnless(os.name == "nt", "需要 Windows PowerShell")
class HierarchyNamespaceTests(SimpleTestCase):
    def test_supported_schema_versions_with_arbitrary_xml_prefix(self):
        source = r"""
param([string]$Bridge)
$ErrorActionPreference = 'Stop'
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($Bridge, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'Bridge syntax error' }
foreach ($name in @('Get-SectionList', 'Get-HierarchyTree')) {
  $fn = $ast.Find({param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name}, $true)
  Invoke-Expression $fn.Extent.Text
}
$out = @()
foreach ($version in @('2013', '2010', '12/2004')) {
  $xml = '<n:Notebooks xmlns:n="http://schemas.microsoft.com/office/onenote/' + $version + '/onenote"><n:Notebook ID="fake-nb" name="Fictional"><n:Section ID="fake-sec" name="Notes" path="C:/fictional/notes.one"/></n:Notebook></n:Notebooks>'
  $tree = @(Get-HierarchyTree $xml)
  $out += @{ notebooks = $tree.Count; sections = $tree[0].sections.Count; id = $tree[0].id }
}
ConvertTo-Json -InputObject $out -Compress
"""
        with tempfile.TemporaryDirectory() as folder:
            script = Path(folder) / "parse-fixture.ps1"
            script.write_text(source, encoding="utf-8")
            result = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(script),
                    str(BUNDLED_BRIDGE_PATH),
                ],
                capture_output=True,
                encoding="utf-8",
                check=True,
                timeout=15,
                creationflags=CREATE_NO_WINDOW,
            )
        self.assertEqual(json.loads(result.stdout), [{"sections": 1, "id": "fake-nb", "notebooks": 1}] * 3)
