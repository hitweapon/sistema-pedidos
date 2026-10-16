@echo off
setlocal
title Emporio dos Paes - Sistema Web
cd /d "%~dp0"

echo.
echo  ==============================================
echo    EMPORIO DOS PAES - INICIALIZACAO DO SISTEMA
echo  ==============================================
echo.

where py >nul 2>nul
if %errorlevel%==0 (
    set "PYTHON=py -3"
) else (
    where python >nul 2>nul
    if errorlevel 1 (
        echo ERRO: Python nao foi encontrado.
        echo Instale o Python 3 pelo site oficial e marque "Add Python to PATH".
        echo https://www.python.org/downloads/windows/
        pause
        exit /b 1
    )
    set "PYTHON=python"
)

echo [1/3] Verificando o instalador de pacotes...
%PYTHON% -m pip --version
if errorlevel 1 (
    echo Nao foi possivel executar o pip. Verifique a instalacao do Python.
    pause
    exit /b 1
)

echo.
echo [2/3] Instalando/verificando dependencias do sistema...
%PYTHON% -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo ERRO ao instalar dependencias. Confira a internet e a instalacao do Python.
    pause
    exit /b 1
)

echo.
echo [3/3] Iniciando o sistema web...
echo Quando o servidor iniciar, acesse: http://127.0.0.1:5000/login
echo Para encerrar, feche esta janela com CTRL+C.
echo.

start "" "http://127.0.0.1:5000/login"
%PYTHON% app.py

echo.
echo O servidor foi encerrado. Se houve erro, veja a mensagem acima.
pause
endlocal
