# Builds the downloadable Windows app into dist\: Starholm-Setup-<version>.exe, a per-user installer that needs no
# admin rights, and Starholm-<version>-windows-x64.zip, the same app as a folder to unpack anywhere.
#
#   pwsh -File ops\build-windows-app.ps1
#
# The Windows counterpart of ops/build-mac-app.sh. The app carries everything it runs: a relocatable Python
# (python-build-standalone) with the packages in requirements-windows.lock, the app's code from the public file set
# (ops/make_public_tree.py, so only what is committed in HEAD and only what the public tree holds), and pdftotext.exe
# and pdfinfo.exe (Poppler, built here, static, with FreeType and zlib). Starholm.exe (ops/windows-launcher.c) runs
# the app. User data never goes in the install folder: the launcher sends it to %LOCALAPPDATA%\Starholm\ (or
# %STARHOLM_DATA_DIR%). Windows 10 22H2 or newer, x64.
#
# What the build PC needs: Windows x64, Visual Studio 2022 or its Build Tools with the C++ workload (cl, rc, CMake and
# Ninja come with it), git, and the internet for the first build. Every download is pinned by SHA-256 and kept in
# dist\.cache\, so later builds reuse it; Poppler is compiled once into dist\.cache\ too. The installer is made with
# Inno Setup, downloaded (pinned) into dist\.cache\ as well. GitHub Actions runs this on windows-latest
# (.github/workflows/windows.yml).
#
# Signing: none yet. Windows SmartScreen warns about an unsigned download until it is signed.
# Settings: STARHOLM_VERSION is the version in the installer and the file names. It defaults to APP_VERSION in
# upgrade/appupdate.py, the one place the version lives.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 3.0
$ProgressPreference = 'SilentlyContinue'  # Invoke-WebRequest is many times slower with its progress bar

$CodeVersion = (Select-String -Path (Join-Path $PSScriptRoot '..\upgrade\appupdate.py') -Pattern '^APP_VERSION = "([0-9.]+)"').Matches[0].Groups[1].Value
$Version = if ($env:STARHOLM_VERSION) { $env:STARHOLM_VERSION } else { $CodeVersion }
if (-not $Version) { throw 'build-windows-app: no APP_VERSION in upgrade/appupdate.py' }
$Name = 'Starholm'
$PyVer = '3.13'
$PyDll = 'python313'

$PBS_URL = 'https://github.com/astral-sh/python-build-standalone/releases/download/20261110/cpython-3.13.18%2B20261110-x86_64-pc-windows-msvc-install_only_stripped.tar.gz'
$PBS_SHA256 = '2e0fed09d48989f64c9305da9ff2d37a41c0d13ab2aa6283405b2b620dfbf805'
$POPPLER_VER = '26.11.0'
$POPPLER_URL = "https://poppler.freedesktop.org/poppler-$POPPLER_VER.tar.xz"
$POPPLER_SHA256 = '41c32680160addf5a8dd7a830e4869c01e9f6bee1abb91e44fabd1ba9d7828fb'
$FREETYPE_VER = '2.14.3'
$FREETYPE_URL = "https://download.savannah.gnu.org/releases/freetype/freetype-$FREETYPE_VER.tar.xz"
$FREETYPE_SHA256 = '36bc4f1cc413335368ee656c42afca65c5a3987e8768cc28cf11ba775e785a5f'
$ZLIB_VER = '1.4'
$ZLIB_URL = "https://github.com/madler/zlib/releases/download/v$ZLIB_VER/zlib-$ZLIB_VER.tar.xz"
$ZLIB_SHA256 = '4f11c512053d7bab8d9e9b89bde3575add3e4c50a383b42ef6cb837035fc5a7c'
$INNO_VER = '6.7.4'
$INNO_URL = "https://github.com/jrsoftware/issrc/releases/download/is-6_7_4/innosetup-$INNO_VER.exe"
$INNO_SHA256 = 'a549929319f9120716677c685535ca4671e4b937b5fdc87e2585f32d8eb30e8c'

