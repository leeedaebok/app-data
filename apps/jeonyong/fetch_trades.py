"""전용평 실거래 — 국토부 아파트 매매 실거래(상세) 원본 수집.

시군구 × 계약월 단위로 원본을 받아 `_cache/jeonyong/trade/<시군구>/<YYYYMM>.json` 에 둔다.
앱이 받아 가는 파일은 이걸 굽는 `build.py` 가 만든다(여기서는 깃에 아무것도 안 쓴다).

⚠️ 이 파일 이름을 `collect.py` 로 바꾸지 말 것 — `refresh.bat` 이 `apps/*/collect.py` 를 돌려
master 에 커밋한다. 실거래 3년치는 수백 MB 라 master 히스토리에 쌓이면 안 된다.

갱신 규칙
- 최근 RECENT_MONTHS 개월은 매번 다시 받는다. 신고 기한이 계약 후 30일이고, 해제(취소) 신고는
  그 뒤에도 들어온다 — 지난 달 파일을 한 번 받고 끝내면 늦게 들어온 거래·취소가 영영 빠진다.
- 그보다 오래된 달은 캐시가 없을 때만 받는다.
- 하루 호출 한도(개발 키 10,000)를 넘지 않게 --budget 만큼만 부르고 멈춘다. 다음 실행이 이어 받는다.

- 기본 120개월(10년, 2026-11-22 운영자 요청 — 단지 상세 그래프). 최근 달부터 거꾸로 채우므로
  예산이 끊겨도 앱이 바로 쓰는 최근 3년이 먼저 찬다. 첫 백필은 약 3만 호출 = 하루 9,000씩 사흘.

사용
  python apps/jeonyong/fetch_trades.py --budget 9000
  python apps/jeonyong/fetch_trades.py --only 11680 --months 2      # 한 곳만
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "_cache" / "jeonyong" / "trade"
SGG_FILE = Path(__file__).with_name("sgg_codes.json")

URL = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev"
PAGE = 1000
RECENT_MONTHS = 3

# 앱에 필요한 필드만 남긴다(원본 필드명 그대로).
KEEP = (
    "sggCd", "aptSeq", "aptNm", "aptDong", "umdNm", "umdCd", "jibun", "roadNm", "roadNmBonbun",
    "roadNmBubun", "buildYear", "excluUseAr", "dealYear", "dealMonth", "dealDay",
    "dealAmount", "floor", "cdealType", "cdealDay", "dealingGbn", "rgstDate",
)

ENV_FALLBACKS = (
    Path(r"D:\claude_workspace\autoblog_local\bok_realestate_blog\.env"),
    Path(r"D:\claude_workspace\autoblog_local\bokine_guide_blog\.env"),
)


class ApiError(RuntimeError):
    pass


class QuotaExceeded(ApiError):
    pass


def load_key() -> str:
    key = os.environ.get("DATA_GO_KR_KEY", "").strip()
    if key:
        return key
    for p in ENV_FALLBACKS:
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                if line.startswith("DATA_GO_KR_KEY="):
                    return line.split("=", 1)[1].strip().strip('"')
    sys.exit("DATA_GO_KR_KEY 없음")


def months_back(n: int, today: date | None = None) -> list[str]:
    """이번 달 포함 최근 n 개월, 오래된 순."""
    d = today or date.today()
    y, m = d.year, d.month
    out = []
    for _ in range(n):
        out.append(f"{y:04d}{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return out[::-1]


def parse_page(content: bytes) -> tuple[int, list[dict]]:
    try:
        root = ET.fromstring(content)
    except ET.ParseError as e:
        raise ApiError(f"XML 아님: {content[:200]!r}") from e
    code = (root.findtext(".//resultCode") or "").strip()
    if code not in ("000", "00"):
        msg = (root.findtext(".//resultMsg") or "").strip()
        if code == "22" or "LIMITED" in msg.upper():
            raise QuotaExceeded(f"{code} {msg}")
        raise ApiError(f"resultCode={code} {msg}")
    total = int(root.findtext(".//totalCount") or 0)
    items = []
    for it in root.findall(".//item"):
        rec = {k: (it.findtext(k) or "").strip() for k in KEEP}
        items.append(rec)
    return total, items


class Fetcher:
    def __init__(self, key: str, budget: int):
        self.key = key
        self.budget = budget
        self.calls = 0
        self.s = requests.Session()

    def _get(self, sgg: str, ym: str, page: int) -> bytes:
        if self.calls >= self.budget:
            raise QuotaExceeded("이번 실행 예산 소진")
        last = None
        for attempt in range(4):
            self.calls += 1
            try:
                r = self.s.get(URL, params={"serviceKey": self.key, "LAWD_CD": sgg, "DEAL_YMD": ym,
                                            "numOfRows": str(PAGE), "pageNo": str(page)}, timeout=40)
                if r.status_code == 429:
                    raise QuotaExceeded("HTTP 429")
                if r.status_code >= 500:
                    raise ApiError(f"HTTP {r.status_code}")
                if r.status_code != 200:
                    raise ApiError(f"HTTP {r.status_code} {r.text[:200]}")
                return r.content
            except (requests.ConnectionError, requests.Timeout, ApiError) as e:
                if isinstance(e, QuotaExceeded):
                    raise
                last = e
                time.sleep(2 + attempt * 3)
        raise ApiError(f"{sgg} {ym} p{page} 재시도 실패: {last}")

    def month(self, sgg: str, ym: str) -> list[dict]:
        total, items = parse_page(self._get(sgg, ym, 1))
        page = 1
        while len(items) < total:
            page += 1
            _, more = parse_page(self._get(sgg, ym, page))
            if not more:
                break
            items.extend(more)
        if len(items) != total:
            raise ApiError(f"{sgg} {ym} 건수 불일치 {len(items)}/{total}")
        # 서버측 필터가 무시되면 남의 지역이 섞인다 — 통째로 버린다.
        # ⚠️ aptSeq 앞자리로 보면 안 된다 — 개편 지역은 옛 코드가 남아 있다(동탄구 41597 의 aptSeq 가 41590-…).
        bad = [i for i in items if i["sggCd"] != sgg]
        if bad:
            raise ApiError(f"{sgg} {ym} 다른 시군구 거래 {len(bad)}건(필터 무시 의심)")
        return items


def write_atomic(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=int, default=120)
    ap.add_argument("--budget", type=int, default=9000)
    ap.add_argument("--only", nargs="*", help="시군구 코드 일부만")
    args = ap.parse_args()

    sggs = [s["code"] for s in json.loads(SGG_FILE.read_text(encoding="utf-8"))["sgg"]]
    if args.only:
        sggs = [s for s in sggs if s in set(args.only)]
    yms = months_back(args.months)
    recent = set(yms[-RECENT_MONTHS:])

    # 최근 달(늘 다시 받음)을 먼저, 그다음 빈 과거 달을 최근 쪽부터 — 예산이 끊겨도 앱에 쓸모 있는 순서.
    jobs = [(s, ym) for ym in reversed(yms) if ym in recent for s in sggs]
    jobs += [(s, ym) for ym in reversed(yms) if ym not in recent for s in sggs
             if not (CACHE / s / f"{ym}.json").exists()]

    f = Fetcher(load_key(), args.budget)
    done = rows = 0
    t0 = time.time()
    try:
        for s, ym in jobs:
            items = f.month(s, ym)
            write_atomic(CACHE / s / f"{ym}.json",
                         {"sgg": s, "ym": ym, "fetched_at": date.today().isoformat(),
                          "count": len(items), "items": items})
            done += 1
            rows += len(items)
            if done % 100 == 0:
                print(f"  {done}/{len(jobs)} 호출 {f.calls} 거래 {rows} ({time.time()-t0:.0f}s)", flush=True)
    except QuotaExceeded as e:
        print(f"멈춤: {e} — 남은 작업 {len(jobs)-done}")
        print(f"RESULT done={done} left={len(jobs)-done} calls={f.calls} rows={rows}")
        return 0
    except ApiError as e:
        print(f"실패: {e}")
        print(f"RESULT done={done} left={len(jobs)-done} calls={f.calls} rows={rows}")
        return 1
    print(f"RESULT done={done} left=0 calls={f.calls} rows={rows}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
