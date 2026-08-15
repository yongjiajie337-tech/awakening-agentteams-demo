#requires -Version 5.1

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$LifecycleScript,
    [Parameter(Mandatory = $true)][string]$FixtureRoot
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-LifecycleFunctionText {
    param([Parameter(Mandatory = $true)][string]$Name)

    $tokens = $null
    $errors = $null
    $ast = [Management.Automation.Language.Parser]::ParseFile(
        $LifecycleScript,
        [ref]$tokens,
        [ref]$errors
    )
    if (@($errors).Count -ne 0) {
        throw "ACL_FIXTURE_LIFECYCLE_PARSE_FAILED"
    }
    $matches = @($ast.FindAll({
        param($node)
        $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
            $node.Name -ceq $Name
    }, $true))
    if ($matches.Count -ne 1) {
        throw ("ACL_FIXTURE_FUNCTION_NOT_EXACT:" + $Name)
    }
    return [string]$matches[0].Extent.Text
}

function Add-FixtureRule {
    param(
        [Parameter(Mandatory = $true)]$Acl,
        [Parameter(Mandatory = $true)]
        [System.Security.Principal.SecurityIdentifier]$Sid,
        [Parameter(Mandatory = $true)]
        [System.Security.AccessControl.FileSystemRights]$Rights,
        [System.Security.AccessControl.AccessControlType]$Type =
            [System.Security.AccessControl.AccessControlType]::Allow,
        [System.Security.AccessControl.InheritanceFlags]$InheritanceFlags =
            [System.Security.AccessControl.InheritanceFlags]::None
    )

    $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
        $Sid,
        $Rights,
        $InheritanceFlags,
        [System.Security.AccessControl.PropagationFlags]::None,
        $Type
    )
    [void]$Acl.AddAccessRule($rule)
}

function New-FixtureDirectory {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)]
        [ValidateSet("tight", "reader", "broad", "writer")]
        [string]$Variant
    )

    $path = Join-Path $FixtureRoot $Name
    [IO.Directory]::CreateDirectory($path) | Out-Null
    $currentUserSid = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $systemSid = New-Object Security.Principal.SecurityIdentifier("S-1-5-18")
    $administratorsSid = New-Object Security.Principal.SecurityIdentifier("S-1-5-32-544")
    $networkServiceSid = New-Object Security.Principal.SecurityIdentifier("S-1-5-20")
    $everyoneSid = New-Object Security.Principal.SecurityIdentifier("S-1-1-0")
    $inheritance = (
        [Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
        [Security.AccessControl.InheritanceFlags]::ObjectInherit
    )

    $acl = New-Object Security.AccessControl.DirectorySecurity
    $acl.SetOwner($currentUserSid)
    $acl.SetAccessRuleProtection($true, $false)
    Add-FixtureRule -Acl $acl -Sid $currentUserSid -Rights FullControl `
        -InheritanceFlags $inheritance
    Add-FixtureRule -Acl $acl -Sid $systemSid -Rights FullControl `
        -InheritanceFlags $inheritance
    Add-FixtureRule -Acl $acl -Sid $administratorsSid -Rights FullControl `
        -InheritanceFlags $inheritance
    $readerRights = (
        [Security.AccessControl.FileSystemRights]::Read -bor
        [Security.AccessControl.FileSystemRights]::Synchronize
    )
    if ($Variant -ceq "reader") {
        Add-FixtureRule -Acl $acl -Sid $networkServiceSid -Rights $readerRights `
            -InheritanceFlags $inheritance
    }
    elseif ($Variant -ceq "broad") {
        Add-FixtureRule -Acl $acl -Sid $everyoneSid -Rights $readerRights `
            -InheritanceFlags $inheritance
    }
    elseif ($Variant -ceq "writer") {
        Add-FixtureRule -Acl $acl -Sid $networkServiceSid -Rights (
            $readerRights -bor [Security.AccessControl.FileSystemRights]::CreateFiles
        ) -InheritanceFlags $inheritance
    }
    [IO.Directory]::SetAccessControl($path, $acl)
    return $path
}

function New-FixtureFile {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)]
        [ValidateSet("tight", "reader", "broad", "writer", "two-readers", "missing-admin")]
        [string]$Variant
    )

    $path = Join-Path $FixtureRoot ($Name + ".env")
    [IO.File]::WriteAllText(
        $path,
        "ACL_FIXTURE_ONLY=public-placeholder`n",
        (New-Object Text.UTF8Encoding($false))
    )
    $currentUserSid = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $systemSid = New-Object Security.Principal.SecurityIdentifier("S-1-5-18")
    $administratorsSid = New-Object Security.Principal.SecurityIdentifier("S-1-5-32-544")
    $networkServiceSid = New-Object Security.Principal.SecurityIdentifier("S-1-5-20")
    $localServiceSid = New-Object Security.Principal.SecurityIdentifier("S-1-5-19")
    $everyoneSid = New-Object Security.Principal.SecurityIdentifier("S-1-1-0")

    $acl = New-Object Security.AccessControl.FileSecurity
    $acl.SetOwner($currentUserSid)
    $acl.SetAccessRuleProtection($true, $false)
    Add-FixtureRule -Acl $acl -Sid $currentUserSid -Rights FullControl
    Add-FixtureRule -Acl $acl -Sid $systemSid -Rights FullControl
    if ($Variant -cne "missing-admin") {
        Add-FixtureRule -Acl $acl -Sid $administratorsSid -Rights FullControl
    }
    $readerRights = (
        [Security.AccessControl.FileSystemRights]::Read -bor
        [Security.AccessControl.FileSystemRights]::Synchronize
    )
    if ($Variant -ceq "reader") {
        Add-FixtureRule -Acl $acl -Sid $networkServiceSid -Rights $readerRights
    }
    elseif ($Variant -ceq "broad") {
        Add-FixtureRule -Acl $acl -Sid $everyoneSid -Rights $readerRights
    }
    elseif ($Variant -ceq "writer") {
        Add-FixtureRule -Acl $acl -Sid $networkServiceSid -Rights (
            $readerRights -bor [Security.AccessControl.FileSystemRights]::WriteData
        )
    }
    elseif ($Variant -ceq "two-readers") {
        Add-FixtureRule -Acl $acl -Sid $networkServiceSid -Rights $readerRights
        Add-FixtureRule -Acl $acl -Sid $localServiceSid -Rights $readerRights
    }
    [IO.File]::SetAccessControl($path, $acl)
    return $path
}

