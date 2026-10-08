@echo off
rem tmt70print launcher -- prefers the bundled embeddable Python, falls back to
rem whatever "py" / "python" is on PATH.
rem SPDX-License-Identifier: GPL-2.0-or-later
setlocal
set "HERE=%~dp0"

if exist "%HERE%python\python.exe" (
    "%HERE%python\python.exe" "%HERE%tmt70print.py" %*
    exit /b %ERRORLEVEL%
)

where py >nul 2>&1
if %ERRORLEVEL%==0 (
    py -3 "%HERE%tmt70print.py" %*
    exit /b %ERRORLEVEL%
)

where python >nul 2>&1
if %ERRORLEVEL%==0 (
    python "%HERE%tmt70print.py" %*
    exit /b %ERRORLEVEL%
)

echo tmt70print: no Python found. Install Python 3.8+ or use the bundled build. 1>&2
exit /b 9
