#requires -Version 5.1
# Self-contained, offline fixtures: no Pester, downloads or installed app needed.
# Run separately in Windows PowerShell 5.1 AND PowerShell 7 on native Windows.
param([Parameter(Mandatory = $true)][string] $PythonExecutable)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0
$installer = Join-Path $PSScriptRoot '..\..\scripts\install.ps1'
$tokens = $null
$errors = $null
$null = [Management.Automation.Language.Parser]::ParseFile(
    $installer, [ref]$tokens, [ref]$errors)
if ($errors.Count -ne 0) { throw ($errors | Out-String) }
. $installer

function Assert-Equal($Actual, $Expected) {
    if ($Actual -cne $Expected) { throw "Fixture assertion failed: expected [$Expected], got [$Actual]." }
}
function Assert-Refused([scriptblock] $Action, [string] $Pattern) {
    try { & $Action } catch {
        if ($_.Exception.Message -notmatch $Pattern) { throw }
        return
    }
    throw "Fixture expected refusal matching $Pattern."
}
function Assert-SharingRefused([scriptblock] $Action) {
    try { & $Action } catch {
        $code = $_.Exception.GetBaseException().HResult -band 65535
        if ($code -notin @(5, 32, 33)) { throw }
        return
    }
    throw 'Fixture expected an access/sharing refusal.'
}

