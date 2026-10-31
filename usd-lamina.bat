@echo off
setlocal EnableExtensions
title usd-lamina
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
set "PYTHONPATH=%ROOT%\src;%PYTHONPATH%"

set "PY="
set "PYW="

if exist "%ROOT%\.venv\Scripts\python.exe" set "PY=%ROOT%\.venv\Scripts\python.exe"
if exist "%ROOT%\.venv\Scripts\pythonw.exe" set "PYW=%ROOT%\.venv\Scripts\pythonw.exe"
if exist "%ROOT%\venv\Scripts\python.exe" if not defined PY set "PY=%ROOT%\venv\Scripts\python.exe"
if exist "%ROOT%\venv\Scripts\pythonw.exe" if not defined PYW set "PYW=%ROOT%\venv\Scripts\pythonw.exe"

if not defined PYW for /f "delims=" %%I in ('where pyw 2^>nul') do (
    set "PYW=%%I"
    goto :have_pyw
)
:have_pyw
if not defined PYW for /f "delims=" %%I in ('where pythonw 2^>nul') do (
    set "PYW=%%I"
    goto :have_pyw2
)
:have_pyw2
if not defined PY for /f "delims=" %%I in ('where py 2^>nul') do (
    set "PY=%%I"
    goto :have_py
)
:have_py
if not defined PY for /f "delims=" %%I in ('where python 2^>nul') do (
    set "PY=%%I"
    goto :have_py2
)
:have_py2

if not defined PY if not defined PYW (
    call :fail "Python 3.11+ was not found. Install Python and enable Add python.exe to PATH."
    exit /b 1
)

rem Explorer double-click: cmd.exe /c "...usd-lamina.bat" — hide the console.
echo(%CMDCMDLINE%| findstr /I /C:"%~nx0" >nul
if %ERRORLEVEL%==0 if defined PYW (
    start "usd-lamina" /D "%ROOT%" "%PYW%" -m usd_lamina %*
    exit /b 0
)

if defined PY (
    "%PY%" -m usd_lamina %*
    exit /b %ERRORLEVEL%
)
"%PYW%" -m usd_lamina %*
exit /b %ERRORLEVEL%

:fail
powershell -NoProfile -Command ^
  "Add-Type -AssemblyName PresentationFramework; [System.Windows.MessageBox]::Show('%~1','usd-lamina')" 2>nul
if errorlevel 1 (
    echo usd-lamina: %~1
    pause
)
exit /b 1
