#Requires -Version 5.1
<#
.SYNOPSIS
    一键构建 fr-translator 便携包（Windows 10/11 x64）。

.DESCRIPTION
    流程：
      1. 下载 Python 3.10 embeddable（缓存到 portable\.cache）
      2. 修改 python310._pth，启用 site 并加入 app / site-packages
      3. 安装 get-pip.py，再用 pip --target 安装 requirements-portable.txt
      4. 拷贝 src/fr_translator 到 app/
      5. full 模式运行 scripts/prepare_models.py 预下载模型
      6. 生成 run.bat / config.yaml（可选 run-gui.bat）
      7. 打包 dist/FrenchTranslatorPortable-<Mode>.zip

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\build_portable.ps1 -Mode full

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\build_portable.ps1 -Mode lite -WithGui

.NOTES
    不使用 venv、不使用 PyInstaller/Nuitka；依赖直接装进嵌入式 Python。
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet('lite', 'full')]
    [string]$Mode,

    [switch]$SkipModels,

    [string]$PythonVersion = '3.10.11',

    [string]$RepoRoot = '',

    [string]$OutputDir = '',

    [string]$HfEndpoint = '',

    [string]$ModelsSource = '',

    [switch]$WithGui,

    [ValidateSet('Optimal', 'Fastest', 'NoCompression')]
    [string]$CompressionLevel = 'Optimal'
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false) } catch { }
$env:PYTHONIOENCODING = 'utf-8'
$env:PIP_DISABLE_PIP_VERSION_CHECK = '1'

if (-not $RepoRoot) {
    $RepoRoot = Split-Path -Parent $PSScriptRoot
}
$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
if (-not $OutputDir) {
    $OutputDir = Join-Path $RepoRoot 'dist'
}
$OutputDir = [System.IO.Path]::GetFullPath($OutputDir)

$PortableRoot = Join-Path $RepoRoot 'portable'
$CacheDir = Join-Path $PortableRoot '.cache'
$StagingRoot = Join-Path $PortableRoot 'FrenchTranslatorPortable'
$PyDir = Join-Path $StagingRoot 'python'
$SitePackages = Join-Path $PyDir 'Lib\site-packages'
$AppDir = Join-Path $StagingRoot 'app'
$ModelsDir = Join-Path $StagingRoot 'models'
$ZipPath = Join-Path $OutputDir ("FrenchTranslatorPortable-{0}.zip" -f $Mode)

$EmbedZipName = "python-$PythonVersion-embed-amd64.zip"
$EmbedZip = Join-Path $CacheDir $EmbedZipName
$EmbedUrl = "https://www.python.org/ftp/python/$PythonVersion/$EmbedZipName"
$GetPipPath = Join-Path $CacheDir 'get-pip.py'
$GetPipUrl = 'https://bootstrap.pypa.io/get-pip.py'

# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
function Write-Step {
    param([string]$Message)
    Write-Host ''
    Write-Host ("[{0}] {1}" -f $Mode.ToUpper(), $Message) -ForegroundColor Cyan
}

function Write-Info {
    param([string]$Message)
    Write-Host ("        {0}" -f $Message) -ForegroundColor Gray
}

function Get-DirSize {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return 0 }
    $sum = (Get-ChildItem -LiteralPath $Path -Recurse -File -ErrorAction SilentlyContinue |
        Measure-Object -Property Length -Sum).Sum
    if (-not $sum) { return 0 }
    return [int64]$sum
}

function Format-Size {
    param([int64]$Bytes)
    if ($Bytes -ge 1GB) { return ('{0:N2} GB' -f ($Bytes / 1GB)) }
    if ($Bytes -ge 1MB) { return ('{0:N1} MB' -f ($Bytes / 1MB)) }
    return ('{0:N0} KB' -f ($Bytes / 1KB))
}

function Invoke-External {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$Description
    )
    Write-Info ("{0}：{1} {2}" -f $Description, $FilePath, ($Arguments -join ' '))
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw ("{0} 失败（退出码 {1}）。" -f $Description, $LASTEXITCODE)
    }
}