function Assert-FixtureCase {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Variant,
        [Parameter(Mandatory = $true)][bool]$ExpectedPass,
        [Parameter(Mandatory = $true)][int]$ExpectedRules,
        [Parameter(Mandatory = $true)][int]$ExpectedReaders
    )

    $path = New-FixtureFile -Name $Name -Variant $Variant
    try {
        $result = Assert-DemoProviderSecretAcl -Path $path `
            -CurrentUserSid ([Security.Principal.WindowsIdentity]::GetCurrent().User)
        if (-not $ExpectedPass -or [int]$result.rule_count -ne $ExpectedRules -or
            [int]$result.restricted_reader_count -ne $ExpectedReaders) {
            throw ("ACL_FIXTURE_UNEXPECTED_PASS:" + $Name)
        }
        Write-Output ("ACL_FIXTURE_CASE=" + $Name + ":PASS")
    }
    catch {
        if ($ExpectedPass -or $_.Exception.Message -cne
            "DEMO_PROVIDER_SECRET_ACL_METADATA_INVALID") {
            throw
        }
        Write-Output ("ACL_FIXTURE_CASE=" + $Name + ":REJECTED")
    }
}

function Assert-DirectoryFixtureCase {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Variant,
        [Parameter(Mandatory = $true)][bool]$ExpectedPass,
        [Parameter(Mandatory = $true)][int]$ExpectedRules,
        [Parameter(Mandatory = $true)][int]$ExpectedReaders
    )

    $path = New-FixtureDirectory -Name $Name -Variant $Variant
    try {
        $result = Assert-DemoProviderSecretAcl -Path $path `
            -CurrentUserSid ([Security.Principal.WindowsIdentity]::GetCurrent().User) `
            -Directory
        if (-not $ExpectedPass -or [int]$result.rule_count -ne $ExpectedRules -or
            [int]$result.restricted_reader_count -ne $ExpectedReaders) {
            throw ("ACL_FIXTURE_UNEXPECTED_DIRECTORY_PASS:" + $Name)
        }
        Write-Output ("ACL_FIXTURE_DIRECTORY_CASE=" + $Name + ":PASS")
    }
    catch {
        if ($ExpectedPass -or $_.Exception.Message -cne
            "DEMO_PROVIDER_SECRET_ACL_METADATA_INVALID") {
            throw
        }
        Write-Output ("ACL_FIXTURE_DIRECTORY_CASE=" + $Name + ":REJECTED")
    }
}

function Assert-JunctionFixture {
    $workspace = Join-Path $FixtureRoot "junction-workspace"
    $target = Join-Path $FixtureRoot "junction-target"
    [IO.Directory]::CreateDirectory($workspace) | Out-Null
    [IO.Directory]::CreateDirectory($target) | Out-Null
    $junction = Join-Path $workspace ".secrets"
    $output = @(& cmd.exe /d /c mklink /J $junction $target 2>&1)
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $junction)) {
        Write-Output "ACL_FIXTURE_PARENT_JUNCTION=SKIP"
        return
    }
    try {
        try {
            [void](Assert-DemoProviderSecretDirectory -Path $junction `
                -ExpectedPath $junction `
                -CurrentUserSid ([Security.Principal.WindowsIdentity]::GetCurrent().User))
            throw "ACL_FIXTURE_PARENT_JUNCTION_UNEXPECTED_PASS"
        }
        catch {
            if ($_.Exception.Message -cne
                "DEMO_PROVIDER_SECRET_PARENT_DIRECTORY_INVALID") {
                throw
            }
            Write-Output "ACL_FIXTURE_PARENT_JUNCTION=REJECTED"
        }
    }
    finally {
        if (Test-Path -LiteralPath $junction) {
            [IO.Directory]::Delete($junction)
        }
    }
}

Invoke-Expression (Get-LifecycleFunctionText -Name "Assert-RegularDirectory")
Invoke-Expression (Get-LifecycleFunctionText -Name "Get-DemoNormalizedFileRights")
Invoke-Expression (Get-LifecycleFunctionText -Name "Assert-DemoProviderSecretAcl")
Invoke-Expression (Get-LifecycleFunctionText -Name "Assert-DemoProviderSecretDirectory")

Assert-FixtureCase -Name "tight" -Variant "tight" -ExpectedPass $true `
    -ExpectedRules 3 -ExpectedReaders 0
Assert-FixtureCase -Name "restricted-reader" -Variant "reader" -ExpectedPass $true `
    -ExpectedRules 4 -ExpectedReaders 1
Assert-FixtureCase -Name "broad-principal" -Variant "broad" -ExpectedPass $false `
    -ExpectedRules 0 -ExpectedReaders 0
Assert-FixtureCase -Name "writer" -Variant "writer" -ExpectedPass $false `
    -ExpectedRules 0 -ExpectedReaders 0
Assert-FixtureCase -Name "two-readers" -Variant "two-readers" -ExpectedPass $false `
    -ExpectedRules 0 -ExpectedReaders 0
Assert-FixtureCase -Name "missing-admin" -Variant "missing-admin" -ExpectedPass $false `
    -ExpectedRules 0 -ExpectedReaders 0

Assert-DirectoryFixtureCase -Name "directory-tight" -Variant "tight" `
    -ExpectedPass $true -ExpectedRules 3 -ExpectedReaders 0
Assert-DirectoryFixtureCase -Name "directory-restricted-reader" -Variant "reader" `
    -ExpectedPass $true -ExpectedRules 4 -ExpectedReaders 1
Assert-DirectoryFixtureCase -Name "directory-broad-principal" -Variant "broad" `
    -ExpectedPass $false -ExpectedRules 0 -ExpectedReaders 0
Assert-DirectoryFixtureCase -Name "directory-writer" -Variant "writer" `
    -ExpectedPass $false -ExpectedRules 0 -ExpectedReaders 0
Assert-JunctionFixture

Write-Output "ACL_FIXTURE_SECRET_VALUE_READ=false"
Write-Output "ACL_FIXTURE_PROVIDER_CALLED=false"
