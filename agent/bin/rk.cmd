@echo off
rem rk を agent\ の外からでも動かす包み（cmd・PowerShell）
setlocal
set "PYTHONPATH=%~dp0..;%PYTHONPATH%"
set "PYTHONIOENCODING=utf-8"
if defined RK_PYTHON (set "RK_PY=%RK_PYTHON%") else (set "RK_PY=python")
"%RK_PY%" -m rk %*
exit /b %ERRORLEVEL%
