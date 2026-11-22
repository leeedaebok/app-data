<#
  전용평 실거래(jeonyong) 매일 갱신: 수집 → 새 단지 좌표 → 굽기 → 게시.

  왜 refresh.bat 이 아니냐: 그건 결과를 master 에 커밋한다. 이 앱 데이터는 수십 MB 라
        `jeonyong-data`(매번 덮어쓰기)·`jeonyong-hist`(바뀔 때만) 브랜치로만 올린다(publish.py).
  수집: 국토부 개발 키 하루 10,000회 중 9,000. 최근 3개월은 매번 다시 받고, 남는 예산으로
        10년치 과거 달을 최근 쪽부터 채운다 — 첫 백필은 사흘(2026-11-23 부터).
  실행: 작업 스케줄러 `\app-data jeonyong` 매일 01:00 (run_min.vbs 로 창 없이)
        로그: logs\refresh_jeonyong.log (깃 무시)
  ⚠️이 파일은 UTF-8 BOM 으로 저장할 것(Windows PowerShell 5.1 이 한글을 깨뜨린다).
#>
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$log  = Join-Path $root 'logs\refresh_jeonyong.log'
New-Item -ItemType Directory -Force (Split-Path $log) | Out-Null
function Log($m) { "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $m" | Out-File -Append -Encoding utf8 $log }

$env:PYTHONIOENCODING = 'utf-8'
# ⚠️수집기 stderr 가 섞여도 멈추지 않게 Continue. 단계마다 종료 코드로 판단한다.
$ErrorActionPreference = 'Continue'
Push-Location $root

function Step($name, $script, $argv) {
    Log "▶ $name"
    & python "$root\apps\jeonyong\$script" @argv 2>&1 | ForEach-Object { Log "  $_" }
    $c = $LASTEXITCODE
    Log "◀ $name exit=$c"
    return $c
}

# 수집이 중간에 끊겨도(예산·API 오류) 받은 달은 캐시에 남아 있다 → 굽기·게시는 그대로 진행.
$null = Step 'fetch' 'fetch_trades.py' @('--budget', '9000')
$null = Step 'geocode' 'geocode.py' @()
$b = Step 'build' 'build.py' @()
if ($b -ne 0) { Log '굽기 실패 — 게시 안 함(앱은 어제 파일을 그대로 받는다)'; Pop-Location; exit 1 }
$p = Step 'publish' 'publish.py' @()
Pop-Location
exit $p