$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Dist = Join-Path $Root 'dist'
$Cache = Join-Path $Dist '.cache'
$Dl = Join-Path $Cache 'dl'
$IconPng = Join-Path $Root 'ops\icon\starholm-1024.png'
$Jobs = 4
$Tar = Join-Path $env:SystemRoot 'System32\tar.exe'  # Windows' own bsdtar (Git's GNU tar reads C:\ as a host)

function Say($text) { Write-Host "`n== $text" }
function Fail($text) { Write-Error "build-windows-app: $text"; exit 1 }
function Run {  # Run <program> <args...>: run it and stop the build when it fails. No param block, on purpose: a
  # plain function passes every argument on as it is written (-I, -c, --build), where an advanced one would read them
  # as its own parameters.
  $program = $args[0]
  $rest = @($args | Select-Object -Skip 1)
  & $program @rest
  if ($LASTEXITCODE -ne 0) { Fail "$program failed (exit $LASTEXITCODE): $($rest -join ' ')" }
}
function Sha256($path) { (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant() }
function Fetch($url, $sum, $file) {  # download url into dist\.cache\dl\file unless it is there with this SHA-256
  New-Item -ItemType Directory -Force -Path $Dl | Out-Null
  $out = Join-Path $Dl $file
  if (-not (Test-Path -LiteralPath $out) -or (Sha256 $out) -ne $sum) {
    Write-Host "Downloading $url"
    Invoke-WebRequest -Uri $url -OutFile "$out.part" -MaximumRedirection 5 -UseBasicParsing
    Move-Item -Force -LiteralPath "$out.part" -Destination $out
  }
  if ((Sha256 $out) -ne $sum) { Remove-Item -Force -LiteralPath $out; Fail "$file does not match its SHA-256." }
  return $out
}

if (-not [Environment]::Is64BitOperatingSystem -or $env:PROCESSOR_ARCHITECTURE -ne 'AMD64') { Fail 'build on Windows x64.' }
if (-not (Test-Path -LiteralPath $IconPng)) { Fail "missing $IconPng" }
if (git -C $Root status --porcelain --untracked-files=no) {
  Write-Warning 'The app is built from HEAD; uncommitted changes are not in it.'
}

# ---------- 0. Visual Studio's C++ tools: cl, rc, link, CMake and Ninja on PATH ----------
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
if (-not (Test-Path -LiteralPath $vswhere)) { Fail 'install Visual Studio 2022 or its Build Tools with the C++ workload.' }
$vs = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $vs) { Fail 'Visual Studio has no C++ x64 tools.' }
$vcvars = Join-Path $vs 'VC\Auxiliary\Build\vcvars64.bat'
cmd /c "`"$vcvars`" >nul && set" | ForEach-Object {
  if ($_ -match '^([^=]+)=(.*)$') { [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2]) }
}
foreach ($tool in 'cl', 'rc', 'link', 'cmake', 'ninja') {
  if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { Fail "$tool isn't on PATH after vcvars64.bat." }
}

# ---------- 1. Python ----------
Say "Python $PyVer (python-build-standalone)"
$pbsArchive = Fetch $PBS_URL $PBS_SHA256 'python-windows.tar.gz'
$Pbs = Join-Path $Cache "pbs-$PBS_SHA256"
if (-not (Test-Path -LiteralPath (Join-Path $Pbs 'python\python.exe'))) {
  if (Test-Path -LiteralPath $Pbs) { Remove-Item -Recurse -Force -LiteralPath $Pbs }
  New-Item -ItemType Directory -Force -Path $Pbs | Out-Null
  Run $Tar -xzf $pbsArchive -C $Pbs
}
$BuildPy = Join-Path $Pbs 'python\python.exe'  # runs the build's own Python steps; the app gets a fresh copy

# ---------- 2. Poppler (pdftotext.exe, pdfinfo.exe), static, with FreeType and zlib ----------
$PopplerOut = Join-Path $Cache "poppler-$POPPLER_VER-ft$FREETYPE_VER-z$ZLIB_VER-win64"
if (-not (Test-Path -LiteralPath (Join-Path $PopplerOut 'bin\pdftotext.exe')) -or
    -not (Test-Path -LiteralPath (Join-Path $PopplerOut 'bin\pdfinfo.exe'))) {
  Say "Poppler $POPPLER_VER with FreeType $FREETYPE_VER and zlib $ZLIB_VER (compiled once, kept in dist\.cache)"
  $popplerArchive = Fetch $POPPLER_URL $POPPLER_SHA256 "poppler-$POPPLER_VER.tar.xz"
  $freetypeArchive = Fetch $FREETYPE_URL $FREETYPE_SHA256 "freetype-$FREETYPE_VER.tar.xz"
  $zlibArchive = Fetch $ZLIB_URL $ZLIB_SHA256 "zlib-$ZLIB_VER.tar.xz"
  $Work = Join-Path $Cache 'poppler-build'
  foreach ($d in $Work, $PopplerOut) { if (Test-Path -LiteralPath $d) { Remove-Item -Recurse -Force -LiteralPath $d } }
  New-Item -ItemType Directory -Force -Path $Work | Out-Null
  # Python's tarfile unpacks .tar.xz on every Windows 10 (its own tar.exe may not read xz).
  foreach ($a in $zlibArchive, $freetypeArchive, $popplerArchive) {
    Run $BuildPy -I -c "import sys, tarfile; tarfile.open(sys.argv[1]).extractall(sys.argv[2], filter='data')" $a $Work
  }
  # One line of Poppler 26.11 that Visual Studio 18's compiler refuses: Object's constructor takes a
  # std::unique_ptr<Array> by value, and on Windows a function destroys its own by-value arguments, so the inline
  # constructor needs ~Array where Array is only declared ("can't delete an incomplete type"). Taking it as && like
  # the Dict constructor beside it is the same for every caller (a unique_ptr can only be moved in). The script
  # stops if the line isn't there exactly once, as after a Poppler upgrade.
  $patch = "import sys; p = sys.argv[1]; s = open(p, encoding='utf-8').read(); " +
           "old = 'explicit Object(std::unique_ptr<Array> arrayA)'; " +
           "sys.exit('Poppler patch: the line is not there exactly once') if s.count(old) != 1 else None; " +
           "open(p, 'w', encoding='utf-8', newline='').write(s.replace(old, 'explicit Object(std::unique_ptr<Array> &&arrayA)'))"
  Run $BuildPy -I -c $patch (Join-Path $Work "poppler-$POPPLER_VER\poppler\Object.h")
  $Prefix = Join-Path $Work 'prefix'
  # The static C runtime (/MT), so the two programs need no Visual C++ runtime DLL.
  $Common = @('-G', 'Ninja', '-DCMAKE_BUILD_TYPE=Release', '-DBUILD_SHARED_LIBS=OFF', "-DCMAKE_INSTALL_PREFIX=$Prefix",
              '-DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreaded', '-DCMAKE_POLICY_DEFAULT_CMP0091=NEW',
              '-DCMAKE_POLICY_VERSION_MINIMUM=3.5',
              # MSVC by name: the runner's PATH also has MinGW's gcc, which CMake would otherwise try first.
              '-DCMAKE_C_COMPILER=cl', '-DCMAKE_CXX_COMPILER=cl')
  Run cmake -S (Join-Path $Work "zlib-$ZLIB_VER") -B (Join-Path $Work 'z') @Common `
    '-DZLIB_BUILD_SHARED=OFF' '-DZLIB_BUILD_STATIC=ON' '-DZLIB_BUILD_TESTING=OFF'
  Run cmake --build (Join-Path $Work 'z') -j $Jobs
  Run cmake --install (Join-Path $Work 'z')
  $zlibLib = Get-ChildItem -LiteralPath (Join-Path $Prefix 'lib') -Filter '*z*.lib' | Select-Object -First 1
  if (-not $zlibLib) { Fail "zlib's static library isn't in $Prefix\lib." }
  $ZlibArgs = @("-DZLIB_LIBRARY=$($zlibLib.FullName)", "-DZLIB_INCLUDE_DIR=$Prefix\include", "-DZLIB_ROOT=$Prefix")
  # FreeType with nothing optional but zlib (no PNG, HarfBuzz, Brotli or bzip2): Poppler only needs it to read fonts.
  Run cmake -S (Join-Path $Work "freetype-$FREETYPE_VER") -B (Join-Path $Work 'ft') @Common @ZlibArgs `
    '-DFT_DISABLE_ZLIB=OFF' '-DFT_REQUIRE_ZLIB=ON' '-DFT_DISABLE_BZIP2=ON' '-DFT_DISABLE_PNG=ON' `
    '-DFT_DISABLE_HARFBUZZ=ON' '-DFT_DISABLE_BROTLI=ON'
  Run cmake --build (Join-Path $Work 'ft') -j $Jobs
  Run cmake --install (Join-Path $Work 'ft')
  # Poppler with only what text extraction needs, as on the Mac, with Windows' own font lookup.
  Run cmake -S (Join-Path $Work "poppler-$POPPLER_VER") -B (Join-Path $Work 'pp') @Common @ZlibArgs `
    "-DCMAKE_PREFIX_PATH=$Prefix" '-DFONT_CONFIGURATION=win32' `
    '-DENABLE_UTILS=ON' '-DENABLE_CPP=OFF' '-DENABLE_GLIB=OFF' '-DENABLE_GOBJECT_INTROSPECTION=OFF' '-DENABLE_QT5=OFF' `
    '-DENABLE_QT6=OFF' '-DENABLE_BOOST=OFF' '-DENABLE_BROTLI=OFF' '-DENABLE_LIBOPENJPEG=OFF' '-DENABLE_LIBJPEG=OFF' `
    '-DENABLE_LCMS=OFF' '-DENABLE_LIBCURL=OFF' '-DENABLE_LIBTIFF=OFF' '-DENABLE_NSS3=OFF' `
    '-DENABLE_GPGME=OFF' '-DENABLE_HARFBUZZ=OFF' '-DENABLE_LIBPNG=OFF' '-DBUILD_MANUAL_TESTS=OFF' '-DBUILD_GTK_TESTS=OFF' `
    '-DBUILD_QT5_TESTS=OFF' '-DBUILD_QT6_TESTS=OFF' '-DBUILD_CPP_TESTS=OFF' '-DRUN_GPERF_IF_PRESENT=OFF' `
    '-DCMAKE_DISABLE_FIND_PACKAGE_PNG=ON' '-DCMAKE_DISABLE_FIND_PACKAGE_JPEG=ON' '-DCMAKE_DISABLE_FIND_PACKAGE_TIFF=ON' `
    '-DCMAKE_DISABLE_FIND_PACKAGE_Cairo=ON' '-DCMAKE_DISABLE_FIND_PACKAGE_Boost=ON' `
    '-DCMAKE_DISABLE_FIND_PACKAGE_Fontconfig=ON' '-DCMAKE_DISABLE_FIND_PACKAGE_GLIB=ON' `
    '-DCMAKE_DISABLE_FIND_PACKAGE_GTK=ON'
  Run cmake --build (Join-Path $Work 'pp') -j $Jobs --target pdftotext pdfinfo
  New-Item -ItemType Directory -Force -Path (Join-Path $PopplerOut 'bin'), (Join-Path $PopplerOut 'licenses') | Out-Null
  Copy-Item (Join-Path $Work 'pp\utils\pdftotext.exe'), (Join-Path $Work 'pp\utils\pdfinfo.exe') (Join-Path $PopplerOut 'bin')
  Copy-Item (Join-Path $Work "poppler-$POPPLER_VER\COPYING") (Join-Path $PopplerOut 'licenses\poppler-COPYING')
  Copy-Item (Join-Path $Work "poppler-$POPPLER_VER\COPYING3") (Join-Path $PopplerOut 'licenses\poppler-COPYING3')
  Copy-Item (Join-Path $Work "freetype-$FREETYPE_VER\LICENSE.TXT") (Join-Path $PopplerOut 'licenses\freetype-LICENSE.TXT')
  Copy-Item (Join-Path $Work "freetype-$FREETYPE_VER\docs\FTL.TXT") (Join-Path $PopplerOut 'licenses\freetype-FTL.TXT')
  Copy-Item (Join-Path $Work "zlib-$ZLIB_VER\LICENSE") (Join-Path $PopplerOut 'licenses\zlib-LICENSE')
  Remove-Item -Recurse -Force -LiteralPath $Work
}

