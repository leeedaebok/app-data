<#
  약국 수집 아침 재시도 (2026-10-29 운영자 결정).

  왜: 10/28·10/29 04:00 수집에서 S3(심평원 약국정보) 2페이지 요청이 공공데이터포털의
      임시 게이트웨이 주소(http://apis.data.go.kr/<번호>/...&SG_APIM=...)로 돌려져 400 이 났다.
      같은 요청이 낮에는 정상이라 새벽 시간대 게이트웨이 전환·점검으로 본다(추정, 공지 미확인).
      collect.py 의 3회 재시도(3·6초)로는 그 창을 못 넘긴다.
  무엇: 오늘 04:00 실행이 약국을 **발행하지 못했을 때만** 약국 하나를 다시 수집한다.
        정상인 날은 아무것도 하지 않는다(커밋도 안 생긴다).
  실행: 작업 스케줄러 `\app-data yakguk retry` 매일 07:00-19:00 매시 (run_min.vbs 로 창 없이)
        로그: logs\retry_yakguk.log (깃 무시)
  ⚠️이 파일은 UTF-8 BOM 으로 저장할 것(Windows PowerShell 5.1 이 한글을 깨뜨린다).
#>
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$val  = Join-Path $root 'data\yakguk\validation.json'
$log  = Join-Path $root 'logs\retry_yakguk.log'
New-Item -ItemType Directory -Force (Split-Path $log) | Out-Null

function Log($m) { "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $m" | Out-File -Append -Encoding utf8 $log }

$today = Get-Date -Format 'yyyy-MM-dd'
$need = $true
try {
    $j = Get-Content $val -Raw -Encoding UTF8 | ConvertFrom-Json
    $gen = [string]$j.generated_at
    if ($gen.StartsWith($today) -and $j.published -eq $true) { $need = $false }
    Log "확인: generated_at=$gen published=$($j.published) reasons=$($j.reasons -join ',')"
} catch {
    Log "validation.json 을 못 읽음 — 재수집한다: $($_.Exception.Message)"
}

if (-not $need) { Log "오늘 이미 발행됨($gen) — 재시도 안 함"; exit 0 }

Log '재수집 시작: apps\yakguk\collect.py'
# 🚨10/29 추가 실측: 실패는 새벽만이 아니다. 같은 날 08:3x 엔 정상, 08:41 엔 **단건 요청도
#   100%** 임시 주소로 돌려졌고, 그 주소는 `NO_OPENAPI_SERVICE_ERROR`(서비스가 없거나 폐기됨)
#   를 준다 — 포털 게이트웨이가 폐기된 옛 경로로 오배정하는 것으로, 수십 분 단위로 오락가락한다.
#   그래서 작업은 07-19시 **매시** 돌고, 오늘 한 번 성공하면 이후는 위에서 바로 끝난다.
# ⚠️refresh.bat 을 쓰지 않는다 — 그건 실패해도 validation.json 을 커밋해 **공개 저장소에
#   매시 실패 기록이 쌓인다.** 수집만 돌리고, 발행됐을 때만 직접 커밋한다.
# ⚠️수집기 stderr 가 섞이면 'Stop' 이 예외로 바꿔 중단하므로 이 구간만 Continue.
$ErrorActionPreference = 'Continue'
Push-Location $root
& python "$root\apps\yakguk\collect.py" 2>&1 | ForEach-Object { Log "  $_" }
$code = $LASTEXITCODE

$ok = $false
try {
    $j2 = Get-Content $val -Raw -Encoding UTF8 | ConvertFrom-Json
    $ok = ($j2.published -eq $true)
    Log "수집 끝(exit=$code): published=$($j2.published) reasons=$($j2.reasons -join ',')"
} catch { Log "수집 끝(exit=$code): validation.json 확인 실패" }

if ($ok -and $code -eq 0) {
    & git add data 2>&1 | ForEach-Object { Log "  $_" }
    & git diff --cached --quiet
    if ($LASTEXITCODE -ne 0) {
        & git commit -m "data(yakguk): retry $(Get-Date -Format 'yyyy-MM-dd_HH:mm')" 2>&1 | ForEach-Object { Log "  $_" }
        & git push 2>&1 | ForEach-Object { Log "  $_" }
        Log "발행 성공 — 커밋·푸시"
    } else { Log '발행 성공 — 바뀐 자료 없음' }
} else {
    # 실패 기록은 되돌린다(04:00 실행의 실패 기록이 이미 올라가 있다 — 그걸로 충분하다)
    & git checkout -- data/yakguk 2>&1 | ForEach-Object { Log "  $_" }
    Log '아직 실패 — 되돌리고 다음 시각에 다시 본다'
}
Pop-Location
$ErrorActionPreference = 'Stop'
exit 0
