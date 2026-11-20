<#
  하늘 앱 기상특보·지진 묶음 갱신 (2026-11-20).

  무엇: apps\haneul\alerts.py 로 data\haneul\alerts.json 을 새로 굽고,
        **내용이 바뀌었거나** 마지막 커밋이 3시간을 넘었을 때만 그 파일 하나를 커밋·푸시한다.
  왜 3시간: 특보가 하루 종일 그대로인 날에도 앱·감시가 "수집이 살아 있다"를 볼 수 있게
            generated_at 을 주기적으로 올린다(앱은 12시간 넘게 묵으면 특보 카드를 내린다).
  실행: 작업 스케줄러 `\app-data haneul alerts` 10분마다 (run_min.vbs 로 창 없이)
        로그: logs\refresh_haneul.log (깃 무시)
  ⚠️refresh.bat 을 쓰지 않는다 — 그건 `git add data` 로 **다른 앱의 작업 중 파일까지** 담고,
    수집기 하나가 실패하면 모든 앱 푸시를 막는다. 여기서는 data/haneul 만 다룬다.
  ⚠️이 파일은 UTF-8 BOM 으로 저장할 것(Windows PowerShell 5.1 이 한글을 깨뜨린다).
#>
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$log  = Join-Path $root 'logs\refresh_haneul.log'
New-Item -ItemType Directory -Force (Split-Path $log) | Out-Null
function Log($m) { "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $m" | Out-File -Append -Encoding utf8 $log }

Push-Location $root
try {
    $out = & python "$root\apps\haneul\alerts.py" 2>&1
    $code = $LASTEXITCODE
    $out | Select-Object -First 3 | ForEach-Object { Log "  $_" }
    if ($code -ne 0) {
        # 실패하면 직전 파일을 그대로 둔다(앱은 낡은 묶음을 계속 보고, 12시간 넘으면 카드를 내린다)
        & git checkout -- data/haneul 2>&1 | Out-Null
        Log "수집 실패(exit=$code) — 파일 되돌림"
        exit 0
    }
    $changed = ($out -join "`n") -match '(?m)^CHANGED$'

    # 다른 작업(refresh.bat·약국 재시도)이 깃을 쥐고 있으면 이번 회차는 건너뛴다 — 10분 뒤 다시 온다
    if (Test-Path (Join-Path $root '.git\index.lock')) { Log 'index.lock 있음 — 건너뜀'; exit 0 }

    $last = & git log -1 --format=%ct -- data/haneul/alerts.json 2>$null
    $ageMin = if ($last) { ([DateTimeOffset]::UtcNow.ToUnixTimeSeconds() - [int64]$last) / 60 } else { 99999 }
    if ($changed -or $ageMin -ge 180) {
        & git add data/haneul/alerts.json 2>&1 | Out-Null
        & git diff --cached --quiet -- data/haneul/alerts.json
        if ($LASTEXITCODE -ne 0) {
            $why = if ($changed) { 'alerts' } else { 'heartbeat' }
            # ⚠️경로를 지정해 커밋한다 — 다른 작업이 미리 올려 둔(staged) 파일을 같이 담지 않게
            & git commit -m "data(haneul): $why $(Get-Date -Format 'yyyy-MM-dd_HH:mm')" -- data/haneul/alerts.json 2>&1 | ForEach-Object { Log "  $_" }
            Log "커밋 ($why)"
        }
    }

    # 앞서 푸시에 실패한 커밋이 남아 있으면 이번에 올린다(내용이 그대로인 회차에도)
    $ahead = & git rev-list --count '@{u}..HEAD' 2>$null
    if ([int]$ahead -gt 0) {
        & git push 2>&1 | ForEach-Object { Log "  $_" }
        if ($LASTEXITCODE -ne 0) {
            # 원격이 앞서 있으면(다른 작업이 먼저 푸시) 당겨서 다음 회차에 올린다.
            # ⚠️--autostash 는 쓰지 않는다 — 다른 작업이 쓰는 중인 파일을 치웠다 되돌리게 된다.
            #   작업트리가 더러우면 pull 이 거절되고, 그건 그 작업이 커밋·푸시하면서 풀린다.
            & git pull --rebase 2>&1 | ForEach-Object { Log "  $_" }
            & git push 2>&1 | ForEach-Object { Log "  $_" }
        }
        Log "푸시 시도 (앞선 커밋 $ahead)"
    }
} finally {
    Pop-Location
}
exit 0