# ---------- 3. The app folder ----------
Say "Assembling $Name"
$Stage = Join-Path $Dist '.stage'
$Public = Join-Path ([IO.Path]::GetTempPath()) ("starholm-public-" + [Guid]::NewGuid().ToString('N'))
if (Test-Path -LiteralPath $Stage) { Remove-Item -Recurse -Force -LiteralPath $Stage }
$App = Join-Path $Stage $Name
$Code = Join-Path $App 'starholm'
New-Item -ItemType Directory -Force -Path $Code | Out-Null
try {
  # The app's code: the public file set from HEAD, then only what runs (no tests, docs, build tools or README).
  Run $BuildPy -I (Join-Path $Root 'ops\make_public_tree.py') $Public | Out-Null
  foreach ($item in 'app', 'library', 'maps', 'upgrade', 'countries', 'guides', 'core.py', 'chat.py', 'memory.py',
                    'emergency.py', 'family_plan.py', 'recommended-models.json', 'LICENSE', 'THIRD_PARTY_NOTICES.md',
                    'SOURCES.md') {
    $src = Join-Path $Public $item
    if (-not (Test-Path -LiteralPath $src)) { Fail "the public tree has no $item" }
    Copy-Item -Recurse -LiteralPath $src -Destination $Code
  }
} finally {
  if (Test-Path -LiteralPath $Public) { Remove-Item -Recurse -Force -LiteralPath $Public }
}
Remove-Item -Force -LiteralPath (Join-Path $Code 'countries\validate.py'), (Join-Path $Code 'library\make_free_sources.py')