function Get-RemoteFile {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter(Mandatory = $true)][string]$OutFile
    )
    if ((Test-Path -LiteralPath $OutFile) -and ((Get-Item -LiteralPath $OutFile).Length -gt 1024)) {
        Write-Info ("使用缓存：{0}" -f $OutFile)
        return
    }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $OutFile) | Out-Null
    $tmp = "$OutFile.part"
    if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Force }

    $errors = New-Object System.Collections.Generic.List[string]

    # 1) PowerShell 自带下载
    try {
        Invoke-WebRequest -Uri $Uri -OutFile $tmp -UseBasicParsing -TimeoutSec 600
        Move-Item -LiteralPath $tmp -Destination $OutFile -Force
        Write-Info ("已下载：{0}" -f (Split-Path -Leaf $OutFile))
        return
    }
    catch {
        $errors.Add("Invoke-WebRequest: $($_.Exception.Message)")
    }

    # 2) curl.exe（Windows 10 1803+ 自带）
    if (Get-Command curl.exe -ErrorAction SilentlyContinue) {
        try {
            & curl.exe -L --fail --retry 3 --retry-delay 3 -o $tmp $Uri
            if ($LASTEXITCODE -eq 0 -and (Test-Path -LiteralPath $tmp)) {
                Move-Item -LiteralPath $tmp -Destination $OutFile -Force
                Write-Info ("已下载：{0}" -f (Split-Path -Leaf $OutFile))
                return
            }
            $errors.Add("curl.exe: 退出码 $LASTEXITCODE")
        }
        catch {
            $errors.Add("curl.exe: $($_.Exception.Message)")
        }
    }

    # 3) 系统 Python 兜底（国内网络/代理环境常见 curl 失败）
    $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCmd) {
        try {
            & $pythonCmd.Source -c "import urllib.request,sys; urllib.request.urlretrieve(sys.argv[1], sys.argv[2])" $Uri $tmp
            if ($LASTEXITCODE -eq 0 -and (Test-Path -LiteralPath $tmp)) {
                Move-Item -LiteralPath $tmp -Destination $OutFile -Force
                Write-Info ("已下载：{0}（python 兜底）" -f (Split-Path -Leaf $OutFile))
                return
            }
            $errors.Add("python: 退出码 $LASTEXITCODE")
        }
        catch {
            $errors.Add("python: $($_.Exception.Message)")
        }
    }

    if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue }
    $detail = ($errors -join '；')
    throw ("下载失败：{0}`n{1}`n请检查网络，或手动把文件放到：{2}" -f $Uri, $detail, $OutFile)
}

function Write-Utf8NoBom {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Content
    )
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Content.Replace("`r`n", "`n").Replace("`n", "`r`n"), $encoding)
}

