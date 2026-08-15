#requires -Version 5.1

[CmdletBinding()]
param(
    [int]$ReadyTimeoutSeconds = 20,
    [switch]$KeepSessionOpen,
    [switch]$LaunchViaCim,
    [switch]$ValidateExisting,
    [guid]$WindowId = [guid]::NewGuid()
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($WindowId -eq [guid]::Empty) {
    throw "M4_FAIL_CLOSED_GATEWAY_WINDOW_ID_INVALID"
}
if ($KeepSessionOpen -and $LaunchViaCim) {
    throw "M4_HOST_RUNTIME_LAUNCH_MODE_CONFLICT"
}
if ($ValidateExisting -and ($KeepSessionOpen -or $LaunchViaCim)) {
    throw "M4_FAIL_CLOSED_GATEWAY_VALIDATE_MODE_CONFLICT"
}

function Resolve-M4FailClosedRegularFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Reason
    )

    $candidate = [IO.Path]::GetFullPath($Path)
    $resolved = [IO.Path]::GetFullPath(
        (Resolve-Path -LiteralPath $candidate -ErrorAction Stop).ProviderPath
    )
    $item = Get-Item -LiteralPath $resolved -Force -ErrorAction Stop
    if (-not [StringComparer]::OrdinalIgnoreCase.Equals($candidate, $resolved) -or
        $item.PSIsContainer -or
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw $Reason
    }
    return $resolved
}

function Resolve-M4FailClosedRegularDirectory {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Reason
    )

    $candidate = [IO.Path]::GetFullPath($Path)
    $resolved = [IO.Path]::GetFullPath(
        (Resolve-Path -LiteralPath $candidate -ErrorAction Stop).ProviderPath
    )
    $item = Get-Item -LiteralPath $resolved -Force -ErrorAction Stop
    if (-not [StringComparer]::OrdinalIgnoreCase.Equals($candidate, $resolved) -or
        -not $item.PSIsContainer -or
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw $Reason
    }
    return $resolved
}

function Get-M4FailClosedListeners {
    return @(
        Get-NetTCPConnection -State Listen -ErrorAction Stop |
            Where-Object { [int]$_.LocalPort -eq 18190 }
    )
}

$nativeSource = @'
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Runtime.InteropServices;

public static class M4FailClosedNativeCommandLineParser
{
    [DllImport("shell32.dll", SetLastError = true)]
    private static extern IntPtr CommandLineToArgvW(
        [MarshalAs(UnmanagedType.LPWStr)] string commandLine,
        out int argumentCount
    );

    [DllImport("kernel32.dll")]
    private static extern IntPtr LocalFree(IntPtr memory);

    public static string[] Split(string commandLine)
    {
        int count;
        IntPtr pointer = CommandLineToArgvW(commandLine, out count);
        if (pointer == IntPtr.Zero)
        {
            throw new Win32Exception(Marshal.GetLastWin32Error());
        }
        try
        {
            var values = new List<string>(count);
            for (int index = 0; index < count; index++)
            {
                IntPtr value = Marshal.ReadIntPtr(pointer, index * IntPtr.Size);
                values.Add(Marshal.PtrToStringUni(value));
            }
            return values.ToArray();
        }
        finally
        {
            LocalFree(pointer);
        }
    }
}
'@
if ($null -eq ("M4FailClosedNativeCommandLineParser" -as [type])) {
    Add-Type -TypeDefinition $nativeSource -Language CSharp -ErrorAction Stop
}

