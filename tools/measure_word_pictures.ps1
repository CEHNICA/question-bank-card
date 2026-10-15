# Where does every picture in an exported docx actually sit on the page?
#
# Word's Range.Information(5) returns the start of the PARAGRAPH, not of the
# picture, so it under-reports a centred picture by half its slack. This script
# therefore asks Word for the table geometry and works the picture positions
# out from that, then reports the two margins that decide whether a row really
# is centred: how much paper is left of the first picture and of the last.
$ErrorActionPreference = 'Stop'
$path = $args[0]
$mm = 25.4 / 72.0

$word = New-Object -ComObject Word.Application
$word.Visible = $false
$word.DisplayAlerts = 0
try {
    $doc = $word.Documents.Open($path, $false, $true)
    $ps = $doc.PageSetup
    $colLeft = $ps.LeftMargin * $mm
    $colWidth = ($ps.PageWidth - $ps.LeftMargin - $ps.RightMargin) * $mm
    $colRight = $colLeft + $colWidth
    Write-Output ("text column {0:N2} .. {1:N2} mm  (width {2:N2} mm)" -f $colLeft, $colRight, $colWidth)

    $spans = @()

    foreach ($tbl in $doc.Tables) {
        $pics = @($tbl.Range.InlineShapes)
        if ($pics.Count -eq 0) { continue }
        $tableLeft = $tbl.Range.Information(5) * $mm
        $widths = @()
        for ($i = 1; $i -le $tbl.Columns.Count; $i++) { $widths += $tbl.Columns.Item($i).Width * $mm }
        Write-Output ("  table: left {0:N2}mm, {1} columns {2:N1} mm each, total {3:N2}mm, rowsAlign={4}" -f `
            $tableLeft, $widths.Count, $widths[0], ($widths | Measure-Object -Sum).Sum, $tbl.Rows.Alignment)
        foreach ($shp in $pics) {
            $r = $shp.Range
            $index = $r.Cells.Item(1).ColumnIndex
            $before = 0.0
            for ($k = 0; $k -lt $index - 1; $k++) { $before += $widths[$k] }
            $picture = $shp.Width * $mm
            $slack = ($widths[$index - 1] - $picture) / 2
            $left = $tableLeft + $before + $slack
            $spans += [pscustomobject]@{ left = $left; right = $left + $picture; width = $picture }
        }
    }

    # Pictures outside a table follow their paragraph's alignment.
    foreach ($shp in $doc.InlineShapes) {
        $r = $shp.Range
        if ($r.Information(12)) { continue }
        $picture = $shp.Width * $mm
        $alignment = [int]$r.Paragraphs.Item(1).Alignment
        $left = switch ($alignment) {
            1 { $colLeft + ($colWidth - $picture) / 2 }   # centre
            2 { $colRight - $picture }                    # right
            default { $colLeft }                          # left (the default)
        }
        $spans += [pscustomobject]@{ left = $left; right = $left + $picture; width = $picture }
    }

    Write-Output ""
    $i = 0
    foreach ($span in $spans) {
        $i++
        Write-Output ("picture {0}: left {1,7:N2}  width {2,6:N2}  right {3,7:N2}   gapL {4,6:N2}  gapR {5,6:N2}  skew {6,6:N2}" -f `
            $i, $span.left, $span.width, $span.right,
            ($span.left - $colLeft), ($colRight - $span.right),
            (($span.left - $colLeft) - ($colRight - $span.right)))
    }

    $lo = ($spans | Measure-Object -Property left -Minimum).Minimum
    $hi = ($spans | Measure-Object -Property right -Maximum).Maximum
    $skew = ($lo - $colLeft) - ($colRight - $hi)
    Write-Output ""
    Write-Output ("whole document spans {0:N2} .. {1:N2} mm, {2:N2} mm of paper" -f $lo, $hi, ($hi - $lo))
    Write-Output ("centred as a block: skew {0:N2} mm  ->  {1}" -f $skew,
        $(if ([math]::Abs($skew) -le 0.5) { 'CENTRED' } else { 'OFF CENTRE' }))
    $doc.Close($false)
} finally {
    $word.Quit()
    [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($word)
}
