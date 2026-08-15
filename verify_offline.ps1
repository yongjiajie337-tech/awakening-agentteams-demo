#requires -Version 5.1

<#
.SYNOPSIS
Verify this review package without Docker, network, Provider, or Secret access.
#>

[CmdletBinding()]
param(
    [string]$PythonPath = "",
    [ValidateSet("Full", "Stdlib", "PackageOnly")]
    [string]$Mode = "Full",
    [switch]$SkipUnitTests
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$packageRoot = [IO.Path]::GetFullPath($PSScriptRoot)
$verifier = Join-Path $packageRoot "scripts\package\verify_package.py"
$expectedFullUnitTestCount = 97
$expectedStdlibUnitTestCount = 33
$fullUnitTestDirectories = @(
    "tests/unit/demo",
    "tests/unit/m4"
)
$stdlibUnitTestFiles = @(
    "tests/unit/demo/test_demo_matrix_control_contract.py",
    "tests/unit/demo/test_demo_worker_gateway_key_sync_contract.py",
    "tests/unit/demo/test_offline_verify_contract.py",
    "tests/unit/demo/test_provider_secret_acl_contract.py",
    "tests/unit/demo/test_release_sealer.py"
)

$effectiveMode = if ($Mode -ieq "Stdlib") {
    "Stdlib"
}
elseif ($Mode -ieq "PackageOnly") {
    "PackageOnly"
}
else {
    "Full"
}
if ($SkipUnitTests) {
    if ($PSBoundParameters.ContainsKey("Mode") -and
        $effectiveMode -cne "PackageOnly") {
        throw "OFFLINE_VERIFY_MODE_CONFLICT"
    }
    $effectiveMode = "PackageOnly"
}

function Write-Python312Hint {
    Write-Host "OFFLINE_VERIFY_HINT=Run 'py -3.12 --version', or pass -PythonPath with the full path to python.exe from a Python 3.12 environment."
}

function Write-Utf8Base64Hint {
    param([Parameter(Mandatory = $true)][string]$TextBase64)
    $message = [Text.Encoding]::UTF8.GetString(
        [Convert]::FromBase64String($TextBase64)
    )
    Write-Host ("OFFLINE_VERIFY_HINT_ZH=" + $message)
}

function Stop-DependencyPreflight {
    param([Parameter(Mandatory = $true)][int]$ExitCode)
    switch ($ExitCode) {
        78 {
            Write-Utf8Base64Hint -TextBase64 "cmVxdWlyZW1lbnRzLWRlbW8ubG9jayDmoLzlvI/ml6DmlYjvvJvor7fkvb/nlKjigJzljIXlkI09PeeyvuehrueJiOacrOKAneeahOS4gOihjOS4gOmhueagvOW8j++8jOW5tumHjeaWsOino+WOi+WOn+WMheehruiupOaWh+S7tuacquiiq+S/ruaUueOAgg=="
            Write-Host "OFFLINE_VERIFY_HINT_EN=requirements-demo.lock is invalid. Re-extract a fresh copy of the original package and retry."
            throw "OFFLINE_VERIFY_DEPENDENCY_LOCK_INVALID"
        }
        79 {
            Write-Utf8Base64Hint -TextBase64 "57y65bCR6ZSB5a6a5L6d6LWW77yb6K+35Zyo5YyF5aSWIFB5dGhvbiAzLjEyIOiZmuaLn+eOr+Wig+S4reaJp+ihjCBwaXAgaW5zdGFsbCAtciAuXHJlcXVpcmVtZW50cy1kZW1vLmxvY2vjgII="
            Write-Host "OFFLINE_VERIFY_HINT_EN=A locked dependency is missing. Create a Python 3.12 virtual environment outside this package, then run: python -m pip install -r .\requirements-demo.lock"
            throw "OFFLINE_VERIFY_DEPENDENCY_MISSING"
        }
        80 {
            Write-Utf8Base64Hint -TextBase64 "5L6d6LWW54mI5pys5LiO6ZSB5paH5Lu25LiN5LiA6Ie077yb6K+35Zyo5YyF5aSW5paw5bu65bmy5YeA55qEIFB5dGhvbiAzLjEyIOiZmuaLn+eOr+Wig+W5tuaMiSByZXF1aXJlbWVudHMtZGVtby5sb2NrIOmHjeaWsOWuieijheOAgg=="
            Write-Host "OFFLINE_VERIFY_HINT_EN=A dependency version does not match the lock. Create a clean Python 3.12 virtual environment outside this package, then run: python -m pip install -r .\requirements-demo.lock"
            throw "OFFLINE_VERIFY_DEPENDENCY_VERSION_MISMATCH"
        }
        81 {
            Write-Utf8Base64Hint -TextBase64 "5L6d6LWW5bey5a6J6KOF5L2G5peg5rOV5a+85YWl77yb6K+36YeN5paw5Yib5bu65YyF5aSW6Jma5ouf546v5aKD5bm25oyJIHJlcXVpcmVtZW50cy1kZW1vLmxvY2sg5a6J6KOF77yM5Yu/5aSN55So5o2f5Z2P55qE5YWo5bGA546v5aKD44CC"
            Write-Host "OFFLINE_VERIFY_HINT_EN=A locked dependency cannot be imported. Create a clean Python 3.12 virtual environment outside this package, then run: python -m pip install -r .\requirements-demo.lock"
            throw "OFFLINE_VERIFY_DEPENDENCY_IMPORT_FAILED"
        }
        default {
            Write-Utf8Base64Hint -TextBase64 "5L6d6LWW6aKE5qOA5byC5bi45YGc5q2i77yb6K+36YeN5paw6Kej5Y6L5bm25oyJIFFVSUNLU1RBUlRfV0lORE9XUy5tZCDliJvlu7rljIXlpJbomZrmi5/njq/looPjgII="
            Write-Host "OFFLINE_VERIFY_HINT_EN=Dependency preflight failed unexpectedly. Re-extract a fresh copy, then follow QUICKSTART_WINDOWS.md to create an external Python 3.12 virtual environment."
            throw ("OFFLINE_VERIFY_DEPENDENCY_PREFLIGHT_FAILED:" + $ExitCode)
        }
    }
}

function New-PythonInvocation {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [AllowEmptyCollection()]
        [Parameter(Mandatory = $true)][string[]]$PrefixArguments
    )
    return [pscustomobject]@{
        FilePath = $FilePath
        PrefixArguments = [string[]]$PrefixArguments
    }
}

