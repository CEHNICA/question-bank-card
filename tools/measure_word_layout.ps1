# Ask Word itself where the figure row actually sits, in millimetres.
# Nothing here guesses: every number comes from the rendered document.
$ErrorActionPreference = 'Stop'
$path = $args[0]

$word = New-Object -ComObject Word.Application
$word.Visible = $false
$word.DisplayAlerts = 0
try {
    $doc = $word.Documents.Open($path, $false, $true)   # ConfirmConversions, ReadOnly

    $mm = 25.4 / 72.0
    $ps = $doc.PageSetup
    $colLeft  = $ps.LeftMargin * $mm
    $colWidth = ($ps.PageWidth - $ps.LeftMargin - $ps.RightMargin) * $mm
    $pageW    = $ps.PageWidth * $mm
    Write-Output ("page {0:N1}mm   text column {1:N1}..{2:N1}mm (width {3:N1}mm)" -f `
        $pageW, $colLeft, ($colLeft + $colWidth), $colWidth)

    # wdHorizontalPositionRelativeToPage = 5
    $i = 0
    foreach ($tbl in $doc.Tables) {
        $i++
        $pics = @($tbl.Range.InlineShapes)
        $left = $tbl.Range.Information(5) * $mm
        Write-Output ""
        Write-Output ("table #{0}  pictures={1}  rowsAlignment={2}  rowsLeftIndent={3:N2}mm" -f `
            $i, $pics.Count, $tbl.Rows.Alignment, $tbl.Rows.LeftIndent * $mm)
        if ($pics.Count -eq 0) { continue }
        Write-Output ("  table left edge  = {0:N2}mm   right edge = {1:N2}mm   width = {2:N2}mm" -f `
            $left, ($left + $tbl.PreferredWidth), $tbl.PreferredWidth * $mm)
        $inside = $left - $colLeft
        Write-Output ("  inside the text column: left gap {0:N2}mm, right gap {1:N2}mm" -f `
            $inside, ($colWidth - $inside - $tbl.PreferredWidth * $mm))
        foreach ($shp in $pics) {
            $sl = $shp.Range.Information(5) * $mm
            Write-Output ("    picture left {0,7:N2}mm  width {1,6:N2}mm  right {2,7:N2}mm   (gap from column left {3:N2}mm)" -f `
                $sl, ($shp.Width * $mm), ($sl + $shp.Width * $mm), ($sl - $colLeft))
        }
    }
    $doc.Close($false)
} finally {
    $word.Quit()
    [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($word)
}