# Python: a fresh copy of the runtime, then the locked packages, installed the way requirements.lock's INSTALL block
# says (hashes required, wheels only except proxy-tools, nothing from outside settings).
Copy-Item -Recurse -LiteralPath (Join-Path $Pbs 'python') -Destination (Join-Path $App 'python')
$Py = Join-Path $App 'python\python.exe'
$env:PIP_CONFIG_FILE = 'NUL'  # pip skips every config file, as /dev/null does on the Mac
foreach ($v in 'PYTHONPATH', 'PYTHONHOME', 'PYTHONSTARTUP', 'PIP_INDEX_URL', 'PIP_EXTRA_INDEX_URL', 'PIP_FIND_LINKS') {
  [Environment]::SetEnvironmentVariable($v, $null)
}
$Pip = @('-I', '-m', 'pip', '--isolated', '--disable-pip-version-check', 'install', '--quiet', '--no-warn-script-location',
         '--index-url', 'https://pypi.org/simple', '--require-hashes', '--only-binary', ':all:')
Run $Py @Pip -r (Join-Path $Root 'requirements-build.lock')
Run $Py @Pip --no-binary proxy-tools --no-build-isolation --check-build-dependencies -r (Join-Path $Root 'requirements-windows.lock')
Run $Py -I -c 'import webview, pythonnet, clr_loader, lxml, libzim, pypdf, trafilatura, truststore, cryptography'

