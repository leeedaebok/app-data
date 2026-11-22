"""전용평 실거래 — 수집 캐시를 앱이 받아 갈 파일로 굽는다.

입력  _cache/jeonyong/trade/<시군구>/<YYYYMM>.json   (fetch_trades.py)
      _cache/jeonyong/geo.json                       (geocode.py, 없으면 좌표 없이 굽는다)
출력  _cache/jeonyong/out/
        index.json        시군구 목록 + 중심좌표 + 최근 3개월 전용 평당가 중앙값
        dong.json         동 단위 같은 요약(지도 중간 줌)
        sgg/<코드>.json    단지 목록 + **최근 RECENT_YEARS 년** 거래 배열 (지도·목록·알림이 받는다)
        names.json        단지 이름 검색용 [id, 이름, 시군구, 동] (앱이 검색할 때만 받는다)
      _cache/jeonyong/hist/
        hist/<코드>.json   그보다 오래된 거래 (단지 상세를 열 때만 받는다 — 10년 그래프용)

거래 배열 한 줄 = [단지번호, 계약일 YYYYMMDD, 전용면적×100, 거래금액(만원), 층, 플래그]
  플래그 bit0 = 해제(취소)된 거래, bit1 = 직거래
  단지번호는 같은 파일 complexes 배열의 위치다.
hist 한 줄 = [단지 id(문자열), 계약일, 전용면적×100, 거래금액, 층, 플래그]
  — 번호가 아니라 id 를 쓴다. hist 는 따로 게시되어 sgg 파일과 굽기 시점이 다를 수 있다.
  최근 3년에 거래가 없는 단지는 앱에서 열 길이 없으니 hist 에도 넣지 않는다.

⚠️ 3년으로 자른 이유: 10년치를 sgg 파일에 다 넣으면 지도 화면이 받는 파일이 3배 넘게 무거워진다
   (최대 남양주 741KB → 약 2.5MB). 10년은 상세 그래프에서만 필요하다(2026-11-22 운영자 요청).

⚠️ 평당가는 **전용면적** 기준이다(이 앱의 정체성). 1평 = 3.305785㎡.
⚠️ 해제된 거래는 배열에는 남기되(앱이 취소선으로 보여 준다) 평당가·신고가 계산에서는 뺀다.
⚠️ 전용 20㎡ 미만은 요약 통계에서 뺀다 — 도시형생활주택 1건이 "동 최저가"로 보이던
   블로그 사고(2026-05-30)가 있었다. 단지 상세의 거래 목록에는 그대로 남긴다.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "_cache" / "jeonyong"
TRADE = CACHE / "trade"
GEO = CACHE / "geo.json"
OUT = CACHE / "out"
HIST = CACHE / "hist"
SGG_FILE = Path(__file__).with_name("sgg_codes.json")

PYEONG = 3.305785
MIN_AREA_STATS = 20.0
RECENT_YEARS = 3
SCHEMA = 1
KST = timezone(timedelta(hours=9))


def to_int(s: str) -> int:
    s = (s or "").replace(",", "").strip()
    return int(s) if s.lstrip("-").isdigit() else 0


def load_month_files(sgg: str) -> list[dict]:
    d = TRADE / sgg
    items: list[dict] = []
    if not d.exists():
        return items
    for f in sorted(d.glob("*.json")):
        items.extend(json.loads(f.read_text(encoding="utf-8"))["items"])
    return items


def ymd(it: dict) -> int:
    return to_int(it["dealYear"]) * 10000 + to_int(it["dealMonth"]) * 100 + to_int(it["dealDay"])


def ppp(amount_man: int, area: float) -> float:
    """전용 1평당 가격(만원)."""
    return amount_man / (area / PYEONG)


def build_sgg(sgg: str, geo: dict, recent_from: int, split_from: int) -> tuple[dict, list[float], dict, list]:
    items = load_month_files(sgg)
    complexes: dict[str, dict] = {}
    order: list[str] = []
    rows = []
    recent_ppp: list[float] = []
    dong_ppp: dict[str, list[float]] = defaultdict(list)
    dong_name: dict[str, str] = {}
    seen = set()
    for it in items:
        cid = it["aptSeq"] or f'{sgg}-{it["umdCd"]}-{it["jibun"]}-{it["aptNm"]}'
        area = float(it["excluUseAr"] or 0)
        amt = to_int(it["dealAmount"])
        day = ymd(it)
        if area <= 0 or amt <= 0:
            continue
        # 같은 거래가 두 달 파일에 겹칠 일은 없지만, 재수집 중 중복 방어.
        key = (cid, day, it["excluUseAr"], amt, it["floor"], it["aptDong"])
        if key in seen:
            continue
        seen.add(key)
        if cid not in complexes:
            g = geo.get(cid) or {}
            complexes[cid] = {
                "id": cid, "nm": it["aptNm"], "umd": it["umdNm"], "umdCd": it["umdCd"],
                "jibun": it["jibun"],
                "road": (it["roadNm"] + " " + str(to_int(it["roadNmBonbun"])) +
                         (f"-{to_int(it['roadNmBubun'])}" if to_int(it["roadNmBubun"]) else "")).strip()
                        if it["roadNm"] else "",
                "by": to_int(it["buildYear"]),
                "lat": g.get("lat"), "lng": g.get("lng"),
            }
            order.append(cid)
        c = complexes[cid]
        # 단지 이름은 가장 최근 거래의 표기를 쓴다(개명·표기 변경).
        if day >= c.get("_last", 0):
            c["_last"] = day
            c["nm"] = it["aptNm"]
        cancelled = it["cdealType"].upper() == "O"
        direct = "직거래" in it["dealingGbn"]
        flags = (1 if cancelled else 0) | (2 if direct else 0)
        rows.append([cid, day, round(area * 100), amt, to_int(it["floor"]), flags])
        if not cancelled and area >= MIN_AREA_STATS and day >= recent_from:
            p = ppp(amt, area)
            recent_ppp.append(p)
            dong_ppp[it["umdCd"]].append(p)
            dong_name[it["umdCd"]] = it["umdNm"]

    # 최근 RECENT_YEARS 년에 거래가 있는 단지만 남긴다. 그보다 오래된 거래는 hist 로.
    live = {r[0] for r in rows if r[1] >= split_from}
    order = [cid for cid in order if cid in live]
    old = sorted((r for r in rows if r[1] < split_from and r[0] in live), key=lambda r: (r[1], r[0]), reverse=True)
    rows = [r for r in rows if r[1] >= split_from]
    idx = {cid: i for i, cid in enumerate(order)}
    rows.sort(key=lambda r: (r[1], idx[r[0]]), reverse=True)
    for r in rows:
        r[0] = idx[r[0]]
    clist = []
    for cid in order:
        c = complexes[cid]
        c.pop("_last", None)
        clist.append(c)

    dongs = {}
    by_dong_pts = defaultdict(list)
    for c in clist:
        if c["lat"] is not None:
            by_dong_pts[c["umdCd"]].append((c["lat"], c["lng"]))
    for cd, ps in dong_ppp.items():
        pts = by_dong_pts.get(cd)
        dongs[cd] = {
            "umdCd": cd, "nm": dong_name[cd], "n": len(ps), "ppp": round(statistics.median(ps)),
            "lat": round(statistics.mean(p[0] for p in pts), 6) if pts else None,
            "lng": round(statistics.mean(p[1] for p in pts), 6) if pts else None,
        }
    return {"complexes": clist, "trades": rows}, recent_ppp, dongs, old


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*")
    args = ap.parse_args()

    sggs = json.loads(SGG_FILE.read_text(encoding="utf-8"))["sgg"]
    if args.only:
        sggs = [s for s in sggs if s["code"] in set(args.only)]
    geo = json.loads(GEO.read_text(encoding="utf-8")) if GEO.exists() else {}

    # 최신 계약월 = 캐시에 있는 가장 늦은 달. 요약은 그 달 포함 최근 3개월.
    yms = sorted({f.stem for d in TRADE.iterdir() if d.is_dir() for f in d.glob("*.json")})
    if not yms:
        sys.exit("캐시 비어 있음 — fetch_trades.py 먼저")
    latest = yms[-1]
    y, m = int(latest[:4]), int(latest[4:])
    m -= 2
    if m <= 0:
        y, m = y - 1, m + 12
    recent_from = y * 10000 + m * 100
    # sgg 파일 = 최신 달 포함 최근 RECENT_YEARS×12 개월. 그 앞은 hist.
    t = int(latest[:4]) * 12 + int(latest[4:]) - 1 - (RECENT_YEARS * 12 - 1)
    split_from = (t // 12) * 10000 + (t % 12 + 1) * 100

    now = datetime.now(KST).isoformat(timespec="seconds")
    index, dong_all, names = [], [], []
    total_trades = total_cx = total_hist = 0
    for s in sggs:
        data, rp, dongs, old = build_sgg(s["code"], geo, recent_from, split_from)
        if not data["trades"]:
            continue
        write_json(HIST / "hist" / f'{s["code"]}.json', {
            "schema": SCHEMA, "app": "jeonyong", "sgg": s["code"], "before": split_from,
            "oldest": old[-1][1] if old else None, "count": len(old), "trades": old,
        })
        total_hist += len(old)
        pts = [(c["lat"], c["lng"]) for c in data["complexes"] if c["lat"] is not None]
        write_json(OUT / "sgg" / f'{s["code"]}.json', {
            "schema": SCHEMA, "app": "jeonyong", "sgg": s["code"], "generated_at": now,
            "count": len(data["trades"]), **data,
        })
        index.append({
            "code": s["code"], "sido": s["sido"], "nm": s["name"],
            "cx": len(data["complexes"]), "n": len(rp),
            "ppp": round(statistics.median(rp)) if rp else None,
            "lat": round(statistics.mean(p[0] for p in pts), 6) if pts else None,
            "lng": round(statistics.mean(p[1] for p in pts), 6) if pts else None,
            # 화면에 걸친 시군구만 받으려고 단지 좌표의 외곽 사각형을 둔다 [남, 서, 북, 동].
            "bbox": [min(p[0] for p in pts), min(p[1] for p in pts),
                     max(p[0] for p in pts), max(p[1] for p in pts)] if pts else None,
        })
        for c in data["complexes"]:
            names.append([c["id"], c["nm"], s["code"], c["umd"]])
        for d in dongs.values():
            d["sgg"] = s["code"]
            dong_all.append(d)
        total_trades += len(data["trades"])
        total_cx += len(data["complexes"])

    if not index:
        print("결과 0건 — 중단")
        return 1
    meta = {"schema": SCHEMA, "app": "jeonyong", "source": "국토교통부 아파트 매매 실거래가 상세(data.go.kr 15126468)",
            "generated_at": now, "latest_ym": latest, "recent_from": recent_from}
    write_json(OUT / "index.json", {**meta, "count": len(index), "sgg": index})
    write_json(OUT / "dong.json", {**meta, "count": len(dong_all), "dong": dong_all})
    write_json(OUT / "names.json", {**meta, "count": len(names), "names": names})
    geo_ok = sum(1 for s in index if s["lat"] is not None)
    print(f"RESULT sgg={len(index)} complexes={total_cx} trades={total_trades} hist={total_hist} "
          f"dong={len(dong_all)} sgg_with_geo={geo_ok} latest={latest} split_from={split_from}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
