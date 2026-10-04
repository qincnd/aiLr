@echo off
chcp 65001 >nul
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\test.ps1" %*
set "EXITCODE=%ERRORLEVEL%"
if not "%EXITCODE%"=="0" (
    echo.
    echo 本地测试未全部通过，退出码 %EXITCODE%。请查看上面的错误信息或 README.md。
    pause
)
endlocal & exit /b %EXITCODE%