# Take out what the app never runs: the installers, Tk, the test suite and the extra commands. The headers and
# python313.lib stay until the launcher is compiled.
$SP = Join-Path $App 'python\Lib\site-packages'
foreach ($p in 'pip', 'pip-*', 'setuptools', 'setuptools-*', '_distutils_hack', 'distutils-precedence.pth',
               'pkg_resources', 'wheel', 'wheel-*') {
  Get-ChildItem -Path $SP -Filter $p -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force
}
$Std = Join-Path $App 'python\Lib'
foreach ($p in 'test', 'idlelib', 'tkinter', 'turtledemo', 'ensurepip', 'turtle.py') {
  $t = Join-Path $Std $p
  if (Test-Path -LiteralPath $t) { Remove-Item -Recurse -Force -LiteralPath $t }
}
$PyRoot = Join-Path $App 'python'
foreach ($p in 'tcl', 'Scripts', 'Tools') {
  $t = Join-Path $PyRoot $p
  if (Test-Path -LiteralPath $t) { Remove-Item -Recurse -Force -LiteralPath $t }
}
Get-ChildItem -Path (Join-Path $PyRoot 'DLLs') -Include '_tkinter.pyd', 'tcl*.dll', 'tk*.dll' -Recurse | Remove-Item -Force
Get-ChildItem -Path $App -Recurse -Directory -Filter '__pycache__' | Remove-Item -Recurse -Force
Get-ChildItem -Path $App -Recurse -File -Filter '*.pyc' | Remove-Item -Force

