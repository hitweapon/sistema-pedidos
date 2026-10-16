@echo off
title Padaria - Debug

start http://127.0.0.1:5000/login

python app.py

echo.
echo ==============================
echo   O programa encerrou com erro
echo   Veja a mensagem acima.
echo ==============================
pause