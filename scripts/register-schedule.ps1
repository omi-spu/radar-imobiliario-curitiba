$ErrorActionPreference = 'Stop'

$projectDirectory = Split-Path -Parent $PSScriptRoot
$uvExecutable = (Get-Command uv -ErrorAction Stop).Source
$powershellExecutable = (Get-Command powershell.exe -ErrorAction Stop).Source
$taskName = 'RadarImobiliarioCuritiba'
$currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

$dailyScript = Join-Path $PSScriptRoot 'run-daily.ps1'
$arguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -UvExecutable "{1}"' -f $dailyScript, $uvExecutable
$action = New-ScheduledTaskAction -Execute $powershellExecutable -Argument $arguments -WorkingDirectory $projectDirectory
$dailyTrigger = New-ScheduledTaskTrigger -Daily -At '07:00'
$loginTrigger = New-ScheduledTaskTrigger -AtLogOn -User $currentUser
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 8)
$principal = New-ScheduledTaskPrincipal -UserId $currentUser -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger @($dailyTrigger, $loginTrigger) -Settings $settings -Principal $principal -Description 'Coleta diária de anúncios de casas em Curitiba e treino semanal de estimativa' -Force | Out-Null
Write-Output "Tarefa $taskName registrada para 07:00 e no próximo login."