function Assert-M4FailClosedProcessArguments {
    param(
        [Parameter(Mandatory = $true)][int]$ProcessId,
        [Parameter(Mandatory = $true)][string]$ExpectedExecutable,
        [Parameter(Mandatory = $true)][string[]]$ExpectedArguments,
        [int[]]$PathArgumentIndices = @()
    )

    $process = Get-Process -Id $ProcessId -ErrorAction Stop
    if ($process.HasExited) {
        throw "M4_FAIL_CLOSED_GATEWAY_PROCESS_NOT_RUNNING"
    }
    $records = @(Get-CimInstance -ClassName Win32_Process `
        -Filter ("ProcessId = " + $ProcessId) -ErrorAction Stop)
    if ($records.Count -ne 1 -or
        [string]::IsNullOrWhiteSpace([string]$records[0].CommandLine) -or
        [string]::IsNullOrWhiteSpace([string]$records[0].ExecutablePath) -or
        -not [StringComparer]::OrdinalIgnoreCase.Equals(
            [IO.Path]::GetFullPath([string]$records[0].ExecutablePath),
            [IO.Path]::GetFullPath($ExpectedExecutable)
        )) {
        throw "M4_FAIL_CLOSED_GATEWAY_PROCESS_IDENTITY_INVALID"
    }
    $arguments = [M4FailClosedNativeCommandLineParser]::Split(
        [string]$records[0].CommandLine
    )
    if ($arguments.Count -ne $ExpectedArguments.Count) {
        throw "M4_FAIL_CLOSED_GATEWAY_PROCESS_ARGUMENTS_INVALID"
    }
    for ($index = 0; $index -lt $ExpectedArguments.Count; $index++) {
        $comparison = [StringComparison]::Ordinal
        if ($PathArgumentIndices -contains $index) {
            $comparison = [StringComparison]::OrdinalIgnoreCase
        }
        if (-not [string]::Equals(
            [string]$arguments[$index],
            [string]$ExpectedArguments[$index],
            $comparison
        )) {
            throw "M4_FAIL_CLOSED_GATEWAY_PROCESS_ARGUMENTS_INVALID"
        }
    }
    return $process
}

function Assert-M4FailClosedRuntimeIdentity {
    param(
        [Parameter(Mandatory = $true)][string]$PidPath,
        [Parameter(Mandatory = $true)][string]$ParentExecutable,
        [Parameter(Mandatory = $true)][string[]]$ParentArguments,
        [Parameter(Mandatory = $true)][string]$ChildExecutable,
        [Parameter(Mandatory = $true)][string[]]$ChildArguments
    )

    $resolvedPidPath = Resolve-M4FailClosedRegularFile -Path $PidPath `
        -Reason "M4_FAIL_CLOSED_GATEWAY_PID_FILE_INVALID"
    $pidText = [IO.File]::ReadAllText($resolvedPidPath).Trim()
    if ($pidText -notmatch '^[1-9][0-9]{0,9}$') {
        throw "M4_FAIL_CLOSED_GATEWAY_PID_FILE_INVALID"
    }
    try {
        $parentPid = [int]$pidText
    }
    catch {
        throw "M4_FAIL_CLOSED_GATEWAY_PID_FILE_INVALID"
    }
    $parent = Assert-M4FailClosedProcessArguments -ProcessId $parentPid `
        -ExpectedExecutable $ParentExecutable -ExpectedArguments $ParentArguments `
        -PathArgumentIndices @(0, 4)
    $listeners = @(Get-M4FailClosedListeners)
    if ($listeners.Count -ne 1 -or
        [string]$listeners[0].LocalAddress -cne "127.0.0.1") {
        throw "M4_FAIL_CLOSED_GATEWAY_LISTENER_INVALID"
    }
    $listenerPid = [int]$listeners[0].OwningProcess
    if ($listenerPid -ne $parentPid) {
        $children = @(Get-CimInstance -ClassName Win32_Process `
            -Filter ("ParentProcessId = " + $parentPid) -ErrorAction Stop)
        $listenerChildren = @($children | Where-Object {
            [int]$_.ProcessId -eq $listenerPid
        })
        if ($listenerChildren.Count -ne 1) {
            throw "M4_FAIL_CLOSED_GATEWAY_LISTENER_PARENT_INVALID"
        }
        [void](Assert-M4FailClosedProcessArguments -ProcessId $listenerPid `
            -ExpectedExecutable $ChildExecutable -ExpectedArguments $ChildArguments `
            -PathArgumentIndices @(0, 4))
    }
    return $parent
}

function Get-M4FailClosedUnauthenticatedStatus {
    $statusCode = 0
    try {
        $request = [Net.HttpWebRequest]::Create(
            "http://127.0.0.1:18190/v1/chat/completions"
        )
        $request.Method = "POST"
        $request.ContentType = "application/json"
        $payload = [Text.Encoding]::UTF8.GetBytes("{}")
        $request.ContentLength = $payload.Length
        $stream = $request.GetRequestStream()
        $stream.Write($payload, 0, $payload.Length)
        $stream.Dispose()
        $response = $request.GetResponse()
        $statusCode = [int]$response.StatusCode
        $response.Dispose()
    }
    catch [Net.WebException] {
        if ($null -ne $_.Exception.Response) {
            $statusCode = [int]$_.Exception.Response.StatusCode
            $_.Exception.Response.Dispose()
        }
    }
    return $statusCode
}

$workspace = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
$python = Resolve-M4FailClosedRegularFile `
    -Path (Join-Path $workspace ".venv\Scripts\python.exe") `
    -Reason "M4_EXISTING_VENV_PYTHON_MISSING"
$pyvenvConfig = Resolve-M4FailClosedRegularFile `
    -Path (Join-Path $workspace ".venv\pyvenv.cfg") `
    -Reason "M4_FAIL_CLOSED_PYVENV_CONFIG_INVALID"
$credentials = Resolve-M4FailClosedRegularFile `
    -Path (Join-Path $workspace "tmp\m4\m4-runtime-secrets-v1\gateway-credentials.env") `
    -Reason "M4_GATEWAY_CREDENTIAL_FILE_MISSING"
$cimLauncher = Resolve-M4FailClosedRegularFile `
    -Path (Join-Path $workspace "scripts\m4\Launch-M4HostRuntime.ps1") `
    -Reason "M4_HOST_RUNTIME_CIM_LAUNCHER_MISSING"
$evidenceArchiver = Resolve-M4FailClosedRegularFile `
    -Path (Join-Path $workspace "scripts\m4\Move-M4GatewayRuntimeEvidence.ps1") `
    -Reason "M4_GATEWAY_EVIDENCE_ARCHIVER_INVALID"
$runtimeDirectoryCandidate = [IO.Path]::GetFullPath(
    (Join-Path $workspace "tmp\m4\gateway")
)
if (-not (Test-Path -LiteralPath $runtimeDirectoryCandidate)) {
    [void][IO.Directory]::CreateDirectory($runtimeDirectoryCandidate)
}
$runtimeDirectory = Resolve-M4FailClosedRegularDirectory `
    -Path $runtimeDirectoryCandidate `
    -Reason "M4_FAIL_CLOSED_GATEWAY_RUNTIME_DIRECTORY_INVALID"
$pidPath = Join-Path $runtimeDirectory "gateway.pid"
$stdoutPath = Join-Path $runtimeDirectory "gateway.stdout.log"
$stderrPath = Join-Path $runtimeDirectory "gateway.stderr.log"

$basePythonCandidates = @()
foreach ($line in [IO.File]::ReadAllLines($pyvenvConfig)) {
    if ($line.StartsWith("executable = ", [StringComparison]::Ordinal)) {
        $basePythonCandidates += $line.Substring("executable = ".Length)
    }
}
if ($basePythonCandidates.Count -ne 1) {
    throw "M4_FAIL_CLOSED_PYVENV_EXECUTABLE_INVALID"
}
$basePython = Resolve-M4FailClosedRegularFile -Path $basePythonCandidates[0] `
    -Reason "M4_FAIL_CLOSED_BASE_PYTHON_INVALID"
$parentArguments = @(
    $python,
    "-m",
    "awakening.model_gateway.m4.fail_closed_runtime",
    "--credentials",
    $credentials,
    "--host",
    "127.0.0.1",
    "--port",
    "18190"
)
$childArguments = @($basePython) + @($parentArguments[1..($parentArguments.Count - 1)])

if ($ValidateExisting) {
    [void](Resolve-M4FailClosedRegularFile -Path $stdoutPath `
        -Reason "M4_FAIL_CLOSED_GATEWAY_STDOUT_FILE_INVALID")
    [void](Resolve-M4FailClosedRegularFile -Path $stderrPath `
        -Reason "M4_FAIL_CLOSED_GATEWAY_STDERR_FILE_INVALID")
    [void](Assert-M4FailClosedRuntimeIdentity -PidPath $pidPath `
        -ParentExecutable $python -ParentArguments $parentArguments `
        -ChildExecutable $basePython -ChildArguments $childArguments)
    if ((Get-M4FailClosedUnauthenticatedStatus) -ne 401) {
        throw "M4_FAIL_CLOSED_GATEWAY_EXISTING_UNAUTHENTICATED_STATUS_INVALID"
    }
    Write-Output "M4_FAIL_CLOSED_GATEWAY_START=NOT_REQUIRED"
    Write-Output "M4_FAIL_CLOSED_GATEWAY_VALIDATE_EXISTING=PASS"
    Write-Output "M4_FAIL_CLOSED_GATEWAY_IDENTITY=PASS"
    Write-Output "M4_FAIL_CLOSED_GATEWAY_LOOPBACK=127.0.0.1:18190"
    Write-Output "M4_FAIL_CLOSED_GATEWAY_UNAUTHENTICATED_STATUS=401"
    Write-Output "M4_FAIL_CLOSED_GATEWAY_PROVIDER_CONFIGURED=false"
    Write-Output ("M4_FAIL_CLOSED_GATEWAY_WINDOW_ID=" + $WindowId.ToString("D").ToLowerInvariant())
    return
}

$rolloverOutput = @(& $evidenceArchiver -RuntimeKind FailClosed `
    -WindowId $WindowId -Phase "pre-fail-closed-start")
if ($rolloverOutput -cnotcontains "M4_GATEWAY_EVIDENCE_ARCHIVE=PASS" -and
    $rolloverOutput -cnotcontains "M4_GATEWAY_EVIDENCE_ARCHIVE=NOT_REQUIRED") {
    throw "M4_FAIL_CLOSED_GATEWAY_EVIDENCE_ARCHIVE_INVALID"
}
foreach ($target in @($pidPath, $stdoutPath, $stderrPath)) {
    if (Test-Path -LiteralPath $target) {
        throw "M4_FAIL_CLOSED_GATEWAY_RUNTIME_TARGET_ALREADY_EXISTS"
    }
}

$process = $null
if ($LaunchViaCim) {
    $powershell = Join-Path $PSHOME "powershell.exe"
    if (-not (Test-Path -LiteralPath $powershell -PathType Leaf)) {
        throw "M4_HOST_RUNTIME_POWERSHELL_MISSING"
    }
    $command = "& '" + $cimLauncher.Replace("'", "''") + "' -Runtime FailClosedGateway"
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
    $commandLine = '"' + $powershell + '" -NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand ' + $encoded
    $created = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
        CommandLine = $commandLine
        CurrentDirectory = $workspace
    }
    if ([int]$created.ReturnValue -ne 0) {
        throw ("M4_HOST_RUNTIME_CIM_CREATE_FAILED:" + [int]$created.ReturnValue)
    }
    $pidDeadline = [DateTime]::UtcNow.AddSeconds(10)
    do {
        if ((Test-Path -LiteralPath $pidPath -PathType Leaf) -and
            (Get-Item -LiteralPath $pidPath -Force).Length -gt 0) {
            break
        }
        Start-Sleep -Milliseconds 200
    } while ([DateTime]::UtcNow -lt $pidDeadline)
    if (-not (Test-Path -LiteralPath $pidPath -PathType Leaf)) {
        throw "M4_HOST_RUNTIME_CIM_PID_MISSING"
    }
    $pidText = [IO.File]::ReadAllText($pidPath).Trim()
    if ($pidText -notmatch '^[1-9][0-9]{0,9}$') {
        throw "M4_HOST_RUNTIME_CIM_PID_INVALID"
    }
    $process = Get-Process -Id ([int]$pidText) -ErrorAction Stop
}
else {
    $providerNames = @(
        "HICLAW_LLM_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "DASHSCOPE_API_KEY"
    )
    $saved = @{}
    foreach ($name in $providerNames) {
        $saved[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
        [Environment]::SetEnvironmentVariable($name, $null, "Process")
    }
    $oldPythonPath = [Environment]::GetEnvironmentVariable("PYTHONPATH", "Process")
    try {
        [Environment]::SetEnvironmentVariable(
            "PYTHONPATH",
            (Join-Path $workspace "src"),
            "Process"
        )
        $process = Start-Process -FilePath $python -ArgumentList @(
            "-m",
            "awakening.model_gateway.m4.fail_closed_runtime",
            "--credentials", $credentials,
            "--host", "127.0.0.1",
            "--port", "18190"
        ) -WorkingDirectory $workspace -WindowStyle Hidden `
          -RedirectStandardOutput $stdoutPath `
          -RedirectStandardError $stderrPath -PassThru
    }
    finally {
        [Environment]::SetEnvironmentVariable("PYTHONPATH", $oldPythonPath, "Process")
        foreach ($name in $providerNames) {
            [Environment]::SetEnvironmentVariable($name, $saved[$name], "Process")
        }
    }
    $pidBytes = [Text.Encoding]::UTF8.GetBytes([string]$process.Id)
    $pidStream = [IO.File]::Open(
        $pidPath,
        [IO.FileMode]::CreateNew,
        [IO.FileAccess]::Write,
        [IO.FileShare]::None
    )
    try {
        $pidStream.Write($pidBytes, 0, $pidBytes.Length)
        $pidStream.Flush()
    }
    finally {
        $pidStream.Dispose()
    }
}

$deadline = [DateTime]::UtcNow.AddSeconds($ReadyTimeoutSeconds)
$statusCode = 0
do {
    if ($process.HasExited) {
        throw ("M4_FAIL_CLOSED_GATEWAY_EXITED:" + $process.ExitCode)
    }
    $statusCode = Get-M4FailClosedUnauthenticatedStatus
    if ($statusCode -eq 401) {
        break
    }
    Start-Sleep -Milliseconds 250
} while ([DateTime]::UtcNow -lt $deadline)
if ($statusCode -ne 401) {
    throw ("M4_FAIL_CLOSED_GATEWAY_NOT_READY:" + $statusCode)
}
[void](Assert-M4FailClosedRuntimeIdentity -PidPath $pidPath `
    -ParentExecutable $python -ParentArguments $parentArguments `
    -ChildExecutable $basePython -ChildArguments $childArguments)

Write-Output "M4_FAIL_CLOSED_GATEWAY_START=PASS"
Write-Output "M4_FAIL_CLOSED_GATEWAY_IDENTITY=PASS"
Write-Output "M4_FAIL_CLOSED_GATEWAY_LOOPBACK=127.0.0.1:18190"
Write-Output "M4_FAIL_CLOSED_GATEWAY_UNAUTHENTICATED_STATUS=401"
Write-Output "M4_FAIL_CLOSED_GATEWAY_PROVIDER_CONFIGURED=false"
Write-Output ("M4_FAIL_CLOSED_GATEWAY_LAUNCH_MODE=" + $(if ($LaunchViaCim) { "cim_detached" } else { "direct" }))
Write-Output ("M4_FAIL_CLOSED_GATEWAY_WINDOW_ID=" + $WindowId.ToString("D").ToLowerInvariant())
foreach ($line in $rolloverOutput) {
    Write-Output ([string]$line)
}
if ($KeepSessionOpen) {
    Write-Output "M4_FAIL_CLOSED_GATEWAY_SESSION_PINNED=true"
    $process.WaitForExit()
}