# Bytecode, compiled now so the app never writes into its install folder: unchecked-hash .pyc files are used as they
# are, never rechecked against the source and never rewritten. A few third-party files don't compile (templates,
# Python 2 examples); those stay as source only.
& $Py -I -m compileall -q -f -j $Jobs --invalidation-mode unchecked-hash $Std $Code | Out-Null

# Poppler.
New-Item -ItemType Directory -Force -Path (Join-Path $App 'poppler') | Out-Null
Copy-Item -Recurse (Join-Path $PopplerOut 'bin'), (Join-Path $PopplerOut 'licenses') (Join-Path $App 'poppler')
& (Join-Path $App 'poppler\bin\pdftotext.exe') -v 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Fail "the bundled pdftotext doesn't run" }

# The icon: the standard Windows sizes from the 1024 px PNG, each stored as PNG inside the .ico (Windows Vista and
# later read those), drawn with .NET's System.Drawing.
$Build = Join-Path $Stage 'build'
New-Item -ItemType Directory -Force -Path $Build | Out-Null
$Ico = Join-Path $Build 'starholm.ico'
Add-Type -AssemblyName System.Drawing
$source = [System.Drawing.Image]::FromFile($IconPng)
$images = foreach ($size in 16, 20, 24, 32, 40, 48, 64, 128, 256) {
  $bmp = New-Object System.Drawing.Bitmap $size, $size
  $g = [System.Drawing.Graphics]::FromImage($bmp)
  $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
  $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
  $g.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
  $g.DrawImage($source, 0, 0, $size, $size)
  $g.Dispose()
  $ms = New-Object System.IO.MemoryStream
  $bmp.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png)
  $bmp.Dispose()
  , @($size, $ms.ToArray())
}
$source.Dispose()
$fs = [System.IO.File]::Create($Ico)
$w = New-Object System.IO.BinaryWriter $fs
$w.Write([UInt16]0); $w.Write([UInt16]1); $w.Write([UInt16]$images.Count)
$offset = 6 + 16 * $images.Count
foreach ($im in $images) {
  $s = $im[0]; $bytes = $im[1]
  $w.Write([Byte]($s % 256)); $w.Write([Byte]($s % 256)); $w.Write([Byte]0); $w.Write([Byte]0)
  $w.Write([UInt16]1); $w.Write([UInt16]32); $w.Write([UInt32]$bytes.Length); $w.Write([UInt32]$offset)
  $offset += $bytes.Length
}
foreach ($im in $images) { $w.Write([byte[]]$im[1]) }
$w.Close()

# The launcher: Starholm.exe, a windowed program with the icon and version, linked against python313.dll, which it
# loads from python\ when first called (delay-loaded, so it can point Windows at that folder first).
$parts = $Version.Split('.') + @('0', '0', '0')
$commaVer = "$($parts[0]),$($parts[1]),$($parts[2]),0"
@"
#include <winver.h>
1 ICON "starholm.ico"
VS_VERSION_INFO VERSIONINFO
FILEVERSION $commaVer
PRODUCTVERSION $commaVer
FILEOS VOS_NT_WINDOWS32
FILETYPE VFT_APP
BEGIN
  BLOCK "StringFileInfo"
  BEGIN
    BLOCK "040904B0"
    BEGIN
      VALUE "CompanyName", "Starholm"
      VALUE "FileDescription", "Starholm"
      VALUE "FileVersion", "$Version"
      VALUE "InternalName", "Starholm"
      VALUE "LegalCopyright", "MIT License. See starholm\\LICENSE."
      VALUE "OriginalFilename", "Starholm.exe"
      VALUE "ProductName", "Starholm"
      VALUE "ProductVersion", "$Version"
    END
  END
  BLOCK "VarFileInfo"
  BEGIN
    VALUE "Translation", 0x409, 1200
  END
