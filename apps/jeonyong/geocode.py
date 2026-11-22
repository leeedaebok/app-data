"""전용평 실거래 — 단지 좌표 붙이기(네이버 클라우드 Maps Geocoding).

입력  _cache/jeonyong/out/sgg/*.json  (build.py 결과의 단지 목록)
출력  _cache/jeonyong/geo.json        {단지id: {"lat", "lng", "src"}}  ← build.py 가 읽는다

- 이미 좌표가 있는 단지는 건너뛴다(단지는 움직이지 않는다). 새 단지만 매일 몇 곳씩 늘어난다.
- 지번 주소 → 실패하면 도로명 주소 순. 결과의 지번 주소에 **동 이름이 들어 있어야** 받아들인다
  (지오코딩은 모르는 주소를 근처 아무 데로나 돌려주는 일이 있다 — 엉뚱한 동에 말풍선이 뜨는 것보다 빈 게 낫다).
- 키: 환경변수 NCP_MAP_KEY_ID / NCP_MAP_KEY, 없으면 app-data/.env. 무료 한도 월 300만 건(주 계정).

사용
  python apps/jeonyong/geocode.py --limit 50     # 시험
  python apps/jeonyong/geocode.py                # 전부
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "_cache" / "jeonyong"
GEO = CACHE / "geo.json"
OUT_SGG = CACHE / "out" / "sgg"
SGG_FILE = Path(__file__).with_name("sgg_codes.json")
URL = "https://maps.apigw.ntruss.com/map-geocode/v2/geocode"


def load_keys() -> tuple[str, str]:
    kid, key = os.environ.get("NCP_MAP_KEY_ID", ""), os.environ.get("NCP_MAP_KEY", "")
    env = ROOT / ".env"
    if (not kid or not key) and env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("NCP_MAP_KEY_ID="):
                kid = line.split("=", 1)[1].strip()
            elif line.startswith("NCP_MAP_KEY="):
                key = line.split("=", 1)[1].strip()
    if not kid or not key:
        sys.exit("NCP_MAP_KEY_ID / NCP_MAP_KEY 없음 (app-data/.env)")
    return kid, key


def sgg_label(sido: str, name: str) -> str:
    """'화성시동탄구' → '화성시 동탄구' (지오코더는 붙여 쓴 시·구를 못 읽는 일이 있다)."""
    import re
    # 세종은 시군구 단계가 없다 — "세종특별자치시 세종시 새롬동" 은 0건, "세종특별자치시 새롬동" 이라야 잡힌다(11/22 실측)
    if sido.startswith("세종"):
        return sido
    m = re.match(r"^(.+시)(.+구)$", name)
    return f"{sido} {m.group(1)} {m.group(2)}" if m else f"{sido} {name}"


def query(s: requests.Session, headers: dict, q: str) -> dict | None:
    for attempt in range(3):
        try:
            r = s.get(URL, params={"query": q}, headers=headers, timeout=15)
        except (requests.ConnectionError, requests.Timeout):
            time.sleep(1 + attempt * 2)
            continue
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(2 + attempt * 3)
            continue
        if r.status_code != 200:
            raise SystemExit(f"HTTP {r.status_code} {r.text[:200]}")
        a = r.json().get("addresses") or []
        return a[0] if a else None
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    kid, key = load_keys()
    headers = {"x-ncp-apigw-api-key-id": kid, "x-ncp-apigw-api-key": key, "Accept": "application/json"}
    sido = {s["code"]: (s["sido"], s["name"]) for s in json.loads(SGG_FILE.read_text(encoding="utf-8"))["sgg"]}
    geo: dict = json.loads(GEO.read_text(encoding="utf-8")) if GEO.exists() else {}

    todo = []
    for f in sorted(OUT_SGG.glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        for c in d["complexes"]:
            if c["id"] not in geo:
                todo.append((d["sgg"], c))
    if args.limit:
        todo = todo[: args.limit]
    print(f"좌표 필요 {len(todo)}곳 (보유 {len(geo)})", flush=True)

    s = requests.Session()
    ok = miss = 0
    t0 = time.time()
    for i, (code, c) in enumerate(todo, 1):
        base = sgg_label(*sido[code])
        tries = []
        if c["jibun"]:
            tries.append(("jibun", f'{base} {c["umd"]} {c["jibun"]}'))
        if c["road"]:
            tries.append(("road", f'{base} {c["road"]}'))
        hit = None
        for src, q in tries:
            a = query(s, headers, q)
            # 동 이름 확인. 리 단위(읍·면 아래)는 umd 가 "봉담읍 수영리" 처럼 둘이라 마지막 낱말로 본다.
            if a and c["umd"].split()[-1] in (a.get("jibunAddress") or ""):
                hit = (src, a)
                break
        if hit:
            src, a = hit
            geo[c["id"]] = {"lat": round(float(a["y"]), 6), "lng": round(float(a["x"]), 6), "src": src}
            ok += 1
        else:
            miss += 1
        if i % 500 == 0:
            GEO.write_text(json.dumps(geo, ensure_ascii=False), encoding="utf-8")
            print(f"  {i}/{len(todo)} ok={ok} miss={miss} ({time.time()-t0:.0f}s)", flush=True)
    GEO.write_text(json.dumps(geo, ensure_ascii=False), encoding="utf-8")
    print(f"RESULT ok={ok} miss={miss} total={len(geo)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