# ---------------------------------------------------------------------------
# 步骤
# ---------------------------------------------------------------------------
function Step-Python {
    Write-Step "准备嵌入式 Python $PythonVersion"
    Get-RemoteFile -Uri $EmbedUrl -OutFile $EmbedZip
    if (Test-Path -LiteralPath $PyDir) { Remove-Item -LiteralPath $PyDir -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $PyDir | Out-Null
    Expand-Archive -LiteralPath $EmbedZip -DestinationPath $PyDir -Force
    if (-not (Test-Path -LiteralPath (Join-Path $PyDir 'python.exe'))) {
        throw "嵌入式 Python 解压失败：$PyDir\python.exe 不存在。"
    }
    Write-Info ("Python 目录：{0}" -f $PyDir)
}

function Step-Pth {
    Write-Step "修改 python310._pth（启用 site / app / site-packages）"
    $pthName = 'python{0}{1}._pth' -f $PythonVersion.Split('.')[0], $PythonVersion.Split('.')[1]
    $pth = Join-Path $PyDir $pthName
    if (-not (Test-Path -LiteralPath $pth)) {
        $candidate = Get-ChildItem -LiteralPath $PyDir -Filter '*._pth' | Select-Object -First 1
        if (-not $candidate) { throw "找不到 $pthName，嵌入式 Python 包结构异常。" }
        $pth = $candidate.FullName
        $pthName = $candidate.Name
    }

    $lines = [System.IO.File]::ReadAllLines($pth)
    $updated = New-Object System.Collections.Generic.List[string]
    $hasSite = $false
    foreach ($line in $lines) {
        $trim = $line.Trim()
        if ($trim -match '^#\s*import\s+site$') {
            $updated.Add('import site')
            $hasSite = $true
        }
        else {
            $updated.Add($line)
        }
    }
    if (-not $hasSite) { $updated.Add('import site') }

    foreach ($entry in @('..\app', 'Lib\site-packages')) {
        $exists = $false
        foreach ($item in $updated) {
            if ($item.Trim() -eq $entry) { $exists = $true; break }
        }
        if (-not $exists) {
            $index = $updated.IndexOf('import site')
            if ($index -ge 0) { $updated.Insert($index, $entry) }
            else { $updated.Add($entry) }
        }
    }

    Set-Content -LiteralPath $pth -Value $updated -Encoding Ascii
    Write-Info ("{0} 内容：" -f $pthName)
    foreach ($line in $updated) { Write-Info ("  " + $line) }
}

function Step-Pip {
    Write-Step "安装 pip 与便携包依赖"
    if (Test-Path -LiteralPath $SitePackages) {
        Get-ChildItem -LiteralPath $SitePackages -Force | Remove-Item -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $SitePackages | Out-Null

    $pythonExe = Join-Path $PyDir 'python.exe'

    # Python embeddable 没有 ensurepip。优先用固定版本的 pip wheel 引导：
    # 可复现，也避免 get-pip.py 每次解析最新 setuptools/wheel 造成长时间卡顿。
    # 构建机没有系统 Python 时，再退回官方 get-pip.py。
    $pipReady = $false
    $systemPython = Get-Command python -ErrorAction SilentlyContinue
    if ($systemPython) {
        try {
            $wheelDir = Join-Path $CacheDir 'pip-wheel'
            New-Item -ItemType Directory -Force -Path $wheelDir | Out-Null
            $wheel = Get-ChildItem -LiteralPath $wheelDir -Filter 'pip-*.whl' |
                Sort-Object LastWriteTime -Descending | Select-Object -First 1
            if (-not $wheel) {
                Invoke-External -FilePath $systemPython.Source -Arguments @(
                    '-m', 'pip', 'download', 'pip==24.2', '--no-deps', '--no-cache-dir', '--dest', $wheelDir
                ) -Description '下载 pip wheel'
                $wheel = Get-ChildItem -LiteralPath $wheelDir -Filter 'pip-*.whl' |
                    Sort-Object LastWriteTime -Descending | Select-Object -First 1
            }
            if (-not $wheel) { throw "pip wheel 不存在：$wheelDir" }

            $bootstrap = Join-Path $CacheDir 'bootstrap_pip.py'
            Write-Utf8NoBom -Path $bootstrap -Content @'
import sys

wheel, target = sys.argv[1], sys.argv[2]
sys.path.insert(0, wheel)
from pip._internal.cli.main import main

raise SystemExit(main([
    "install", "--no-warn-script-location", "--disable-pip-version-check",
    "--target", target, wheel,
]))
'@
            Invoke-External -FilePath $pythonExe -Arguments @(
                $bootstrap, $wheel.FullName, $SitePackages
            ) -Description '用 pip wheel 引导 pip'
            $pipReady = $true
        }
        catch {
            Write-Info ("pip wheel 引导失败：{0}" -f $_.Exception.Message)
        }
    }

    if (-not $pipReady) {
        Write-Info "改用官方 get-pip.py 引导 pip（可能较慢）"
        Get-RemoteFile -Uri $GetPipUrl -OutFile $GetPipPath
        Invoke-External -FilePath $pythonExe -Arguments @(
            $GetPipPath, '--no-warn-script-location', '--disable-pip-version-check'
        ) -Description '安装 pip（get-pip.py）'
    }

    $requirements = Join-Path $RepoRoot 'requirements-portable.txt'
    if (-not (Test-Path -LiteralPath $requirements)) {
        throw "找不到 requirements-portable.txt：$requirements"
    }
    Invoke-External -FilePath $pythonExe -Arguments @(
        '-m', 'pip', 'install',
        '--disable-pip-version-check',
        '--no-warn-script-location',
        '--no-cache-dir',
        '--progress-bar', 'off',
        '--only-binary=:all:',
        '--target', $SitePackages,
        '-r', $requirements
    ) -Description '安装依赖（torch CPU / faster-whisper / transformers 等）'

    if ($WithGui) {
        $guiRequirements = Join-Path $RepoRoot 'requirements-gui.txt'
        if (-not (Test-Path -LiteralPath $guiRequirements)) {
            throw "找不到 requirements-gui.txt：$guiRequirements"
        }
        Invoke-External -FilePath $pythonExe -Arguments @(
            '-m', 'pip', 'install',
            '--disable-pip-version-check',
            '--no-warn-script-location',
            '--no-cache-dir',
            '--progress-bar', 'off',
            '--only-binary=:all:',
            '--target', $SitePackages,
            '-r', $guiRequirements
        ) -Description '安装 GUI 依赖（PyQt6）'
    }

    # 校验依赖能正常 import（DLL 路径问题在这里暴露，而不是打包后才发现）
    $env:PATH = "$SitePackages\torch\lib;$SitePackages\ctranslate2;$env:PATH"
    Invoke-External -FilePath $pythonExe -Arguments @(
        '-c', 'import torch, ctranslate2, faster_whisper, transformers, soundcard, silero_vad, onnxruntime; print(1)'
    ) -Description '校验依赖导入'
}

function Step-App {
    Write-Step "拷贝应用代码到 app/"
    $source = Join-Path $RepoRoot 'src\fr_translator'
    if (-not (Test-Path -LiteralPath $source)) {
        throw "找不到源码目录：$source"
    }
    if (Test-Path -LiteralPath $AppDir) { Remove-Item -LiteralPath $AppDir -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $AppDir | Out-Null
    Copy-Item -LiteralPath $source -Destination $AppDir -Recurse -Force
    Get-ChildItem -LiteralPath $AppDir -Recurse -Directory -Filter '__pycache__' -ErrorAction SilentlyContinue |
        Remove-Item -Recurse -Force
    Write-Info ("已拷贝：{0}" -f $AppDir)
}

function Step-Models {
    if ($SkipModels) {
        Write-Step "跳过模型下载（-SkipModels）"
        return
    }
    if ($Mode -eq 'lite') {
        Write-Step "lite 模式：不预置模型（首次运行联网下载）"
        return
    }

    if ($ModelsSource) {
        Write-Step "从现有目录复制模型"
        $candidate = $ModelsSource
        if (-not [System.IO.Path]::IsPathRooted($candidate)) {
            $candidate = Join-Path $RepoRoot $candidate
        }
        $source = (Resolve-Path -LiteralPath $candidate).Path
        if (-not (Test-Path -LiteralPath $source)) {
            throw "模型源目录不存在：$ModelsSource"
        }
        New-Item -ItemType Directory -Force -Path $ModelsDir | Out-Null
        foreach ($name in @('faster-whisper', 'silero-vad')) {
            $from = Join-Path $source $name
            if (Test-Path -LiteralPath $from) {
                Copy-Item -LiteralPath $from -Destination $ModelsDir -Recurse -Force
                Write-Info ("复制 {0}" -f $from)
            }
        }
        $hubSource = Join-Path $source 'hub'
        if (Test-Path -LiteralPath $hubSource) {
            $hubTarget = Join-Path $ModelsDir 'hub'
            New-Item -ItemType Directory -Force -Path $hubTarget | Out-Null
            Get-ChildItem -LiteralPath $hubSource -Directory -Filter 'Helsinki-NLP--*' |
                ForEach-Object {
                    Copy-Item -LiteralPath $_.FullName -Destination $hubTarget -Recurse -Force
                    Write-Info ("复制 {0}" -f $_.Name)
                }
        }
        return
    }

    Write-Step "下载模型（faster-whisper small / opus-mt-fr-zh / silero-vad）"
    New-Item -ItemType Directory -Force -Path $ModelsDir | Out-Null
    $pythonExe = Join-Path $PyDir 'python.exe'
    $prepare = Join-Path $RepoRoot 'scripts\prepare_models.py'
    if (-not (Test-Path -LiteralPath $prepare)) {
        throw "找不到 scripts\prepare_models.py"
    }

    $oldHfHome = $env:HF_HOME
    $env:HF_HOME = $ModelsDir
    if ($HfEndpoint) { $env:HF_ENDPOINT = $HfEndpoint }
    try {
        $arguments = @($prepare, '--root', $StagingRoot, '--retries', '3')
        if ($HfEndpoint) { $arguments += @('--hf-endpoint', $HfEndpoint) }
        Invoke-External -FilePath $pythonExe -Arguments $arguments -Description '预下载模型'
    }
    finally {
        $env:HF_HOME = $oldHfHome
    }
}

function Test-TranslationModel {
    param([string]$Dir)
    $config = Join-Path $Dir 'config.json'
    if (-not (Test-Path -LiteralPath $config)) { return $false }
    if ((Get-Item -LiteralPath $config).Length -eq 0) { return $false }
    return ((Test-Path -LiteralPath (Join-Path $Dir 'pytorch_model.bin')) -or
        (Test-Path -LiteralPath (Join-Path $Dir 'model.safetensors')))
}

function Test-ModelsReady {
    $whisper = Join-Path $StagingRoot 'models\faster-whisper\small\model.bin'
    if (-not (Test-Path -LiteralPath $whisper)) { return $false }
    $hub = Join-Path $StagingRoot 'models\hub'
    if (-not (Test-Path -LiteralPath $hub)) { return $false }

    $hasDirect = Test-TranslationModel (Join-Path $hub 'Helsinki-NLP--opus-mt-fr-zh')
    $hasFrEn = Test-TranslationModel (Join-Path $hub 'Helsinki-NLP--opus-mt-fr-en')
    $hasEnZh = Test-TranslationModel (Join-Path $hub 'Helsinki-NLP--opus-mt-en-zh')
    return ($hasDirect -or ($hasFrEn -and $hasEnZh))
}

function Step-Launchers {
    Write-Step "生成 run.bat / config.yaml / logs"
    $modelsReady = Test-ModelsReady
    $offline = if ($modelsReady) { '1' } else { '0' }

    $runBat = @'
@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
chcp 65001 >nul
title fr-translator - 法语实时翻译（@EDITION@）

set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "FR_TRANSLATOR_ROOT=%~dp0"
set "FR_TRANSLATOR_EDITION=@EDITION@"
set "HF_HOME=%~dp0models"
set "HUGGINGFACE_HUB_CACHE=%~dp0models\hub"
set "HF_HUB_OFFLINE=@OFFLINE@"
set "TRANSFORMERS_OFFLINE=@OFFLINE@"
set "HF_HUB_DISABLE_TELEMETRY=1"
set "PYTHONPATH=%~dp0app"
set "PATH=%~dp0python;%~dp0python\Lib\site-packages\torch\lib;%PATH%"

if /I "%~1"=="--local" goto :local
goto :main

:main
"%~dp0python\python.exe" -m fr_translator %*
set "EXITCODE=%ERRORLEVEL%"
goto :finish

:local
rem USB 2.0 用法：run.bat --local
rem 把整个便携包按需镜像到本机临时目录，再从本地磁盘启动，避免 U 盘随机读太慢。
if not defined TEMP set "TEMP=%LOCALAPPDATA%\Temp"
set "LOCAL_DIR=%TEMP%\fr-translator-portable"
echo [fr-translator] 正在把便携包镜像到本机临时目录：
echo                %LOCAL_DIR%
if not exist "%LOCAL_DIR%" mkdir "%LOCAL_DIR%"
robocopy "%~dp0." "%LOCAL_DIR%" /E /R:1 /W:1 /NFL /NDL /NJH /NJS /NP /XD "%~dp0logs" "%LOCAL_DIR%\logs" >nul
if errorlevel 8 (
    echo [fr-translator] 镜像失败：请检查磁盘空间、权限或杀毒软件。
    echo.
    pause
    exit /b 1
)
if not exist "%LOCAL_DIR%\logs" mkdir "%LOCAL_DIR%\logs"
set "ARGS="
shift
:collect
if "%~1"=="" goto :runlocal
set "ARGS=%ARGS% %1"
shift
goto :collect
:runlocal
call "%LOCAL_DIR%\run.bat" %ARGS%
exit /b %ERRORLEVEL%

:finish
echo.
if not "%EXITCODE%"=="0" echo [fr-translator] 程序退出码：%EXITCODE%
pause
exit /b %EXITCODE%
'@
    $runBat = $runBat.Replace('@EDITION@', $Mode).Replace('@OFFLINE@', $offline)
    Write-Utf8NoBom -Path (Join-Path $StagingRoot 'run.bat') -Content $runBat

    if ($WithGui) {
        $runGui = @'
@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
chcp 65001 >nul
title fr-translator - 悬浮字幕（@EDITION@）
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "FR_TRANSLATOR_ROOT=%~dp0"
set "FR_TRANSLATOR_EDITION=@EDITION@"
set "HF_HOME=%~dp0models"
set "HUGGINGFACE_HUB_CACHE=%~dp0models\hub"
set "HF_HUB_OFFLINE=@OFFLINE@"
set "TRANSFORMERS_OFFLINE=@OFFLINE@"
set "HF_HUB_DISABLE_TELEMETRY=1"
set "PYTHONPATH=%~dp0app"
set "PATH=%~dp0python;%~dp0python\Lib\site-packages\torch\lib;%PATH%"
"%~dp0python\python.exe" -m fr_translator.gui %*
set "EXITCODE=%ERRORLEVEL%"
echo.
if not "%EXITCODE%"=="0" echo [fr-translator] 程序退出码：%EXITCODE%
pause
exit /b %EXITCODE%
'@
        $runGui = $runGui.Replace('@EDITION@', $Mode).Replace('@OFFLINE@', $offline)
        Write-Utf8NoBom -Path (Join-Path $StagingRoot 'run-gui.bat') -Content $runGui
    }

    $exampleConfig = Join-Path $RepoRoot 'config.example.yaml'
    if (-not (Test-Path -LiteralPath $exampleConfig)) { throw "找不到 config.example.yaml" }
    Copy-Item -LiteralPath $exampleConfig -Destination (Join-Path $StagingRoot 'config.yaml') -Force

    $logsDir = Join-Path $StagingRoot 'logs'
    New-Item -ItemType Directory -Force -Path $logsDir | Out-Null
    Write-Utf8NoBom -Path (Join-Path $logsDir '说明.txt') -Content @'
运行日志写在当前目录（fr-translator.log），最多保留 3 个 5MB 的备份。
如果程序无法启动，请把这里的日志内容和控制台提示一起反馈。
'@

    $readme = Join-Path $RepoRoot 'README.md'
    if (Test-Path -LiteralPath $readme) {
        Copy-Item -LiteralPath $readme -Destination (Join-Path $StagingRoot 'README.md') -Force
    }

    Write-Info ("模型就绪：{0}；离线开关：{1}" -f $modelsReady, $offline)
}

function Step-Zip {
    Write-Step "打包 zip"
    New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
    if (Test-Path -LiteralPath $ZipPath) { Remove-Item -LiteralPath $ZipPath -Force }

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $level = [System.IO.Compression.CompressionLevel]$CompressionLevel
    [System.IO.Compression.ZipFile]::CreateFromDirectory($StagingRoot, $ZipPath, $level, $true)

    $size = (Get-Item -LiteralPath $ZipPath).Length
    Write-Host ''
    Write-Host ("构建完成：{0}" -f $ZipPath) -ForegroundColor Green
    Write-Host ("包大小：{0}" -f (Format-Size $size)) -ForegroundColor Green
    Write-Host ("解压后：{0}" -f (Format-Size (Get-DirSize $StagingRoot))) -ForegroundColor Green
    Write-Host ''
    Write-Host '验证建议：' -ForegroundColor Yellow
    Write-Host '  1. 解压到不含中文/空格的目录（中文路径也能用，但先排除路径因素）'
    Write-Host '  2. 双击 run.bat，首次运行选择输出设备'
    Write-Host '  3. 播放法语视频，确认出现“法语：/ 中文：”两行输出'
    Write-Host '  4. 在源码目录运行 scripts\check_env.py 可做完整环境自检'
    Write-Host ''
}

# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
try {
    Write-Host '============================================================' -ForegroundColor Cyan
    Write-Host ("fr-translator 便携包构建：{0}" -f $Mode) -ForegroundColor Cyan
    Write-Host '============================================================' -ForegroundColor Cyan
    Write-Host ("仓库根目录：{0}" -f $RepoRoot)
    Write-Host ("输出目录  ：{0}" -f $OutputDir)

    New-Item -ItemType Directory -Force -Path $CacheDir | Out-Null
    if (Test-Path -LiteralPath $StagingRoot) {
        Write-Step "清理旧的 staging 目录"
        Remove-Item -LiteralPath $StagingRoot -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $StagingRoot | Out-Null

    Step-Python
    Step-Pth
    Step-Pip
    Step-App
    Step-Models
    Step-Launchers
    Step-Zip

    Write-Info ("staging 目录保留在：{0}" -f $StagingRoot)
    exit 0
}
catch {
    Write-Host ''
    Write-Host ("构建失败：{0}" -f $_.Exception.Message) -ForegroundColor Red
    Write-Host ''
    exit 1
}
