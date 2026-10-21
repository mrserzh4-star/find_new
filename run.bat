@echo off
rem Один прогін сервісу. Цей файл запускає і планувальник, і ти сам подвійним кліком.
rem Якщо є .venv — беремо Python звідти, інакше системний.
setlocal
cd /d "%~dp0"
if exist "%~dp0.venv\Scripts\python.exe" (
    set "PY=%~dp0.venv\Scripts\python.exe"
) else (
    set "PY=python"
)
"%PY%" "%~dp0run.py" run
endlocal
