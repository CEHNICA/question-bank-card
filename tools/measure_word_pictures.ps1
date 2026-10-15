# Where does every picture in the exported document actually sit, in millimetres?
$ErrorActionPreference = 'Stop'
$path = $args[0]

$word = New-Object -ComObject Word.Application
$word.Visible = $false
$word.DisplayAlerts = 0
try {
    $doc = $word.Documents.Open($path, $false, $true)
    $mm = 25.4 / 72.0
    $ps = $doc.PageSetup
    $colLeft  = $ps.LeftMargin * $mm
    $colWidth = ($ps.PageWidth - $ps.LeftMargin - $ps.RightMargin) * $mm
    $colRight = $colLeft + $colWidth
    Write-Output ("text column {0:N2} .. {1:N2} mm" -f $colLeft, $colRight)

    $n = 0
    foreach ($shp in $doc.InlineShapes) {
        $n++
        $r = $shp.Range
        $left = $r.Information(5) * $mm
        $w = $shp.Width * $mm
        $inTable = $r.Information(12)      # wdWithInTable
        $paraAlign = $r.Paragraphs.Item(1).Alignment   # 0 left 1 centre 2 right
        $alignName = @{0='LEFT';1='CENTRE';2='RIGHT';3='JUSTIFY'}[[int]$paraAlign]
        $leftGap  = $left - $colLeft
        $rightGap = $colRight - ($left + $w)
        Write-Output ("picture {0}: left {1,7:N2}  width {2,6:N2}  right {3,7:N2}   inTable={4,-5} paraAlign={5,-6}  gapL {6,6:N2}  gapR {7,6:N2}  skew {8,6:N2}" -f `
            $n, $left, $w, ($left + $w), $inTable, $alignName, $leftGap, $rightGap, ($leftGap - $rightGap))
    }
    $doc.Close($false)
} finally {
    $word.Quit()
    [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($word)
}
