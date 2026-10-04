# Rasterise CJK glyphs to 16x16 1-bit bitmaps using GDI+.
#
# IMPORTANT: this file must stay pure ASCII. Windows PowerShell 5.1 reads
# .ps1 files as ANSI, so any Chinese literal here would be mangled before it
# ever runs. All Chinese comes in via -CharsFile (read as UTF-8), and the
# output keys are Unicode code points (numbers), not characters.
#
# Anti-aliasing is switched off on purpose: the NovaDesk framebuffer is
# 1 bit per pixel (draw / don't draw), so we need crisp bilevel glyphs.
param(
    [Parameter(Mandatory=$true)][string]$CharsFile,
    [Parameter(Mandatory=$true)][string]$Out,
    [int]$FontSize = 16,
    [string]$FontName = "SimSun",
    [int]$Cell = 16,
    [int]$OffsetX = 0,
    [int]$OffsetY = 0,
    [int]$Threshold = 127
)

Add-Type -AssemblyName System.Drawing

$text = [System.IO.File]::ReadAllText($CharsFile, [System.Text.UTF8Encoding]::new($false))
$chars = @()
foreach ($c in $text.ToCharArray()) {
    if ([int]$c -gt 127 -and -not $chars.Contains($c)) { $chars += $c }
}
Write-Host "unique non-ascii chars: $($chars.Count)"

$bmp = New-Object System.Drawing.Bitmap($Cell, $Cell)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::SingleBitPerPixelGridFit
$g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::None
$g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::NearestNeighbor
$g.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::Half

$font = New-Object System.Drawing.Font($FontName, $FontSize, [System.Drawing.FontStyle]::Regular, [System.Drawing.GraphicsUnit]::Pixel)
$brush = [System.Drawing.Brushes]::White

$sb = New-Object System.Text.StringBuilder
[void]$sb.Append("{")
[void]$sb.Append('"_meta":{"font":"' + $FontName + '","size":' + $FontSize + ',"cell":' + $Cell + ',"offsetX":' + $OffsetX + ',"offsetY":' + $OffsetY + '},')
[void]$sb.Append('"glyphs":[')

$first = $true
foreach ($ch in $chars) {
    $g.Clear([System.Drawing.Color]::Black)
    $g.DrawString([string]$ch, $font, $brush, [float]$OffsetX, [float]$OffsetY)

    if (-not $first) { [void]$sb.Append(",") }
    $first = $false

    [void]$sb.Append('{"cp":' + [int]$ch + ',"rows":[')
    for ($y = 0; $y -lt $Cell; $y++) {
        # NOTE: PowerShell's -shl works on Int32, so a shift >= 32 silently
        # wraps (the shift count is masked to 5 bits). Cell sizes above 31
        # produced scrambled bitmaps until this was cast to Int64.
        $bits = [long]0
        for ($x = 0; $x -lt $Cell; $x++) {
            $c = $bmp.GetPixel($x, $y)
            if ($c.R -gt $Threshold) { $bits = $bits -bor ([long]1 -shl ($Cell - 1 - $x)) }
        }
        if ($y -gt 0) { [void]$sb.Append(",") }
        [void]$sb.Append([long]$bits)
    }
    [void]$sb.Append("]}")
}
[void]$sb.Append("]}")

[System.IO.File]::WriteAllText($Out, $sb.ToString(), [System.Text.UTF8Encoding]::new($false))
Write-Host "wrote $Out  glyphs=$($chars.Count)  font=$FontName@$FontSize"

$g.Dispose(); $bmp.Dispose(); $font.Dispose()
