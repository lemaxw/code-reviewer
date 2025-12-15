@echo off
setlocal

REM If an argument is provided, use it as SUBDIR
if not "%~1"=="" (
    set "SUBDIR=%~1"
    goto run
)

REM No argument: derive SUBDIR from current directory after "src\"
set "FULL=%CD%"

REM Check if FULL contains "\src\"
set "CHECK=%FULL:\src\=%"
if "%CHECK%"=="%FULL%" (
    echo You are not in a src\ subdirectory and no argument was provided.
    goto endd
)

REM Strip everything up to and including "src\"
REM E:\40\src\cpp\Admin\AdminUtils  -->  cpp\Admin\AdminUtils
set "SUBDIR=%FULL:*src\=%"

REM Convert backslashes to forward slashes for Linux path
set "SUBDIR=%SUBDIR:\=/%"

:run
docker run --rm -it ^
  -e SVN_USERNAME=svc ^
  -e SVN_PASSWORD=gfn ^
  -v /src:/work ^
  -w /work/%SUBDIR% ^
  harbor.devportal.dalet.cloud/webnews-dev/tools/code-reviewer:2.1.3

:endd
endlocal
