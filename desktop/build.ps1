# build.ps1 - 一条命令打出 NovaDesk.exe
#
# 干三件事：
#   1. 用 MSVC 编出壳（shell.exe）—— 一个 WebView2 宿主
#   2. 把网页端打包好的 NovaDesk.html 追加到壳的尾部，再补一段页脚
#   3. 拷到分享目录
#
# 关键点：**不需要任何编译器就能"导出"**。往 exe 尾部追加数据，PE 加载器
# 只认头部，多出来的字节它不管，程序照样跑。导出一个小应用 = 拷一份壳 +
# 追加用户的页面 + 补页脚，两秒钟的事，不用重新编译。
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File build.ps1
#   powershell -ExecutionPolicy Bypass -File build.ps1 -SkipWeb   # 只重编壳

[CmdletBinding()]
param(
    [string]$OutDir = 'D:\novadesk-share',
    [switch]$SkipWeb
)

$ErrorActionPreference = 'Stop'

$Here   = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root   = Split-Path -Parent $Here              # NovaDesk\
$Src    = Join-Path $Here 'src'
$Sdk    = Join-Path $Here '_sdk'
$Build  = Join-Path $Here 'build'
$Shell  = Join-Path $Build 'shell.exe'
$Html   = Join-Path $Root 'NovaDesk.html'
$OutExe = Join-Path $OutDir 'NovaDesk.exe'

$Vcvars = 'D:\vista\VC\Auxiliary\Build\vcvars64.bat'
if (-not (Test-Path $Vcvars)) { throw "找不到 vcvars64.bat：$Vcvars" }
if (-not (Test-Path (Join-Path $Sdk 'build\native\include\WebView2.h'))) {
    throw "WebView2 SDK 不在 $Sdk，先按 README 下载 Microsoft.Web.WebView2"
}

New-Item -ItemType Directory -Force -Path $Build | Out-Null

# ---------------------------------------------------------------- 1. 编壳
Write-Host '== [1/4] 准备 MSVC 环境 =='
cmd /c "`"$Vcvars`" >nul 2>&1 && set" | ForEach-Object {
    if ($_ -match '^([^=]+)=(.*)$') {
        Set-Item -Path ("env:" + $matches[1]) -Value $matches[2] -ErrorAction SilentlyContinue
    }
}
if (-not (Get-Command cl.exe -ErrorAction SilentlyContinue)) { throw 'vcvars 之后仍然找不到 cl.exe' }

Write-Host '== [2/4] 编译壳 =='
$sources = @('nv_main.cpp', 'nv_common.cpp', 'nv_bridge.cpp', 'nv_exebridge.cpp', 'nv_net.cpp') |
    ForEach-Object { Join-Path $Src $_ }
foreach ($s in $sources) { if (-not (Test-Path $s)) { throw "缺源文件：$s" } }

& cl.exe /nologo /std:c++17 /EHsc /O2 /MT /utf-8 /W3 `
    /DUNICODE /D_UNICODE /D_WIN32_WINNT=0x0A00 /DWINVER=0x0A00 `
    /I"$(Join-Path $Sdk 'build\native\include')" `
    /Fo"$Build\" /Fe:"$Shell" `
    $sources `
    /link /SUBSYSTEM:WINDOWS /MACHINE:X64 /INCREMENTAL:NO `
    "$(Join-Path $Sdk 'build\native\x64\WebView2LoaderStatic.lib')" `
    ole32.lib oleaut32.lib shell32.lib shlwapi.lib user32.lib gdi32.lib `
    advapi32.lib version.lib

if ($LASTEXITCODE -ne 0) { throw "编译失败（$LASTEXITCODE）" }
if (-not (Test-Path $Shell)) { throw '没产出 shell.exe' }
Write-Host ("   壳: {0}  ({1:N0} 字节)" -f $Shell, (Get-Item $Shell).Length)

# ---------------------------------------------------------------- 2. 网页端
if (-not $SkipWeb) {
    Write-Host '== [3/4] 打包网页端 =='
    Push-Location (Join-Path $Root 'ai')
    try {
        & python build.py
        if ($LASTEXITCODE -ne 0) { throw 'build.py 失败' }
    } finally { Pop-Location }
} else {
    Write-Host '== [3/4] 跳过网页端打包 =='
}
if (-not (Test-Path $Html)) { throw "没有 $Html" }

