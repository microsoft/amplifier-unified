# W8 phase 1. Synthetic tasks only. Never import or run Amplifier.
$ErrorActionPreference = 'Stop'
if ($env:GITHUB_ACTIONS -cne 'true' -or $env:RUNNER_ENVIRONMENT -cne 'github-hosted' -or
    $env:AMPLIFIER_W8_SYNTHETIC_TASKS -cne '1' -or $env:OS -cne 'Windows_NT') {
    throw 'This probe requires its opt-in hosted Windows Actions job.'
}
$id = [guid]::NewGuid().ToString('N')
$prefix = 'AmplifierUnified-W8-Probe-'
$base = Join-Path $env:RUNNER_TEMP "w8-recovery-$id"
$null = New-Item -ItemType Directory -Path $base
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
# Do not inherit broad runner-folder permissions for the probe's data.
& icacls.exe $base /inheritance:r /grant:r "*${sid}:(OI)(CI)F" '*S-1-5-18:(OI)(CI)F'
if ($LASTEXITCODE -ne 0) { throw 'Could not make the probe folder private.' }
$null = New-Item -ItemType File -Path "$base/synthetic-hosted-probe"
$payload = Join-Path $PSScriptRoot 'windows_recovery_payload.py'
$python = (Get-Command python.exe -CommandType Application | Select-Object -First 1).Source
$pythonw = Join-Path (Split-Path $python) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonw)) { throw 'Windowless Python is absent.' }
$scheduler = New-Object -ComObject Schedule.Service
$scheduler.Connect()
$root = $scheduler.GetFolder('\')
$tasks = @{}
$facts = [Collections.Generic.List[object]]::new()
$handles = @{}
$seen = @{}
$trace = [Collections.Generic.List[object]]::new()
$begin = Get-Date
$clock = [Diagnostics.Stopwatch]::StartNew()
$channel = 'Microsoft-Windows-TaskScheduler/Operational'
$historyWasEnabled = $null
$traceSource = "W8-$id"

Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class W8Native {
    [DllImport("kernel32.dll", SetLastError=true)]
    public static extern bool TerminateProcess(IntPtr process, uint code);
}
'@

function Fact($kind, $value) {
    $row = [ordered]@{
        kind = $kind
        seconds = [math]::Round($clock.Elapsed.TotalSeconds, 3)
        pacific = [TimeZoneInfo]::ConvertTimeBySystemTimeZoneId(
            [DateTimeOffset]::UtcNow, 'Pacific Standard Time').ToString('o')
        value = $value
    }
    $facts.Add($row)
    Write-Output ('W8 ' + ($row | ConvertTo-Json -Depth 12 -Compress))
}

function Current($label) {
    $record = $tasks[$label]
    $task = $root.GetTask($record.name)
    if ($task.Xml -cne $record.xml) { throw "Synthetic task changed: $($record.name)" }
    return $task
}

function Snapshot($label, $why) {
    $t = Current $label
    $state = @{
        label = $label; why = $why; state = [int]$t.State
        enabled = [bool]$t.Enabled; instances = [int]$t.GetInstances(0).Count
        lastResult = [long]$t.LastTaskResult; lastRun = $t.LastRunTime.ToString('o')
        nextRun = $t.NextRunTime.ToString('o')
    }
    Fact 'task-state' $state
}

function Enable($label, $enabled) {
    $t = Current $label
    $t.Enabled = $enabled
    $tasks[$label].xml = $root.GetTask($tasks[$label].name).Xml
    Fact 'enabled' @{ label = $label; requested = $enabled }
    Snapshot $label 'enabled-readback'
}

function Create($label, $mode, $repeatMinutes, $restart) {
    $name = $prefix + $id + '-' + $label
    try {
        $null = $root.GetTask($name)
        throw 'Random synthetic task name already exists.'
    } catch {
        $e = $_.Exception
        while ($null -ne $e.InnerException) { $e = $e.InnerException }
        if ($e.HResult -ne -2147024894) { throw }
    }
    # Same principal, policy and settings as the existing product task.
    $definition = $scheduler.NewTask(0)
    $definition.RegistrationInfo.Author = $sid
    $definition.RegistrationInfo.Description = "W8 synthetic $id $label"
    $definition.RegistrationInfo.URI = '\' + $name
    $definition.Principal.Id = 'Owner'
    $definition.Principal.UserId = $sid
    $definition.Principal.LogonType = 3 # InteractiveToken, not S4U or password.
    $definition.Principal.RunLevel = 0
    $logon = $definition.Triggers.Create(9)
    $logon.UserId = $sid
    $logon.Enabled = $true
    if ($repeatMinutes -gt 0) {
        $timer = $definition.Triggers.Create(1)
        $timer.Enabled = $true
        $timer.StartBoundary = [DateTimeOffset]::Now.AddSeconds(30).ToString("yyyy-MM-dd'T'HH:mm:sszzz")
        $timer.Repetition.Interval = "PT$($repeatMinutes)M"
        # No Duration and no EndBoundary: indefinite repetition.
        $timer.Repetition.StopAtDurationEnd = $false
    }
    $settings = $definition.Settings
    $settings.MultipleInstances = 2 # IgnoreNew
    $settings.DisallowStartIfOnBatteries = $false
    $settings.StopIfGoingOnBatteries = $false
    $settings.AllowHardTerminate = $true
    $settings.StartWhenAvailable = $false
    $settings.RunOnlyIfNetworkAvailable = $false
    $settings.AllowDemandStart = $true
    $settings.Enabled = $true
    $settings.Hidden = $false
    $settings.RunOnlyIfIdle = $false
    $settings.WakeToRun = $false
    $settings.ExecutionTimeLimit = 'PT0S'
    $settings.Priority = 7
    $settings.IdleSettings.StopOnIdleEnd = $false
    $settings.IdleSettings.RestartOnIdle = $false
    if ($restart) { $settings.RestartInterval = 'PT1M'; $settings.RestartCount = 3 }
    $action = $definition.Actions.Create(0)
    $action.Path = $pythonw
    $action.Arguments = "-I -S -B `"$payload`" `"$base`" $label $mode"
    $action.WorkingDirectory = $base
    $definition.Actions.Context = 'Owner'
    $submitted = $definition.XmlText
    $submitted | Set-Content -LiteralPath "$base/$label-submitted.xml" -Encoding utf8
    # TASK_CREATE only. Never replace an existing task.
    $t = $root.RegisterTask($name, $submitted, 2, $sid, $null, 3, $null)
    $tasks[$label] = @{ name = $name; xml = $t.Xml }
    $t.Xml | Set-Content -LiteralPath "$base/$label-export.xml" -Encoding utf8
    Fact 'created' @{ label = $label; name = $name; repeatMinutes = $repeatMinutes; restart = $restart }
    if ($label -eq 'ignore') {
        Fact 'submitted-xml' $submitted
        Fact 'exported-xml' $t.Xml
        $ns = [Xml.XmlNamespaceManager]::new(([xml]$t.Xml).NameTable)
        $ns.AddNamespace('t', 'http://schemas.microsoft.com/windows/2004/02/mit/task')
        $xml = [xml]$t.Xml
        $nodes = @($xml.SelectNodes('/t:Task/t:Triggers/*', $ns))
        $timeNode = $xml.SelectSingleNode('/t:Task/t:Triggers/t:TimeTrigger', $ns)
        Fact 'roundtrip' @{
            triggerCount = $nodes.Count; triggerNames = @($nodes | ForEach-Object LocalName)
            timeTrigger = $timeNode.OuterXml
            interval = $timeNode.Repetition.Interval
            duration = [string]$timeNode.Repetition.Duration
            endBoundary = [string]$timeNode.EndBoundary
            stopAtDurationEnd = [string]$timeNode.Repetition.StopAtDurationEnd
            submittedBoundary = [string]$timer.StartBoundary
            exportedBoundary = [string]$timeNode.StartBoundary
            byteEqual = $submitted -ceq $t.Xml
        }
        if ($nodes.Count -ne 2 -or $timeNode.Repetition.Interval -ne 'PT1M' -or
            $timeNode.Repetition.Duration -or $timeNode.EndBoundary) {
            throw 'The indefinite trigger did not survive registration.'
        }
    }
    $null = $t.Run($null)
    Snapshot $label 'on-demand-request'
}

function Read-Payloads {
    foreach ($file in Get-ChildItem -LiteralPath $base -Filter '*.json') {
        if ($seen.ContainsKey($file.Name)) { continue }
        # A new file may still be in its first write. Do not accept a partial read.
        try { $record = Get-Content -LiteralPath $file.FullName -Raw | ConvertFrom-Json }
        catch { continue }
        if (-not $record.role) { continue }
        $seen[$file.Name] = $true
        Fact 'payload' $record
        if ($record.role -in @('action', 'child')) {
            try {
                $p = [Diagnostics.Process]::GetProcessById([int]$record.pid)
                # Retain the native handle before checking creation time.
                $null = $p.Handle
                if ($p.StartTime.ToFileTimeUtc().ToString() -cne $record.birth) {
                    throw 'Payload PID was reused.'
                }
                $handles[$file.Name] = @{ process = $p; record = $record; reported = $false }
            } catch { Fact 'handle-unavailable' @{ label = $record.label; role = $record.role; error = $_.Exception.Message } }
        }
    }
    foreach ($item in $handles.Values) {
        if (-not $item.reported -and $item.process.HasExited) {
            $item.reported = $true
            Fact 'native-exit' @{
                label = $item.record.label; role = $item.record.role
                pid = $item.record.pid; code = $item.process.ExitCode
            }
        }
    }
}

function End-Owned($label, $role, $method) {
    $items = @($handles.Values | Where-Object {
        $_.record.label -eq $label -and $_.record.role -eq $role -and -not $_.process.HasExited
    })
    if ($items.Count -ne 1) { throw "Expected one retained $role process of $label; got $($items.Count)" }
    $item = $items[0]
    if ($method -eq 'TerminateProcess-zero') {
        if (-not [W8Native]::TerminateProcess($item.process.Handle, 0)) {
            throw 'TerminateProcess refused the synthetic retained process.'
        }
    } else {
        # The retained handle prevents PID reuse. No /T and no process-name kill.
        & taskkill.exe /F /PID $item.record.pid
        if ($LASTEXITCODE -ne 0) { throw 'taskkill refused the synthetic process.' }
    }
    Fact 'external-end' @{ label = $label; role = $role; pid = $item.record.pid; method = $method }
}

try {
    Fact 'environment' @{
        os = (Get-CimInstance Win32_OperatingSystem | Select-Object Caption, Version, ProductType)
        python = $pythonw; userSid = $sid
        administrator = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
            [Security.Principal.WindowsBuiltInRole]::Administrator)
    }
    & python.exe -I -S -B $payload $base runner direct
    if ($LASTEXITCODE -ne 0) { throw 'Direct token probe failed.' }
    Fact 'sessions' (& qwinsta.exe | Out-String)
    Fact 'power-states' (& powercfg.exe /a | Out-String)
    try {
        $historyWasEnabled = (Get-WinEvent -ListLog $channel).IsEnabled
        if (-not $historyWasEnabled) { & wevtutil.exe sl $channel /e:true }
        Fact 'history' @{ enabledBefore = $historyWasEnabled; enabledNow = (Get-WinEvent -ListLog $channel).IsEnabled }
    } catch { Fact 'history-unavailable' $_.Exception.Message }
    try {
        $null = Register-CimIndicationEvent -Query 'SELECT * FROM Win32_ProcessStartTrace' -SourceIdentifier $traceSource
        Fact 'process-trace' 'Win32_ProcessStartTrace subscribed; no command lines read.'
    } catch { Fact 'process-trace-unavailable' $_.Exception.Message }
    Create ignore long 1 $true
    Create actionzero long 1 $true
    Create childzero long 1 $true
    Create actionforce long 1 $true
    Create disabled long 1 $true
    Create retryonly fail 0 $true
    Create retryrepeat fail 2 $true
    $measure = [Diagnostics.Stopwatch]::StartNew()
    $done = @{}
    $lastSample = -10
    while ($measure.Elapsed.TotalSeconds -lt 350) {
        Read-Payloads
        foreach ($event in @(Get-Event -SourceIdentifier $traceSource -ErrorAction SilentlyContinue)) {
            $p = $event.SourceEventArgs.NewEvent
            $trace.Add(@{ pid = [int]$p.ProcessID; parent = [int]$p.ParentProcessID
                name = [string]$p.ProcessName; session = [int]$p.SessionID; at = [string]$p.TIME_CREATED })
            Remove-Event -EventIdentifier $event.EventIdentifier
        }
        $seconds = $measure.Elapsed.TotalSeconds
        if ($seconds -ge 45 -and -not $done.kill) {
            End-Owned actionzero action TerminateProcess-zero
            End-Owned actionforce action taskkill
            Enable disabled $false
            try {
                $null = (Current disabled).Run($null)
                Fact 'disabled-demand' 'Run returned without an error.'
            } catch {
                Fact 'disabled-demand' @{ hresult = $_.Exception.HResult; message = $_.Exception.Message }
            }
            $done.kill = $true
        }
        if ($seconds -ge 105 -and -not $done.child) {
            End-Owned childzero child TerminateProcess-zero
            # Cooperative end, not Task.Stop. The disable has not ended this child.
            $null = New-Item -ItemType File -Path "$base/disabled.stop"
            $done.child = $true
        }
        if ($seconds -ge 165 -and -not $done.enable) {
            Remove-Item -LiteralPath "$base/disabled.stop"
            Enable disabled $true
            $done.enable = $true
        }
        if ($seconds - $lastSample -ge 15) {
            foreach ($label in @($tasks.Keys | Sort-Object)) { Snapshot $label 'periodic' }
            $lastSample = $seconds
        }
        Start-Sleep -Milliseconds 500
    }
    Read-Payloads
    $known = @($handles.Values | ForEach-Object { [int]$_.record.pid })
    Fact 'matching-process-start-trace' @($trace | Where-Object {
        $_.pid -in $known -or $_.parent -in $known
    })
    Fact 'trace-coverage' @{
        totalEvents = $trace.Count
        limit = 'Only recorded payload PIDs and their direct children are printed. Shared Scheduler helpers and windows in other desktops are not excluded by this trace.'
    }
    Fact 'limits' @(
        'No sign-out or new sign-in was attempted for the existing runner account; that would end its job.',
        'Logon-trigger XML is registered, but a logon-trigger start is not measured in this job.',
        'No standard account was created. This is the hosted runner account, not the standard-account fixture.',
        'powercfg /a is read only. No suspend, resume, clock change, reboot, UAC or Fast Startup was attempted.',
        'Own-window enumeration is one payload-start snapshot, not continuous visual observation of an interactive screen.',
        'No app, no stopped-mode product no-op, no update, no migration and no installation was run.'
    )
    Fact 'measurement-loop-complete' $true
} finally {
    foreach ($label in @($tasks.Keys | Sort-Object)) {
        try {
            Enable $label $false
            $stop = "$base/$label.stop"
            if (-not (Test-Path -LiteralPath $stop)) { $null = New-Item -ItemType File -Path $stop }
        } catch { Fact 'cleanup-error' @{ label = $label; step = 'disable'; error = $_.Exception.Message } }
    }
    $deadline = [DateTime]::UtcNow.AddSeconds(20)
    do {
        Start-Sleep -Milliseconds 300
        Read-Payloads
        $remaining = @($tasks.Keys | Where-Object { (Current $_).GetInstances(0).Count -gt 0 })
    } while ($remaining.Count -gt 0 -and [DateTime]::UtcNow -lt $deadline)
    foreach ($label in @($tasks.Keys | Sort-Object)) {
        try {
            $t = Current $label
            if ($t.GetInstances(0).Count -ne 0) { throw 'Still running; retained rather than force-ended.' }
            $root.DeleteTask($tasks[$label].name, 0)
            $absent = $false
            try { $null = $root.GetTask($tasks[$label].name) } catch {
                $e = $_.Exception
                while ($null -ne $e.InnerException) { $e = $e.InnerException }
                if ($e.HResult -eq -2147024894) { $absent = $true } else { throw }
            }
            Fact 'removed' @{ name = $tasks[$label].name; confirmedAbsent = $absent }
            if (-not $absent) { throw 'Deletion was not confirmed.' }
        } catch { Fact 'retained-task' @{ name = $tasks[$label].name; error = $_.Exception.Message } }
    }
    try {
        foreach ($event in Get-WinEvent -FilterHashtable @{ LogName = $channel; StartTime = $begin } -ErrorAction Stop) {
            $xml = [xml]$event.ToXml()
            $values = @($xml.Event.EventData.Data | ForEach-Object { @{ name = $_.Name; value = $_.'#text' } })
            if (@($values | Where-Object { $_.value -in @($tasks.Values | ForEach-Object { '\' + $_.name }) }).Count -gt 0) {
                Fact 'scheduler-event' @{ id = $event.Id; record = $event.RecordId
                    pacific = [TimeZoneInfo]::ConvertTimeBySystemTimeZoneId($event.TimeCreated, 'Pacific Standard Time').ToString('o')
                    data = $values }
            }
        }
    } catch { Fact 'history-read-error' $_.Exception.Message }
    Unregister-Event -SourceIdentifier $traceSource -ErrorAction SilentlyContinue
    if ($historyWasEnabled -eq $false) { & wevtutil.exe sl $channel /e:false }
    foreach ($item in $handles.Values) { $item.process.Dispose() }
    $facts | ConvertTo-Json -Depth 16 | Set-Content -LiteralPath "$base/facts.json" -Encoding utf8
    Write-Output "W8 evidence folder: $base"
    "W8_EVIDENCE=$base" >> $env:GITHUB_ENV
}
if (@($facts | Where-Object { $_.kind -in @('retained-task', 'cleanup-error') }).Count -gt 0) {
    throw 'Synthetic resources remain; read the exact names above.'
}