function Test-Python312 {
    param([Parameter(Mandatory = $true)]$Invocation)
    try {
        $arguments = @($Invocation.PrefixArguments) + @(
            "-I", "-B", "-c",
            "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
        )
        $version = @(& $Invocation.FilePath @arguments 2>$null)
        $exitCode = $LASTEXITCODE
        return ($exitCode -eq 0 -and $version.Count -eq 1 -and
            [string]$version[0] -ceq "3.12")
    }
    catch {
        return $false
    }
}

function Resolve-Python {
    if (-not [string]::IsNullOrWhiteSpace($PythonPath)) {
        try {
            $candidate = [IO.Path]::GetFullPath($PythonPath)
        }
        catch {
            throw "OFFLINE_VERIFY_PYTHON_PATH_INVALID"
        }
        try {
            $present = Test-Path -LiteralPath $candidate -PathType Leaf `
                -ErrorAction Stop
        }
        catch {
            throw "OFFLINE_VERIFY_PYTHON_PATH_INVALID"
        }
        if (-not $present) {
            throw "OFFLINE_VERIFY_PYTHON_PATH_INVALID"
        }
        try {
            $item = Get-Item -LiteralPath $candidate -Force -ErrorAction Stop
        }
        catch {
            throw "OFFLINE_VERIFY_PYTHON_PATH_INVALID"
        }
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "OFFLINE_VERIFY_PYTHON_PATH_INVALID"
        }
        $explicit = New-PythonInvocation -FilePath $item.FullName `
            -PrefixArguments @()
        if (-not (Test-Python312 -Invocation $explicit)) {
            Write-Python312Hint
            throw "OFFLINE_VERIFY_PYTHON_VERSION_UNSUPPORTED"
        }
        return $explicit
    }

    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($null -ne $launcher) {
        $candidate = New-PythonInvocation -FilePath $launcher.Source `
            -PrefixArguments @("-3.12")
        if (Test-Python312 -Invocation $candidate) {
            return $candidate
        }
    }

    $command = Get-Command python.exe -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($null -ne $command) {
        $candidate = New-PythonInvocation -FilePath $command.Source `
            -PrefixArguments @()
        if (Test-Python312 -Invocation $candidate) {
            return $candidate
        }
    }

    Write-Python312Hint
    throw "OFFLINE_VERIFY_PYTHON_3_12_REQUIRED"
}

function Invoke-Python {
    param(
        [Parameter(Mandatory = $true)]$Invocation,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )
    $allArguments = @($Invocation.PrefixArguments) + $Arguments
    & $Invocation.FilePath @allArguments
}

if (-not (Test-Path -LiteralPath $verifier -PathType Leaf)) {
    throw "OFFLINE_VERIFY_SCRIPT_MISSING"
}

$python = Resolve-Python
Write-Output "OFFLINE_VERIFY_PYTHON_VERSION=3.12"
Write-Output ("OFFLINE_VERIFY_MODE=" + $effectiveMode)
if ($SkipUnitTests) {
    Write-Output "OFFLINE_VERIFY_COMPATIBILITY_ALIAS=SkipUnitTests->PackageOnly"
}

$tokens = $null
$errors = $null
foreach ($script in @(
    (Join-Path $packageRoot "verify_offline.ps1"),
    (Join-Path $packageRoot "run_demo.ps1")
)) {
    [void][Management.Automation.Language.Parser]::ParseFile(
        $script,
        [ref]$tokens,
        [ref]$errors
    )
    if (@($errors).Count -ne 0) {
        throw ("OFFLINE_VERIFY_POWERSHELL_PARSE_FAILED:" + [IO.Path]::GetFileName($script))
    }
}

$previousBytecode = $env:PYTHONDONTWRITEBYTECODE
$previousOffline = $env:AWAKENING_PACKAGE_OFFLINE_VERIFY
$originalLocation = Get-Location
try {
    $env:PYTHONDONTWRITEBYTECODE = "1"
    $env:AWAKENING_PACKAGE_OFFLINE_VERIFY = "1"
    Set-Location -LiteralPath $packageRoot

    if ($effectiveMode -ceq "Full") {
        $dependencyPreflight = @'
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import re
import sys

root = Path(sys.argv[1])
requirements = root / 'requirements-demo.lock'

def clean(value):
    return ''.join(
        character if character.isascii() and (character.isalnum() or character in '._+-') else '?'
        for character in str(value)
    )[:128]

def fail(code, marker):
    print(marker, file=sys.stderr)
    raise SystemExit(code)

try:
    raw_lines = requirements.read_text(encoding='utf-8').splitlines()
except UnicodeError:
    fail(78, 'OFFLINE_VERIFY_DEPENDENCY_LOCK_INVALID=line=NA;category=utf8-decode')
except OSError:
    fail(78, 'OFFLINE_VERIFY_DEPENDENCY_LOCK_INVALID=line=NA;category=unreadable')

pins = {}
for line_number, raw_line in enumerate(raw_lines, 1):
    line = raw_line.strip()
    if not line or line.startswith('#'):
        continue
    if line.count('==') != 1:
        fail(
            78,
            f'OFFLINE_VERIFY_DEPENDENCY_LOCK_INVALID=line={line_number};category=exact-pin-separator-count',
        )
    name, expected = line.split('==', 1)
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', name):
        fail(
            78,
            f'OFFLINE_VERIFY_DEPENDENCY_LOCK_INVALID=line={line_number};category=package-name',
        )
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.!+_-]*', expected):
        fail(
            78,
            f'OFFLINE_VERIFY_DEPENDENCY_LOCK_INVALID=line={line_number};category=exact-version',
        )
    pins[name.casefold()] = (name, expected)
    try:
        actual = version(name)
    except PackageNotFoundError:
        fail(
            79,
            'OFFLINE_VERIFY_DEPENDENCY_MISSING='
            f'package={name};expected={expected};actual=MISSING',
        )
    if actual != expected:
        fail(
            80,
            'OFFLINE_VERIFY_DEPENDENCY_VERSION_MISMATCH='
            f'package={name};expected={expected};actual={clean(actual)}',
        )

for distribution_name, module_name in (
    ('jsonschema', 'jsonschema'),
    ('psycopg', 'psycopg'),
):
    if distribution_name not in pins:
        fail(
            78,
            'OFFLINE_VERIFY_DEPENDENCY_LOCK_INVALID='
            f'line=NA;category=required-import-pin-missing-{distribution_name}',
        )
    package_name, expected = pins[distribution_name]
    actual = clean(version(package_name))
    try:
        import_module(module_name)
    except Exception:
        fail(
            81,
            'OFFLINE_VERIFY_DEPENDENCY_IMPORT_FAILED='
            f'package={package_name};module={module_name};expected={expected};actual={actual}',
        )
'@
        Invoke-Python -Invocation $python -Arguments @(
            "-I", "-B", "-c", $dependencyPreflight, $packageRoot
        )
        $dependencyExitCode = $LASTEXITCODE
        if ($dependencyExitCode -ne 0) {
            Stop-DependencyPreflight -ExitCode $dependencyExitCode
        }
        Write-Output "OFFLINE_VERIFY_DEPENDENCY_PREFLIGHT=PASS"
    }
    else {
        Write-Output ("OFFLINE_VERIFY_DEPENDENCY_PREFLIGHT=NOT_REQUIRED_FOR_" + $effectiveMode.ToUpperInvariant())
    }

    Invoke-Python -Invocation $python -Arguments @(
        "-I", "-B", $verifier, "--package-root", $packageRoot
    )
    if ($LASTEXITCODE -ne 0) {
        throw ("OFFLINE_PACKAGE_VERIFIER_FAILED:" + $LASTEXITCODE)
    }

    if ($effectiveMode -cne "PackageOnly") {
        $unitTestRunner = @'
from pathlib import Path
import sys
import unittest

root = Path(sys.argv[1]).resolve()
expected = int(sys.argv[2])
profile = sys.argv[3]
full_directories = tuple(sys.argv[4].split('|'))
stdlib_files = tuple(sys.argv[5].split('|'))
sys.path.insert(0, str(root / 'src'))
loader = unittest.TestLoader()
suite = unittest.TestSuite()
if profile == 'Full':
    for relative in full_directories:
        start = root / relative
        if not start.is_dir():
            print(f'OFFLINE_UNIT_TEST_DIRECTORY_MISSING={relative}', file=sys.stderr)
            raise SystemExit(81)
        suite.addTests(loader.discover(str(start), pattern='test_*.py', top_level_dir=str(start)))
elif profile == 'Stdlib':
    for relative in stdlib_files:
        test_file = root / relative
        if not test_file.is_file():
            print(f'OFFLINE_UNIT_TEST_FILE_MISSING={relative}', file=sys.stderr)
            raise SystemExit(81)
        start = test_file.parent
        suite.addTests(
            loader.discover(
                str(start),
                pattern=test_file.name,
                top_level_dir=str(start),
            )
        )
else:
    print(f'OFFLINE_UNIT_TEST_PROFILE_INVALID={profile}', file=sys.stderr)
    raise SystemExit(84)
count = suite.countTestCases()
print(f'OFFLINE_UNIT_TEST_PROFILE={profile}')
print(f'OFFLINE_UNIT_TEST_DISCOVERED={count}')
if count != expected:
    print(f'OFFLINE_UNIT_TEST_COUNT_MISMATCH={count}:{expected}', file=sys.stderr)
    raise SystemExit(82)
result = unittest.TextTestRunner(verbosity=2).run(suite)
if not result.wasSuccessful():
    raise SystemExit(83)
'@
        $expectedUnitTestCount = if ($effectiveMode -ceq "Full") {
            $expectedFullUnitTestCount
        }
        else {
            $expectedStdlibUnitTestCount
        }
        Invoke-Python -Invocation $python -Arguments @(
            "-I", "-B", "-c", $unitTestRunner,
            $packageRoot, [string]$expectedUnitTestCount, $effectiveMode,
            ($fullUnitTestDirectories -join "|"),
            ($stdlibUnitTestFiles -join "|")
        )
        if ($LASTEXITCODE -ne 0) {
            throw ("OFFLINE_DEMO_AND_M4_UNIT_TESTS_FAILED:" + $LASTEXITCODE)
        }
        Write-Output ("OFFLINE_UNIT_TEST_COUNT=" + $expectedUnitTestCount)
        Write-Output "OFFLINE_DEMO_AND_M4_UNIT_TESTS=PASS"
        if ($effectiveMode -ceq "Stdlib") {
            Write-Output "OFFLINE_STDLIB_WORKER_SHELL_CONTRACT_TEST_COUNT=8"
            Write-Output "OFFLINE_STDLIB_GIT_BASH_DYNAMIC_TEST_COUNT=1"
            Write-Output "OFFLINE_STDLIB_GIT_BASH_DYNAMIC_TEST_POLICY=SKIP_IF_UNAVAILABLE"
        }
    }
    else {
        Write-Output "OFFLINE_DEMO_AND_M4_UNIT_TESTS=NOT_RUN_IN_PACKAGE_ONLY_MODE"
    }
}
finally {
    Set-Location -LiteralPath $originalLocation
    $env:PYTHONDONTWRITEBYTECODE = $previousBytecode
    $env:AWAKENING_PACKAGE_OFFLINE_VERIFY = $previousOffline
}

Write-Output "PACKAGE_OFFLINE_VERIFY=PASS"
Write-Output ("PACKAGE_OFFLINE_VERIFY_MODE=" + $effectiveMode)
Write-Output "PACKAGE_OFFLINE_DOCKER_CALLED=false"
Write-Output "PACKAGE_OFFLINE_NETWORK_CALLED=false"
Write-Output "PACKAGE_OFFLINE_PROVIDER_CALLED=false"
Write-Output "PACKAGE_OFFLINE_SECRET_READ=false"