# ---------------------------------------------------------------- 3. 追加 + 页脚
Write-Host '== [4/4] 把页面追加到壳尾部并写页脚 =='

function Add-Payload {
    param([string]$ShellPath, [string]$HtmlPath, [string]$AppName, [string]$OutPath)

    Copy-Item -LiteralPath $ShellPath -Destination $OutPath -Force

    $htmlBytes = [IO.File]::ReadAllBytes($HtmlPath)
    $nameBytes = [Text.Encoding]::UTF8.GetBytes($AppName)

    $fs = [IO.File]::Open($OutPath, [IO.FileMode]::Append, [IO.FileAccess]::Write)
    try {
        # 追加模式下 Position 就是文件末尾的绝对偏移
        $htmlOffset = [uint64]$fs.Position
        $fs.Write($htmlBytes, 0, $htmlBytes.Length)

        $nameOffset = [uint64]$fs.Position
        $fs.Write($nameBytes, 0, $nameBytes.Length)

        # 页脚 40 字节，必须和 nv.h 里的 NvFooter 逐字段对齐
        $footer = New-Object byte[] 40
        [Text.Encoding]::ASCII.GetBytes('NVDSEXE1').CopyTo($footer, 0)          # magic[8]
        [BitConverter]::GetBytes($htmlOffset).CopyTo($footer, 8)                # htmlOffset
        [BitConverter]::GetBytes([uint64]$htmlBytes.Length).CopyTo($footer, 16) # htmlLen
        [BitConverter]::GetBytes($nameOffset).CopyTo($footer, 24)               # nameOffset
        [BitConverter]::GetBytes([uint32]$nameBytes.Length).CopyTo($footer, 32) # nameLen
        # 36..39 reserved 留 0
        $fs.Write($footer, 0, 40)
    } finally { $fs.Close() }

    return [pscustomobject]@{
        Path = $OutPath
        Size = (Get-Item $OutPath).Length
        Sha  = (Get-FileHash $OutPath -Algorithm SHA256).Hash
    }
}

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

# 上一次跑起来的 exe 会占着文件，不先关掉就会拷失败
#（报的是 "being used by another process"，看着像权限问题，其实只是没关）
Get-Process NovaDesk -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Milliseconds 400

$r = Add-Payload -ShellPath $Shell -HtmlPath $Html -AppName 'NovaDesk' -OutPath $OutExe

# 自检：页脚能不能被读回来（跟壳启动时用的是同一套读法）
$bytes = [IO.File]::ReadAllBytes($OutExe)
$tail = $bytes[($bytes.Length - 40)..($bytes.Length - 1)]
$magic = [Text.Encoding]::ASCII.GetString($tail, 0, 8)
$hOff  = [BitConverter]::ToUInt64($tail, 8)
$hLen  = [BitConverter]::ToUInt64($tail, 16)
$nOff  = [BitConverter]::ToUInt64($tail, 24)
$nLen  = [BitConverter]::ToUInt32($tail, 32)
$nameBack = [Text.Encoding]::UTF8.GetString($bytes, [int]$nOff, [int]$nLen)
$htmlBackOk = $true
if ($hOff + $hLen -ne $nOff) { $htmlBackOk = $false }

Write-Host ''
Write-Host '================ 结果 ================'
Write-Host ("exe    : {0}" -f $r.Path)
Write-Host ("大小   : {0:N0} 字节 ({1:N2} MB)" -f $r.Size, ($r.Size / 1MB))
Write-Host ("SHA256 : {0}" -f $r.Sha)
Write-Host ("页脚   : magic={0} 页面={1} 字节 @{2} 应用名='{3}'" -f $magic, $hLen, $hOff, $nameBack)
Write-Host ("自检   : {0}" -f $(if ($magic -eq 'NVDSEXE1' -and $nameBack -eq 'NovaDesk' -and $htmlBackOk) { '通过' } else { '★ 不通过 ★' }))
Write-Host '====================================='