$root = New-PrivateInstallDirectory
$fixtureStorage = $script:InstallStorage
try {
    # P3: parse the displayed command as PowerShell source, without executing it.
    $apostrophePath = "C:\Users\O'Brien\space " + [char]0x03B2 + '\amplifier-unified.exe'
    $command = Format-PowerShellLaunchCommand $apostrophePath
    $commandTokens = $null
    $commandErrors = $null
    $commandAst = [Management.Automation.Language.Parser]::ParseInput(
        $command, [ref]$commandTokens, [ref]$commandErrors)
    Assert-Equal $commandErrors.Count 0
    $commands = @($commandAst.FindAll({
        param($node) $node -is [Management.Automation.Language.CommandAst]
    }, $true))
    Assert-Equal $commands.Count 1
    Assert-Equal $commands[0].InvocationOperator ([Management.Automation.Language.TokenKind]::Ampersand)
    Assert-Equal $commands[0].CommandElements.Count 1
    Assert-Equal $commands[0].CommandElements[0].Value $apostrophePath

    # P1: a normal (not reparse) ancestor with foreign mutation must be refused
    # before storage/log/download writes. A safe read/traverse grant is accepted.
    $canary = Join-Path $root 'outside-canary.txt'
    [IO.File]::WriteAllText($canary, 'outside remains unchanged')
    $foreign = New-Object Security.Principal.SecurityIdentifier('S-1-5-21-111111111-222222222-333333333-4242')
    foreach ($rights in @('DeleteSubdirectoriesAndFiles', 'WriteAttributes', 'ChangePermissions',
        'TakeOwnership', 'WriteData', 'WriteExtendedAttributes', 'Delete', 'ReadAndExecute')) {
        $ancestor = Join-Path $root ('ancestor-' + $rights)
        $null = [IO.Directory]::CreateDirectory($ancestor)
        $acl = Get-Acl -LiteralPath $ancestor
        $rule = New-Object Security.AccessControl.FileSystemAccessRule($foreign, $rights, 'Allow')
        $acl.AddAccessRule($rule)
        Set-Acl -LiteralPath $ancestor -AclObject $acl
        if ((Get-Item -LiteralPath $ancestor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw 'Mutation fixture unexpectedly became a reparse point.'
        }
        if ($rights -eq 'ReadAndExecute') {
            $safeContext = New-Object UnifiedBootstrap.InstallStorage($ancestor)
            try { $safeContext.Validate() } finally { $safeContext.Dispose() }
        } else {
            Assert-Refused { $unexpected = New-Object UnifiedBootstrap.InstallStorage($ancestor) } 'Foreign mutation'
            Assert-Equal (@(Get-ChildItem -LiteralPath $ancestor -Force).Count) 0
        }
        Assert-Equal ([IO.File]::ReadAllText($canary)) 'outside remains unchanged'
    }
    $ancestor = Join-Path $root 'pinned-ancestor'
    $null = [IO.Directory]::CreateDirectory($ancestor)
    $pinned = New-Object UnifiedBootstrap.InstallStorage($ancestor)
    try {
        Assert-SharingRefused { [IO.Directory]::Move($ancestor, ($ancestor + '-replacement')) }
        Assert-SharingRefused { [IO.Directory]::Move($pinned.Root, ($pinned.Root + '-replacement')) }
        $pinned.Validate()
    } finally { $pinned.Dispose() }
    # Prove that the sharing refusal, not a permanently broken rename fixture,
    # caused the failure. No live context pins this synthetic ancestor now.
    [IO.Directory]::Move($ancestor, ($ancestor + '-replacement'))
    [IO.Directory]::Move(($ancestor + '-replacement'), $ancestor)
    Assert-Equal ([IO.File]::ReadAllText($canary)) 'outside remains unchanged'

    $name = 'amplifier_unified-0.44.0-py3-none-any.whl'
    $hash = 'a' * 64
    Assert-Equal (Get-ExactChecksum "$hash  $name`n" $name) $hash
    Assert-Equal (Get-ExactChecksum "$hash *$name`r`n" $name) $hash
    Assert-Refused { Get-ExactChecksum "$hash  $name`n$hash  $name" $name } 'duplicate'
    Assert-Refused { Get-ExactChecksum "$hash  not-$name" $name } 'Missing'
    Assert-Refused { Get-ExactChecksum "broken  $name" $name } 'Malformed'
    Assert-Refused { Get-ExactChecksum "$hash  ../$name" $name } 'Malformed'
    $body = Join-Path $root 'body'
    [IO.File]::WriteAllText($body, 'unchanged')
    Assert-Refused { Assert-FileChecksum $body ('0' * 64) } 'mismatch'
    Assert-FileChecksum $body (Get-FileHash -LiteralPath $body -Algorithm SHA256).Hash.ToLowerInvariant()
    Assert-SharingRefused { [IO.File]::AppendAllText($body, 'must not reach hashed bytes') }
    Assert-SharingRefused { [IO.File]::Move($body, ($body + '-replacement')) }
    Assert-Equal ([IO.File]::ReadAllText($body)) 'unchanged'

    # Real Windows extraction, including Unicode/space paths and zip-slip cases.
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    foreach ($entryName in @('ok.txt', '../escape.txt', '/absolute.txt', 'C:/drive.txt',
        'folder/file:stream', 'CON.txt', 'folder/trailing.')) {
        $archivePath = Join-Path $root ([Guid]::NewGuid().ToString('N') + '.zip')
        $archive = [IO.Compression.ZipFile]::Open($archivePath, [IO.Compression.ZipArchiveMode]::Create)
        try {
            $entry = $archive.CreateEntry($entryName)
            $writer = New-Object IO.StreamWriter($entry.Open())
            try { $writer.Write('fixture') } finally { $writer.Dispose() }
        } finally { $archive.Dispose() }
        $destination = Join-Path $root ('space ' + [char]0x03B2 + ' ' + [Guid]::NewGuid().ToString('N'))
        if ($entryName -eq 'ok.txt') {
            Expand-ContainedZip $archivePath $destination
            Assert-Equal ([IO.File]::ReadAllText((Join-Path $destination 'ok.txt'))) 'fixture'
            Assert-Refused { Expand-ContainedZip $archivePath $destination } 'already exists'
            Assert-SharingRefused { [IO.Directory]::Move($destination, ($destination + '-replacement')) }
            $fixtureStorage.Validate()
        } else {
            Assert-Refused { Expand-ContainedZip $archivePath $destination } 'Unsafe|escaped'
        }
    }
    # Exact extraction creation refuses even a pre-existing ordinary safe root.
    $collision = Join-Path $root 'extraction-collision'
    $null = [IO.Directory]::CreateDirectory($collision)
    [IO.File]::WriteAllText((Join-Path $collision 'keep.txt'), 'never adopted')
    Assert-Refused { Expand-ContainedZip $archivePath $collision } 'already exists'
    Assert-Equal ([IO.File]::ReadAllText((Join-Path $collision 'keep.txt'))) 'never adopted'
    $junctionTarget = Join-Path $root 'junction-target'
    $junction = Join-Path $root 'junction'
    $null = [IO.Directory]::CreateDirectory($junctionTarget)
    [IO.File]::WriteAllText((Join-Path $junctionTarget 'canary.txt'), 'junction target unchanged')
    # CPython's native junction fixture requires no symlink privilege or shell.
    & $PythonExecutable -I -S -c 'import _winapi,sys; _winapi.CreateJunction(sys.argv[1],sys.argv[2])' $junctionTarget $junction
    if ($LASTEXITCODE -ne 0) { throw 'Native junction fixture could not be created; not a pass.' }
    try {
        Assert-Refused { $unexpected = New-Object UnifiedBootstrap.InstallStorage($junction) } 'reparse'
        Assert-Refused { Expand-ContainedZip $archivePath $junction } 'already exists'
        Assert-Equal ([IO.File]::ReadAllText((Join-Path $junctionTarget 'canary.txt'))) 'junction target unchanged'
    } finally { [IO.Directory]::Delete($junction) }
    if (Test-Path -LiteralPath (Join-Path $root 'escape.txt')) { throw 'ZIP escaped destination.' }
    foreach ($unsafeKind in @('case-collision', 'symlink')) {
        $archivePath = Join-Path $root "$unsafeKind.zip"
        $archive = [IO.Compression.ZipFile]::Open($archivePath, [IO.Compression.ZipArchiveMode]::Create)
        try {
            if ($unsafeKind -eq 'case-collision') {
                $null = $archive.CreateEntry('same.txt')
                $null = $archive.CreateEntry('SAME.txt')
            } else {
                $entry = $archive.CreateEntry('link')
                $entry.ExternalAttributes = (40960 -shl 16)
            }
        } finally { $archive.Dispose() }
        $destination = Join-Path $root $unsafeKind
        Assert-Refused { Expand-ContainedZip $archivePath $destination } 'Unsafe|duplicated'
        Assert-Equal (@(Get-ChildItem -LiteralPath $destination).Count) 0
    }

    $support = [pscustomobject]@{
        schemaVersion = 1; release = 'v0.44.0'; status = 'qualified'
        os = 'windows-11'; architecture = 'x86_64'; python = 'cpython-3.13'
        powershell = @('5.1', '7'); nativePackages = @('amplifier-core')
        hostConstraints = 'windows-host-constraints.txt'; defaultGraph = 'windows-default-graph.json'
    }
    $null = Read-NativeSupport $support '0.44.0'
    foreach ($field in @('release', 'status', 'os', 'architecture', 'python', 'hostConstraints')) {
        $original = $support.$field
        $support.$field = 'unsupported'
        Assert-Refused { Read-NativeSupport $support '0.44.0' } 'not qualified|Invalid'
        $support.$field = $original
    }
    Assert-Refused { Assert-NativeActivationContract } 'integration is pending'

    # Real child process: Unicode/space cwd, CRT argv quoting, anonymous isolation.
    $script:InstallWork = Join-Path $root ('space ' + [char]0x03B2)
    $fixtureStorage.CreateDirectory($script:InstallWork)
    $script:InstallLog = Join-Path $root 'child.log'
    Write-PrivateInstallText $script:InstallLog '' -Mutable
    $script:InstallStage = 'fixture'
    $script:UseAnonymous = $true
    # An executable shadow in an unsafe PATH-like directory is refused before
    # even a version probe. It need not be a runnable binary for this falsifier.
    $shadow = Join-Path $root 'ancestor-WriteData\shadow.exe'
    [IO.File]::WriteAllText($shadow, 'not executable')
    Assert-Refused { Invoke-InstallChild $shadow @('--version') @{} } 'Foreign mutation'
    $appendShadow = Join-Path $root 'append-shadow.exe'
    [IO.File]::WriteAllText($appendShadow, 'not executable')
    $acl = Get-Acl -LiteralPath $appendShadow
    $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($foreign, 'AppendData', 'Allow')))
    Set-Acl -LiteralPath $appendShadow -AclObject $acl
    Assert-Refused { Invoke-InstallChild $appendShadow @('--version') @{} } 'Foreign mutation'
    Assert-Equal ([IO.File]::ReadAllText($canary)) 'outside remains unchanged'
    # Use the invoking test interpreter to test CRT argv directly. Invoking
    # powershell.exe -File would add that command's separate argument parser.
    $probe = 'import json,os,sys; print(json.dumps(dict(values=sys.argv[1:],globalConfig=os.getenv("GIT_CONFIG_GLOBAL"),anonymous=os.getenv("AMPLIFIER_GITHUB_ACCESS"),tokenAbsent="GH_TOKEN" not in os.environ)))'
    $beforePath = $env:PATH
    $beforeToken = $env:GH_TOKEN
    $beforeConfig = $env:GIT_CONFIG_GLOBAL
    try {
        $env:GH_TOKEN = 'synthetic-fixture-token'
        $env:GIT_CONFIG_GLOBAL = 'synthetic-user-config'
        $values = @('space value', ('unicode ' + [char]0x03B2), 'quote"value', 'trailing\', '')
        $result = Invoke-InstallChild $PythonExecutable (@('-I', '-S', '-c', $probe) + $values) @{}
        $observed = $result | ConvertFrom-Json
        Assert-Equal $observed.globalConfig 'NUL'
        Assert-Equal $observed.anonymous 'anonymous'
        Assert-Equal $observed.tokenAbsent $true
        Assert-Equal $env:GH_TOKEN 'synthetic-fixture-token'
        Assert-Equal $env:GIT_CONFIG_GLOBAL 'synthetic-user-config'
        Assert-Equal $env:PATH $beforePath
        Assert-Equal (@($observed.values).Count) $values.Count
        for ($i = 0; $i -lt $values.Count; $i++) { Assert-Equal $observed.values[$i] $values[$i] }
    } finally {
        $env:GH_TOKEN = $beforeToken
        $env:GIT_CONFIG_GLOBAL = $beforeConfig
    }
    if ([IO.File]::ReadAllText($script:InstallLog) -match 'synthetic-fixture-token') {
        throw 'Child credential reached log.'
    }

    # Atomic creation/owner DACL is a native check, not a POSIX chmod assertion.
    $privateStorage = New-Object UnifiedBootstrap.InstallStorage($root)
    $private = $privateStorage.Root
    try {
        $acl = Get-Acl -LiteralPath $private
        Assert-Equal $acl.AreAccessRulesProtected $true
        $owner = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
        foreach ($rule in $acl.Access) {
            $sid = $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
            if ($sid -notin @($owner, 'S-1-5-18')) { throw 'Unexpected private-directory trustee.' }
        }
    } finally {
        $privateStorage.Dispose()
        Remove-Item -LiteralPath $private -Recurse -Force
    }

    # Execute actual orchestration with fixture downloads, WITHOUT helpers,
    # app installation, service registration, browser access or network.
    function Assert-NativePlatform {}
    function New-PrivateInstallDirectory {
        $script:InstallStorage = New-Object UnifiedBootstrap.InstallStorage($root)
        return $script:InstallStorage.Root
    }
    function Find-InstallHelper { throw 'FIXTURE: helper must not run' }
    $script:Downloads = New-Object 'Collections.Generic.List[string]'
    function Get-BoundedDownload([string] $Url, [string] $Destination, [long] $MaxBytes) {
        $script:Downloads.Add($Url)
        Write-InstallReceipt $Destination @{
            draft = $false; prerelease = $false; tag_name = 'v0.44.0'
            assets = @(@{name = 'SHA256SUMS'}, @{name = 'amplifier_unified-0.44.0-py3-none-any.whl'})
        }
    }
    Assert-Refused { Invoke-UnifiedInstall 'latest' $true $true } 'Release v0.44.0 has no native Windows support record'
    Assert-Equal $script:Downloads.Count 1
    Assert-Equal $script:Downloads[0] 'https://api.github.com/repos/microsoft/amplifier-unified/releases/latest'
    $script:Downloads.Clear()
    Assert-Refused { Invoke-UnifiedInstall '0.44.0' $true $false } 'Release v0.44.0 has no native Windows support record'
    Assert-Equal $script:Downloads[0] 'https://api.github.com/repos/microsoft/amplifier-unified/releases/tags/v0.44.0'

    # A publisher record alone cannot open the unintegrated activation seam.
    # All content is synthetic; hash validation and orchestration are real.
    $script:FixtureBodies = @{}
    $script:FixtureBodies['native-windows-support.json'] = ConvertTo-Json $support -Depth 8
    $script:FixtureBodies[$name] = 'synthetic wheel'
    $script:FixtureBodies['windows-host-constraints.txt'] = 'amplifier-core==2.0.2'
    $script:FixtureBodies['windows-default-graph.json'] = '{"fixture":true}'
    $lines = @()
    foreach ($asset in $script:FixtureBodies.Keys) {
        $sha = [Security.Cryptography.SHA256]::Create()
        try {
            $bytes = [Text.Encoding]::UTF8.GetBytes($script:FixtureBodies[$asset])
            $digest = ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-', '').ToLowerInvariant()
            $lines += "$digest  $asset"
        } finally { $sha.Dispose() }
    }
    $script:FixtureBodies['SHA256SUMS'] = $lines -join "`n"
    $script:BadWheel = $false
    function Get-BoundedDownload([string] $Url, [string] $Destination, [long] $MaxBytes) {
        $script:Downloads.Add($Url)
        if ($Url -match '/releases/(latest|tags/v0\.44\.0)$') {
            Write-InstallReceipt $Destination @{
                draft = $false; prerelease = $false; tag_name = 'v0.44.0'
                assets = @(@{name = 'native-windows-support.json'})
            }
        } else {
            $asset = ([Uri]$Url).Segments[-1]
            $body = $script:FixtureBodies[$asset]
            if ($script:BadWheel -and $asset -eq $name) { $body = 'corrupted fixture' }
            [IO.File]::WriteAllText($Destination, $body, (New-Object Text.UTF8Encoding($false)))
        }
    }
    $script:Downloads.Clear()
    Assert-Refused { Invoke-UnifiedInstall 'latest' $true $true } 'Native activation integration is pending'
    Assert-Equal $script:Downloads.Count 6
    $script:BadWheel = $true
    Assert-Refused { Invoke-UnifiedInstall '0.44.0' $true $true } 'Artifact checksum mismatch'
    Write-Output 'OFFLINE FIXTURE PASS (not clean-machine or native installation qualification)'
} finally {
    $fixtureStorage.Dispose()
    Remove-Item -LiteralPath $root -Recurse -Force
}