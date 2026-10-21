@echo off
rem Локальний сервер find_new. Подвійний клік - і сторінка стає живою:
rem на ній зʼявляється робоча кнопка збору, а позначки лягають у базу,
rem а не в кеш браузера. Вікно не закривай - воно і є сервер.
rem Зупинити: закрити це вікно або Ctrl+C.
setlocal
cd /d "%~dp0"
if exist "%~dp0.venv\Scripts\python.exe" (
    set "PY=%~dp0.venv\Scripts\python.exe"
) else (
    set "PY=python"
)
echo.
echo   ВIДКРИЙ У БРАУЗЕРI:   http://localhost:8765
echo.
"%PY%" "%~dp0run.py" serve
echo.
echo   Сервер зупинився. Рядки вище кажуть чому.
pause
endlocal
