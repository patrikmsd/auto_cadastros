@echo off
rem Liga o assistente para a rede e religa sozinho se ele cair. Feche esta janela para desligar.
chcp 65001 >nul
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8
if not exist "robo\saida\logs" mkdir "robo\saida\logs"
:ligar
".venv\Scripts\python.exe" -m robo.assistente --rede --sem-navegador
echo %date% %time% assistente parou (codigo %errorlevel%), religando em 10s>> "robo\saida\logs\reinicios.log"
timeout /t 10 /nobreak >nul
goto ligar
