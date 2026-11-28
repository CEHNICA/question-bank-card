"""运行真实 PowerShell 分支，替换 COM 为内存对象，不打开本机笔记。"""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import skipUnless

from django.test import SimpleTestCase

BRIDGE = Path(__file__).with_name("onenote_bridge.ps1")
SHELL = shutil.which("powershell") or shutil.which("pwsh")

HARNESS = r"""
param([string]$Bridge, [string]$Fixture)
$ErrorActionPreference = 'Stop'
$f = [IO.File]::ReadAllText($Fixture) | ConvertFrom-Json
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($Bridge, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw ($errors | Out-String) }
$switch = $ast.Find({param($n) $n -is [System.Management.Automation.Language.SwitchStatementAst] -and $n.Condition.Extent.Text -eq '$op'}, $true)
$clause = $switch.Clauses | Where-Object { $_.Item1.Value -eq $f.op }
$body = $clause.Item2.Extent.Text
$body = $body.Substring(1, $body.Length - 2)
$ONE_NS = 'http://schemas.microsoft.com/office/onenote/2013/onenote'
$params = $f.params
$script:Reads = 0
$script:Writes = @()
$script:Nav = @()
function Get-PageContent($one, $id) {
  $index = [Math]::Min($script:Reads, $f.snapshots.Count - 1)
  $script:Reads++
  return [string]$f.snapshots[$index]
}
function Has-VisibleOneNoteFrame { return $true }
function Show-OneNoteForeground { return $true }
$one = New-Object PSObject
$one | Add-Member ScriptMethod UpdatePageContent {param($xml) $script:Writes += $xml}
$one | Add-Member ScriptMethod NavigateTo {
  param($id, $objectId)
  $script:Nav += [string]$objectId
  if ($objectId -and $f.failObjectNavigation) { throw 'fake object jump failed' }
}
$one | Add-Member ScriptMethod GetHyperlinkToObject {
  param($id, $objectId, $link)
  $link.Value = 'onenote:fake-object-' + $objectId
}
"""


@skipUnless(SHELL, "PowerShell required for native bridge fixture")
class OneNoteAttemptBridgeTests(SimpleTestCase):
    def invoke(self, op, params, snapshots=(), **options):
        with tempfile.TemporaryDirectory(prefix="qb-bridge-fixture-") as directory:
            folder = Path(directory)
            fixture = folder / "input.json"
            fixture.write_text(
                json.dumps({"op": op, "params": params, "snapshots": snapshots, **options}), encoding="utf-8"
            )
            # Dot-source the case body so $data remains visible for assertions.
            script = (
                HARNESS
                + r"""
. ([scriptblock]::Create($body))
@{data=$data; writes=@($script:Writes); nav=@($script:Nav); reads=$script:Reads} | ConvertTo-Json -Depth 20 -Compress
"""
            )
            harness = folder / "harness.ps1"
            harness.write_text(script, encoding="utf-8")
            run = subprocess.run(
                [SHELL, "-NoProfile", "-NonInteractive", "-File", str(harness), str(BRIDGE), str(fixture)],
                capture_output=True,
                timeout=15,
            )
            self.assertEqual(run.returncode, 0, run.stderr.decode(errors="replace"))
            return json.loads(run.stdout)

    @staticmethod
    def snapshots(year):
        ns = f"http://schemas.microsoft.com/office/onenote/{year}/onenote"
        old = (
            f'<one:Page xmlns:one="{ns}" ID="{{page}}">'
            '<one:Outline objectID="{old}"><one:OEChildren><one:OE><one:T>original note</one:T>'
            "</one:OE></one:OEChildren></one:Outline>"
            '<one:InkDrawing objectID="{ink}"/>'
        )
        new = (
            '<one:Outline objectID="{new}"><one:OEChildren><one:OE objectID="{oe}">'
            "<one:T><![CDATA[<b>fake attempt marker</b>]]></one:T></one:OE></one:OEChildren></one:Outline>"
            '<one:Outline objectID="{concurrent}"><one:OEChildren><one:OE><one:T>another writer</one:T>'
            "</one:OE></one:OEChildren></one:Outline>"
        )
        return old + "</one:Page>", old + new + "</one:Page>"

    def append(self, year=2013, delay=False, missing=False):
        before, after = self.snapshots(year)
        fragment = (
            '<one:Outline xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote">'
            '<one:Position x="900" y="86"/><one:OEChildren><one:OE>'
            "<one:T>fake attempt marker</one:T></one:OE></one:OEChildren></one:Outline>"
        )
        return self.invoke(
            "append_outline",
            {"pageId": "{page}", "outlineXml": fragment, "marker": "fake attempt marker"},
            [before, before if delay or missing else after, before if missing else after],
        )

    def test_append_preserves_ink_and_matches_new_object_in_all_schemas(self):
        for year in (2007, 2010, 2013):
            with self.subTest(year=year):
                result = self.append(year)
                self.assertTrue(result["data"]["appended"])
                self.assertEqual(result["data"]["outlineId"], "{new}")
                self.assertEqual(result["data"]["focusObjectId"], "{oe}")
                self.assertIn("fake-object-{new}", result["data"]["objectUrl"])
                self.assertEqual(len(result["writes"]), 1)
                self.assertIn('objectID="{ink}"', result["writes"][0])
                self.assertIn('x="900"', result["writes"][0])
                self.assertEqual(result["nav"], [])
                if year != 2013:
                    self.assertNotIn("/2013/onenote", result["writes"][0])

    def test_readback_delay_does_not_repeat_write(self):
        result = self.append(delay=True)
        self.assertEqual(result["data"]["outlineId"], "{new}")
        self.assertEqual(len(result["writes"]), 1)
        self.assertGreater(result["reads"], 2)

    def test_missing_id_does_not_claim_navigation_or_use_old_outline(self):
        result = self.append(missing=True)
        self.assertFalse(result["data"]["appended"])
        self.assertEqual(result["data"]["outlineId"], "")
        self.assertNotIn("navigated", result["data"])
        self.assertEqual(len(result["writes"]), 1)

    def test_failed_object_navigation_reports_page_fallback_truthfully(self):
        result = self.invoke("navigate", {"id": "{page}", "objectId": "{oe}"}, failObjectNavigation=True)
        self.assertFalse(result["data"]["object_navigated"])
        self.assertEqual(result["nav"], ["{oe}", ""])

    def test_successful_object_navigation_is_distinct_from_page_open(self):
        result = self.invoke("navigate", {"id": "{page}", "objectId": "{oe}"})
        self.assertTrue(result["data"]["object_navigated"])
        self.assertEqual(result["nav"], ["{oe}"])
