@echo off
REM 앱 데이터 갱신 — 수집 후 변경이 있을 때만 커밋/푸시한다.
REM   refresh.bat              모든 앱
REM   refresh.bat gonggoalimi  특정 앱만
setlocal enabledelayedexpansion
cd /d "%~dp0"

set TARGET=%1
set FAILED=0
set RAN=0

if "%TARGET%"=="" (
  echo [1/3] 전체 앱 수집...
  for /d %%A in (apps\*) do (
    if exist "%%A\collect.py" (
      set RAN=1
      python "%%A\collect.py"
      if errorlevel 1 set FAILED=1
    )
  )
) else (
  if not exist "apps\%TARGET%\collect.py" (
    echo [오류] apps\%TARGET%\collect.py 가 없습니다.
    exit /b 1
  )
  echo [1/3] %TARGET% 수집...
  set RAN=1
  python "apps\%TARGET%\collect.py"
  if errorlevel 1 set FAILED=1
)

if "%RAN%"=="0" (
  echo [오류] 실행할 수집기가 없습니다.
  exit /b 1
)

REM 수집이 하나라도 실패하면 푸시하지 않는다 — 반쪽 데이터를 앱에 내보내지 않기 위해서다.
if "%FAILED%"=="1" (
  echo [중단] 수집 실패 - 커밋/푸시를 건너뜁니다.
  exit /b 1
)

echo [2/3] 변경 확인...
git add data
git diff --cached --quiet
if %errorlevel%==0 (
  echo   변경 없음 - 푸시 생략.
  exit /b 0
)

echo [3/3] 커밋 / 푸시...
for /f "tokens=*" %%d in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd_HH:mm"') do set NOW=%%d
if "%TARGET%"=="" (
  git commit -m "data: refresh %NOW%"
) else (
  git commit -m "data(%TARGET%): refresh %NOW%"
)
git push
echo 완료.
