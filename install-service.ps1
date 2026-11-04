# Run once as Administrator. Registers a Task Scheduler job that starts the
# iEAST web controller at boot, with no user logged in, and restarts it if it dies.
$dir    = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $dir ".venv\Scripts\python.exe"
$action = New-ScheduledTaskAction -Execute $python -Argument "-m app.main" -WorkingDirectory $dir
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
Register-ScheduledTask -TaskName "iEAST Web Control" -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Force | Out-Null
# Environment for the task comes from a .env file next to this script.
Write-Host "Task registered. Edit $dir\.env to set DEVICES, then: Start-ScheduledTask 'iEAST Web Control'"
