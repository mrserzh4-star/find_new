@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if exist "%~dp0.venv\Scripts\pythonw.exe" (
    set "PY=%~dp0.venv\Scripts\python.exe"
) else (
    set "PY=python"
)
"%PY%" "%~dp0open_product.py"
endlocal
