@echo off
rem Ставить щогодинний запуск у Windows Task Scheduler. Запустити ОДИН раз.
rem Зняти потім:  schtasks /delete /tn "find_new hourly" /f
rem Подивитись:   schtasks /query /tn "find_new hourly"
setlocal
schtasks /create ^
    /tn "find_new hourly" ^
    /tr "\"%~dp0run.bat\"" ^
    /sc hourly ^
    /f
if errorlevel 1 (
    echo.
    echo Не вдалося створити задачу. Спробуй запустити цей файл від імені адміністратора.
) else (
    echo.
    echo Готово: сервіс запускатиметься щогодини.
    echo Результат дивись у digest.html у цій же папці.
)
pause
endlocal
