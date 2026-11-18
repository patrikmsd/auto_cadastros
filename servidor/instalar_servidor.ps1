# Prepara este PC como servidor do assistente. Rodar UMA vez, no PowerShell como Administrador:
#   powershell -ExecutionPolicy Bypass -File servidor\instalar_servidor.ps1
# 1. tarefa "Assistente PowerCRM": liga o assistente quando o usuario entra no Windows (sessao com tela:
#    se o login automatico do CRM falhar, a janela do navegador precisa aparecer para alguem terminar)
# 2. libera a porta 8780 no Firewall (so rede privada/dominio)
# 3. impede o PC de suspender ligado na tomada
$ErrorActionPreference = "Stop"
$bat = Join-Path $PSScriptRoot "iniciar_assistente.bat"
$usuario = "$env:USERDOMAIN\$env:USERNAME"

$acao = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$bat`"" -WorkingDirectory (Split-Path $PSScriptRoot)
$gatilho = New-ScheduledTaskTrigger -AtLogOn -User $usuario
$ajustes = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $usuario -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName "Assistente PowerCRM" -Action $acao -Trigger $gatilho -Settings $ajustes -Principal $principal -Force | Out-Null
Write-Host "Tarefa 'Assistente PowerCRM' criada: liga quando $usuario entrar no Windows."

if (-not (Get-NetFirewallRule -DisplayName "Assistente PowerCRM 8780" -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName "Assistente PowerCRM 8780" -Direction Inbound -Protocol TCP -LocalPort 8780 `
        -Action Allow -Profile Private,Domain | Out-Null
}
Write-Host "Porta 8780 liberada no Firewall (rede privada/dominio; nunca abrir para a internet)."

powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
Write-Host "Suspensao desligada na tomada."
Write-Host ""
Write-Host "Para ligar agora sem reiniciar:  Start-ScheduledTask -TaskName 'Assistente PowerCRM'"
Write-Host "Chave de cada analista:          .venv\Scripts\python.exe -m robo.assistente --nova-chave `"Nome`""
