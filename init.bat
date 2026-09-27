@echo off
chcp 65001 >nul
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\init.ps1" %*
set "EXITCODE=%ERRORLEVEL%"
if not "%EXITCODE%"=="0" (
    echo.
    echo 初始化未完成，退出码 %EXITCODE%。可查看上面的错误信息或 README.md。
    pause
)
endlocal & exit /b %EXITCODE%