END
"@ | Set-Content -Encoding ascii -LiteralPath (Join-Path $Build 'starholm.rc')
Push-Location $Build
try {
  Run rc /nologo /fo starholm.res starholm.rc
  # /MT with the Universal CRT from Windows (ucrt.lib, not libucrt.lib): the Visual C++ runtime is built in, so the
  # program starts before python\ is on the DLL path, and it shares Windows' C runtime with python313.dll, so the
  # log it opens as stdout and stderr is the one Python writes to.
  Run cl /nologo /O2 /W3 /MT /DUNICODE /D_UNICODE "/I$PyRoot\include" (Join-Path $Root 'ops\windows-launcher.c') `
    starholm.res "/Fe:$(Join-Path $App "$Name.exe")" /link /SUBSYSTEM:WINDOWS "/LIBPATH:$PyRoot\libs" `
    /NODEFAULTLIB:libucrt.lib ucrt.lib "/DELAYLOAD:$PyDll.dll" delayimp.lib shell32.lib ole32.lib user32.lib uuid.lib
} finally { Pop-Location }
foreach ($p in 'include', 'libs') {
  $t = Join-Path $PyRoot $p
  if (Test-Path -LiteralPath $t) { Remove-Item -Recurse -Force -LiteralPath $t }
}

# ---------- 4. Checks: nothing pointing at this build PC, nothing the app would write into its folder ----------
Say 'Checking the app folder'
$texts = Get-ChildItem -LiteralPath $App -Recurse -File -Include '*.py', '*.json', '*.txt', '*.cfg', '*.pth', '*.md'
$leak = $texts | Select-String -SimpleMatch -Pattern $Root, $env:USERPROFILE -List | Select-Object -First 5
if ($leak) { $leak | ForEach-Object { Write-Host $_.Path }; Fail 'the files above hold a path from this build PC.' }
if (-not (Test-Path -LiteralPath (Join-Path $Code 'app\__pycache__'))) { Fail "the app's code wasn't compiled" }

# ---------- 5. The installer and the .zip ----------
Say 'Packaging'
$innoDir = Join-Path $Cache "innosetup-$INNO_VER"
$iscc = Join-Path $innoDir 'ISCC.exe'
if (-not (Test-Path -LiteralPath $iscc)) {
  $innoSetup = Fetch $INNO_URL $INNO_SHA256 "innosetup-$INNO_VER.exe"
  $p = Start-Process -FilePath $innoSetup -Wait -PassThru -ArgumentList @(
    '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', '/CURRENTUSER', '/NOICONS', "/DIR=`"$innoDir`"")
  if ($p.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $iscc)) { Fail "Inno Setup didn't install ($($p.ExitCode))." }
}
$Setup = Join-Path $Dist "$Name-Setup-$Version.exe"
$Zip = Join-Path $Dist "$Name-$Version-windows-x64.zip"
foreach ($f in $Setup, $Zip) { if (Test-Path -LiteralPath $f) { Remove-Item -Force -LiteralPath $f } }
Run $iscc /Q "/DAppVersion=$Version" "/DStageDir=$App" "/DOutputDir=$Dist" "/DIconFile=$Ico" (Join-Path $Root 'ops\windows-installer.iss')
Push-Location $Stage
try { Run $Tar -a -c -f $Zip $Name } finally { Pop-Location }
Remove-Item -Recurse -Force -LiteralPath $Build

Say 'Done'
$appBytes = (Get-ChildItem -LiteralPath $App -Recurse -File | Measure-Object -Property Length -Sum).Sum
Write-Host ("App folder: {0} ({1:N0} MB)" -f $App, ($appBytes / 1MB))
foreach ($f in $Setup, $Zip) {
  Write-Host ("{0}: {1} bytes, SHA-256 {2}" -f (Split-Path -Leaf $f), (Get-Item -LiteralPath $f).Length, (Sha256 $f))
}
