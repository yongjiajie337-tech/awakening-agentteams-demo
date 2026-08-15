#requires -Version 5.1

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("FailClosedGateway", "StateMcp")]
    [string]$Runtime
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$workspace = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
$python = [System.IO.Path]::GetFullPath((Join-Path $workspace ".venv\Scripts\python.exe"))
$pythonPath = [System.IO.Path]::GetFullPath((Join-Path $workspace "src"))
foreach ($path in @($python, $pythonPath)) {
    $item = Get-Item -LiteralPath $path -Force -ErrorAction Stop
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "M4_HOST_RUNTIME_LAUNCH_INPUT_INVALID"
    }
}

foreach ($name in @(
    "HICLAW_LLM_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "DASHSCOPE_API_KEY"
)) {
    [Environment]::SetEnvironmentVariable($name, $null, "Process")
}
[Environment]::SetEnvironmentVariable("PYTHONPATH", $pythonPath, "Process")

if ($Runtime -ceq "FailClosedGateway") {
    $runtimeDirectory = [System.IO.Path]::GetFullPath((Join-Path $workspace "tmp\m4\gateway"))
    $pidPath = Join-Path $runtimeDirectory "gateway.pid"
    $stdoutPath = Join-Path $runtimeDirectory "gateway.stdout.log"
    $stderrPath = Join-Path $runtimeDirectory "gateway.stderr.log"
    $credentials = [System.IO.Path]::GetFullPath((Join-Path $workspace "tmp\m4\m4-runtime-secrets-v1\gateway-credentials.env"))
    $arguments = @(
        "-m", "awakening.model_gateway.m4.fail_closed_runtime",
        "--credentials", $credentials,
        "--host", "127.0.0.1",
        "--port", "18190"
    )
    foreach ($target in @($pidPath, $stdoutPath, $stderrPath)) {
        if (Test-Path -LiteralPath $target) {
            throw "M4_HOST_RUNTIME_LAUNCH_TARGET_ALREADY_EXISTS"
        }
    }
}
else {
    $runtimeDirectory = [System.IO.Path]::GetFullPath((Join-Path $workspace "tmp\m4\state-http"))
    $pidPath = Join-Path $runtimeDirectory "state-http.pid"
    $stdoutPath = Join-Path $runtimeDirectory "state-http.stdout.log"
    $stderrPath = Join-Path $runtimeDirectory "state-http.stderr.log"
    $m2Env = [System.IO.Path]::GetFullPath((Join-Path $workspace ".env.m2"))
    $m4Env = [System.IO.Path]::GetFullPath((Join-Path $workspace ".env.m4"))
    $fixtureState = [System.IO.Path]::GetFullPath((Join-Path $workspace "tmp\m4\state\runtime-state.json"))
    $arguments = @(
        "-m", "awakening.adapters.m4.state_http_runtime",
        "--m2-env", $m2Env,
        "--m4-env", $m4Env,
        "--fixture-state", $fixtureState,
        "--host", "127.0.0.1",
        "--port", "18191"
    )
    foreach ($target in @($pidPath, $stdoutPath, $stderrPath)) {
        $item = Get-Item -LiteralPath $target -Force -ErrorAction Stop
        if ($item.PSIsContainer -or
            ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
            $item.Length -ne 0) {
            throw "M4_HOST_RUNTIME_RESERVED_TARGET_INVALID"
        }
    }
}

foreach ($inputPath in @($arguments | Where-Object {
    $_ -is [string] -and [System.IO.Path]::IsPathRooted([string]$_)
})) {
    $item = Get-Item -LiteralPath $inputPath -Force -ErrorAction Stop
    if ($item.PSIsContainer -or
        ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "M4_HOST_RUNTIME_LAUNCH_INPUT_INVALID"
    }
}

$process = Start-Process `
    -FilePath $python `
    -ArgumentList $arguments `
    -WorkingDirectory $workspace `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdoutPath `
    -RedirectStandardError $stderrPath `
    -PassThru
[System.IO.File]::WriteAllText(
    $pidPath,
    [string]$process.Id,
    (New-Object System.Text.UTF8Encoding($false))
)
