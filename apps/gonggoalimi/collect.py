"""
사업공고 알리미(gonggoalimi) — K-Startup 사업공고 수집기.

전체 3만여 건 중 '모집중'만 서버측 조건 필터로 받아, 마감일로 한 번 더 거른 뒤
data/gonggoalimi/notices.json 으로 저장한다.

필드명은 원본(snake_case)을 그대로 둔다 — 앱의 기존 파싱/분류 코드를 그대로 쓰기 위해서다.

사용법:
    python collect.py
    KSTARTUP_SERVICE_KEY=<data.go.kr Decoding 키> python collect.py   (키 없으면 공개 엔드포인트 사용)
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 콘솔 인코딩은 건드리지 않는다(cmd 는 cp949). 표현 못 하는 글자만 흘려보낸다.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

APP = "gonggoalimi"
ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / APP / "notices.json"

KST = timezone(timedelta(hours=9))

# 정식 경로(활용신청 기반). 일일 10,000콜 제한이 있으나 필터를 쓰면 회당 3-4콜이면 끝난다.
OFFICIAL = "https://apis.data.go.kr/B552735/kisedKstartupService01/getAnnouncementInformation01"
# 서비스 설계서에 END POINT URL 로 명시된 원본 엔드포인트. 인증 없이 열려 있고 호출 제한이 없다.
PUBLIC = "https://nidapi.k-startup.go.kr/api/kisedKstartupService/v1/getAnnouncementInformation"

PER_PAGE = 100
MAX_PAGES = 20          # 모집중은 통상 300건 안팎. 늘어날 여지를 두고 상한만 걸어둔다.
CONTENT_LIMIT = 600     # 공고 본문은 앱에서 요약으로만 쓴다. 전문을 담으면 파일이 5배가 된다.
SHRINK_GUARD = 0.5      # 직전 수집 대비 이만큼 밑으로 줄면 이상으로 보고 중단한다.

# 앱이 실제로 읽는 필드만 남긴다(KStartupItem 매핑과 1:1).
FIELDS = [
    "pbanc_sn", "biz_pbanc_nm", "pbanc_rcpt_bgng_dt", "pbanc_rcpt_end_dt",
    "rcrt_prgs_yn", "supt_regin", "supt_biz_clsfc", "aply_trgt_ctnt", "aply_trgt",
    "biz_enyy", "aply_mthd_onli_rcpt_istc", "biz_aply_url", "detl_pg_url",
    "biz_gdnc_url", "pbanc_ntrp_nm", "sprv_inst", "pbanc_ctnt", "intg_pbanc_yn",
]


def _s(v):
    return "" if v is None else str(v).strip()


def _get(url, params):
    qs = urllib.parse.urlencode(params, safe="[]:")
    req = urllib.request.Request(url + "?" + qs, headers={"User-Agent": "app-data-collector/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_page(page, service_key):
    """모집중 조건 필터를 걸어 한 페이지를 가져온다."""
    params = {
        "page": page,
        "perPage": PER_PAGE,
        "returnType": "json",
        "cond[rcrt_prgs_yn::EQ]": "Y",
    }
    if service_key:
        params["serviceKey"] = service_key
        return _get(OFFICIAL, params)
    return _get(PUBLIC, params)


def parse_ymd(raw):
    s = _s(raw)[:8]
    if len(s) != 8 or not s.isdigit():
        return None
    try:
        return datetime(int(s[:4]), int(s[4:6]), int(s[6:8]), tzinfo=KST).date()
    except ValueError:
        return None


def trim_content(text):
    """본문을 잘라 담되, 잘린 끝에 반쪽짜리 HTML 엔티티(&am...)가 남지 않게 한다."""
    s = _s(text)
    if len(s) <= CONTENT_LIMIT:
        return s
    cut = s[:CONTENT_LIMIT]
    amp = cut.rfind("&")
    if amp != -1 and ";" not in cut[amp:]:
        cut = cut[:amp]
    return cut


def collect(service_key):
    today = datetime.now(KST).date()
    seen = {}
    pages = 0

    for page in range(1, MAX_PAGES + 1):
        resp = fetch_page(page, service_key)
        total = int(resp.get("totalCount") or 0)
        match = int(resp.get("matchCount") or 0)

        # 서버가 조건 필터를 무시하면 전체가 그대로 내려온다. 그걸 모른 채 앞 몇 페이지만
        # 담으면 '모집중 목록'이 조용히 엉뚱한 내용으로 바뀐다 — 차라리 실패시킨다.
        if page == 1:
            if match <= 0 or match >= total:
                raise SystemExit(
                    f"[중단] 모집중 필터가 듣지 않는다 (matchCount={match}, totalCount={total}). "
                    "API 스펙 변경 가능성 — 수집을 중단한다."
                )
            print(f"  모집중 {match}건 / 전체 {total}건")

        rows = resp.get("data") or []
        if not rows:
            break
        pages += 1

        for it in rows:
            pk = _s(it.get("pbanc_sn"))
            title = _s(it.get("biz_pbanc_nm"))
            if not pk or not title or pk in seen:
                continue
            end = parse_ymd(it.get("pbanc_rcpt_end_dt"))
            # 서버가 Y로 두고도 마감일이 지난 건이 섞인다. 마감일로 한 번 더 거른다.
            if end is None or (end - today).days < 0:
                continue
            row = {k: it.get(k) for k in FIELDS}
            row["pbanc_ctnt"] = trim_content(it.get("pbanc_ctnt"))
            seen[pk] = row

    # 마감 임박순. 앱도 같은 순서로 보여주므로 미리 정렬해 둔다.
    notices = sorted(seen.values(), key=lambda r: (_s(r.get("pbanc_rcpt_end_dt")), _s(r.get("biz_pbanc_nm"))))
    return notices, pages


def previous_count():
    if not OUT.exists():
        return None
    try:
        with OUT.open(encoding="utf-8") as f:
            return int(json.load(f).get("count") or 0)
    except Exception:
        return None


def main():
    key = os.environ.get("KSTARTUP_SERVICE_KEY", "").strip()
    print(f"[{APP}] 수집 시작 ({'정식 인증키' if key else '공개 엔드포인트'})")

    try:
        notices, pages = collect(key)
    except urllib.error.HTTPError as e:
        if key and e.code in (401, 403):
            print(f"  인증키 거부({e.code}) — 공개 엔드포인트로 재시도")
            notices, pages = collect("")
        else:
            raise

    if not notices:
        raise SystemExit("[중단] 수집 결과가 0건이다.")

    prev = previous_count()
    if prev and len(notices) < prev * SHRINK_GUARD:
        raise SystemExit(
            f"[중단] 건수가 급감했다 ({prev} → {len(notices)}). "
            "원본 이상이 의심되므로 기존 파일을 유지한다."
        )

    payload = {
        "schema": 1,
        "app": APP,
        "source": "kstartup",
        "generated_at": datetime.now(KST).isoformat(timespec="seconds"),
        "count": len(notices),
        "notices": notices,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))

    size_kb = OUT.stat().st_size / 1024
    delta = "" if prev is None else f" (직전 {prev}건)"
    print(f"  {len(notices)}건 저장{delta} · {pages}페이지 · {size_kb:.0f} KB")
    print(f"  -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
