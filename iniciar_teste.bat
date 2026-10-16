@echo off
setlocal
title Emporio dos Paes - TESTE (porta 5001)
cd /d "%~dp0"

rem Use this in a SEPARATE TEST COPY, not the folder used in production.
rem Keep the test copy's own dados.db and config.json.
set "PADARIA_PORTA=5001"
set "APP_URL=http://127.0.0.1:5001/login"

echo.
echo  ==============================================
echo    EMPORIO DOS PAES - AMBIENTE DE TESTE
echo  ==============================================
echo.
echo Esta copia vai abrir em: %APP_URL%
echo Porta: 5001
echo Mantenha esta janela aberta enquanto testa.
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

echo [1/3] Verificando pip...
%PYTHON% -m pip --version
if errorlevel 1 (
    echo Nao foi possivel executar o pip.
    pause
    exit /b 1
)

echo.
echo [2/3] Instalando/verificando dependencias...
%PYTHON% -m pip install -r requirements.txt
if errorlevel 1 (
    echo Falha ao instalar dependencias. Confira a internet.
    pause
    exit /b 1
)

echo.
echo [3/3] Iniciando o ambiente de teste na porta 5001...
start "" "%APP_URL%"
%PYTHON% app.py

echo.
echo O servidor de teste foi encerrado.
pause
endlocal
