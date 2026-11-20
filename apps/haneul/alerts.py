"""
하늘(haneul) — 기상청 기상특보·지진 수집기.

앱이 기상청 API 를 직접 부르지 않게(인증키가 앱에 박히지 않게) 여기서 받아
data/haneul/alerts.json 으로 굽는다. 앱은 그 파일을 받아 **내 시·도·시군구와 맞춰 본다.**

⚠️파일 이름이 `collect.py` 가 아닌 이유: `refresh.bat` 은 `apps/*/collect.py` 를 전부 돌리고
  하나라도 실패하면 **모든 앱의 푸시를 막는다.** 기상청 장애가 약국·공고 갱신까지 멈추지 않게
  이 수집기는 그 순회에서 빼고 `refresh_haneul.ps1`(10분 주기)로만 돈다.

원본(공공데이터포털, 활용신청 2026-11-20):
  - 기상청_기상특보 조회서비스 15000415 `getPwnStatus` — 지금 발효 중인 특보 요약(t6)·예비특보(t7)
    · 이용허락: 공공누리 제1유형(출처표시) — 앱에 "기상청" 출처를 반드시 적는다.
  - 기상청_지진정보 조회서비스 15000420 `getEqkMsg` — 최근 지진 통보(최대 3일). 이용허락 제한 없음.

사용법:
    python apps/haneul/alerts.py            (인증키: 환경변수 DATA_GO_KR_KEY → autoblog_local/.env 들)
    python apps/haneul/alerts.py --selftest (네트워크 없이 파서만 확인)
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

APP = "haneul"
ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / APP / "alerts.json"
KST = timezone(timedelta(hours=9))
BASE = "https://apis.data.go.kr/1360000"

# 인증키를 찾는 곳 — 같은 계정의 일반 인증키(디코딩 키, `==` 로 끝남)를 블로그들이 이미 쓰고 있다.
KEY_FILES = [
    Path(r"D:\claude_workspace\autoblog_local\bokine_guide_blog\.env"),
    Path(r"D:\claude_workspace\autoblog_local\bok_realestate_blog\.env"),
]

# ── 시·도 이름 정규화 ────────────────────────────────────────────────
# 특보 문구("전북자치도", "경상북도")·지진 진도("전남광주", "경북")·안드로이드 역지오코딩
# ("전북특별자치도", "서울특별시")가 같은 곳을 다르게 부른다. 전부 두 글자 줄임으로 모은다.
# ⚠️앱의 `KmaRegion.sidoKey` 와 **같은 표**여야 한다.
SIDO = {
    "서울": "서울", "서울특별시": "서울",
    "부산": "부산", "부산광역시": "부산",
    "대구": "대구", "대구광역시": "대구",
    "인천": "인천", "인천광역시": "인천",
    "광주": "광주", "광주광역시": "광주",
    "대전": "대전", "대전광역시": "대전",
    "울산": "울산", "울산광역시": "울산",
    "세종": "세종", "세종특별자치시": "세종",
    "경기": "경기", "경기도": "경기",
    "강원": "강원", "강원도": "강원", "강원특별자치도": "강원", "강원자치도": "강원",
    "충북": "충북", "충청북도": "충북",
    "충남": "충남", "충청남도": "충남",
    "전북": "전북", "전라북도": "전북", "전북특별자치도": "전북", "전북자치도": "전북",
    "전남": "전남", "전라남도": "전남",
    "경북": "경북", "경상북도": "경북",
    "경남": "경남", "경상남도": "경남",
    "제주": "제주", "제주도": "제주", "제주특별자치도": "제주",
}

# 시·도 괄호 없이 홀로 나오는 섬 특보구역 → (시·도, 시군구 줄임)
LONE_AREAS = {
    "울릉도.독도": ("경북", ["울릉"]),
    "서해5도": ("인천", ["옹진"]),
}

# 서울은 시군구가 아니라 4개 권역으로 특보가 나간다 → 구 이름(줄임)으로 펼친다.
# 출처: 기상청 특보구역(서울 동남·동북·서남·서북권).
SEOUL_ZONES = {
    "서울동남권": ["강남", "서초", "송파", "강동"],
    "서울동북권": ["성동", "광진", "동대문", "중랑", "성북", "강북", "도봉", "노원"],
    "서울서남권": ["양천", "강서", "구로", "금천", "영등포", "동작", "관악"],
    # ⚠️"중구"는 두 글자라 줄이지 않는다(앱 `KmaRegion.stem` 과 같은 규칙 — "중"이면 아무 데나 맞는다)
    "서울서북권": ["종로", "중구", "용산", "은평", "서대문", "마포"],
}

ROMAN = {"Ⅰ": 1, "Ⅱ": 2, "Ⅲ": 3, "Ⅳ": 4, "Ⅴ": 5, "Ⅵ": 6, "Ⅶ": 7, "Ⅷ": 8, "Ⅸ": 9, "Ⅹ": 10,
         "Ⅺ": 11, "Ⅻ": 12}

# 바다 특보구역(앞바다·먼바다·전해상)은 육지 사용자와 맞출 일이 없다 — 표시용으로만 센다.
SEA_RE = re.compile(r"(앞바다|먼바다|전해상|해상)$")


def area_stem(name):
    """특보구역 이름 → 시군구 줄임. "동해평지"→"동해", "영광(낙월면 제외)"→"영광", "태백시"→"태백"."""
    s = re.sub(r"\(.*?\)", "", name).strip()
    s = re.sub(r"(평지|산지|산간|중산간|북부|남부|동부|서부)$", "", s) if not s.startswith("제주도") else s
    if len(s) > 2:
        s = re.sub(r"(시|군|구)$", "", s)
    return s


def split_top(text, sep=","):
    """괄호 안의 쉼표는 건너뛰며 나눈다. "a(b, c), d" → ["a(b, c)", "d"]."""
    out, depth, cur = [], 0, ""
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == sep and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


def parse_group(token):
    """"강원도(태백, 동해평지)" 하나 → {sido, all, areas, raw_areas, excludes} (모르는 것은 None)."""
    m = re.match(r"^([^()]+?)\s*(?:\((.*)\))?$", token)
    if not m:
        return None
    head, inner = m.group(1).strip(), m.group(2)
    if head in LONE_AREAS:
        sido, stems = LONE_AREAS[head]
        return {"sido": sido, "all": False, "areas": stems, "raw": [token], "excludes": []}
    if SEA_RE.search(head):
        return {"sido": None, "sea": True, "raw": [token]}
    sido = SIDO.get(head)
    if sido is None:
        # 모르는 형태 — 버리지 않고 원문으로 남긴다(앱은 표시만, 맞춰 보지는 않는다)
        return {"sido": None, "raw": [token]}
    if inner is None:
        return {"sido": sido, "all": True, "areas": [], "raw": [], "excludes": []}
    raw = split_top(inner)
    # "서귀포시남부 제외" — 괄호 안이 통째로 제외 목록이면 '그 시·도 전체 중 일부 빼고'
    if len(raw) == 1 and raw[0].endswith("제외") and "(" not in raw[0]:
        ex = [x.strip() for x in raw[0][:-2].split(",") if x.strip()]
        return {"sido": sido, "all": True, "areas": [], "raw": raw,
                "excludes": sorted({area_stem(x) for x in ex} | set(ex))}
    areas = []
    for a in raw:
        if sido == "서울" and a in SEOUL_ZONES:
            areas += SEOUL_ZONES[a]
        else:
            areas.append(area_stem(a))
    return {"sido": sido, "all": False, "areas": sorted(set(areas)), "raw": raw, "excludes": []}


LINE_RE = re.compile(r"^o\s*(\S+?)(주의보|경보)\s*:\s*(.+)$")


def parse_t6(t6):
    """발효 중 특보 요약 → [{kind, level, groups, sea}]"""
    out = []
    for line in (t6 or "").replace("\r", "").split("\n"):
        line = line.strip()
        m = LINE_RE.match(line)
        if not m:
            continue
        kind, level, rest = m.group(1), m.group(2), m.group(3)
        groups, sea = [], 0
        for tok in split_top(rest):
            g = parse_group(tok)
            if g is None:
                continue
            if g.get("sea"):
                sea += 1
                continue
            groups.append({k: v for k, v in g.items() if k != "sea"})
        out.append({"kind": kind, "level": level, "groups": groups, "sea": sea})
    return out


PRE_HEAD_RE = re.compile(r"^\(\d+\)\s*(\S+?)\s*예비특보")
PRE_LINE_RE = re.compile(r"^o\s*(.+?)\s*:\s*(.+)$")


def parse_t7(t7):
    """예비특보 → [{kind, when, groups}]"""
    out, kind = [], None
    for line in (t7 or "").replace("\r", "").split("\n"):
        line = line.strip()
        h = PRE_HEAD_RE.match(line)
        if h:
            kind = h.group(1)
            continue
        m = PRE_LINE_RE.match(line)
        if m and kind:
            groups = [g for g in (parse_group(t) for t in split_top(m.group(2))) if g and not g.get("sea")]
            out.append({"kind": kind, "when": m.group(1), "groups": groups})
    return out


def parse_intensity(inT):
    """"최대진도 Ⅳ(경북,충북),Ⅲ(강원)" → {"경북":4, "충북":4, "강원":3}. "전남광주" 는 둘로 나눈다."""
    out = {}
    s = (inT or "").replace("최대진도", "").strip()
    for m in re.finditer(r"([ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫ])\(([^)]*)\)", s):
        lv = ROMAN[m.group(1)]
        for name in m.group(2).split(","):
            name = name.strip()
            keys = ["전남", "광주"] if name == "전남광주" else [SIDO.get(name, name)]
            for k in keys:
                out[k] = max(out.get(k, 0), lv)
    return out


def max_intensity(inT):
    m = re.search(r"([ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫ])", inT or "")
    return ROMAN[m.group(1)] if m else 0


def quakes_from(items):
    """같은 지진(발생시각)의 속보·정보 여러 건 → 가장 늦게 낸 것 하나. 국내 진도 정보가 있는 것만."""
    best = {}
    for it in items:
        tm = str(it.get("tmEqk") or "")
        if not tm:
            continue
        if tm not in best or int(it.get("tmFc") or 0) > int(best[tm].get("tmFc") or 0):
            best[tm] = it
    out = []
    for tm, it in best.items():
        inten = parse_intensity(it.get("inT"))
        if not inten:
            continue   # 국내에서 느낄 수 없는 지진(국외·진도 없음)은 앱에 안 보낸다
        out.append({
            "tm_eqk": tm,
            "tm_fc": str(it.get("tmFc")),
            "fc_tp": int(it.get("fcTp") or 0),
            "loc": it.get("loc") or "",
            "lat": it.get("lat"),
            "lon": it.get("lon"),
            "mt": it.get("mt"),
            "dep": it.get("dep"),
            "max_int": max_intensity(it.get("inT")),
            "intensity": inten,
            "int_text": it.get("inT") or "",
        })
    return sorted(out, key=lambda q: q["tm_eqk"], reverse=True)


# ── 네트워크 ────────────────────────────────────────────────────────
def load_key():
    k = os.environ.get("DATA_GO_KR_KEY", "").strip()
    if k:
        return k
    for p in KEY_FILES:
        if p.exists():
            m = re.search(r"^DATA_GO_KR_KEY\s*=\s*(.+)$", p.read_text(encoding="utf-8"), re.M)
            if m:
                return m.group(1).strip().strip("\"'")
    raise SystemExit("[중단] DATA_GO_KR_KEY 가 없다")


def call(path, key, **params):
    # ⚠️디코딩 키는 반드시 URL 인코딩해서 보낸다 — 빠뜨리면 '등록되지 않은 서비스키'(403)라
    #   **키가 안 된 것처럼 보인다**(2026-11-20 첫 확인 때 그렇게 90분을 기다렸다).
    q = urllib.parse.urlencode({"serviceKey": key, "pageNo": 1, "numOfRows": 100, "dataType": "JSON", **params})
    last = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(f"{BASE}/{path}?{q}", headers={"User-Agent": "app-data-collector/1.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                j = json.loads(r.read().decode("utf-8"))
            head = j["response"]["header"]
            if head.get("resultCode") == "03":   # NODATA_ERROR — 정상적인 '없음'
                return []
            if head.get("resultCode") != "00":
                raise RuntimeError(f"{path} resultCode={head.get('resultCode')} {head.get('resultMsg')}")
            items = j["response"]["body"]["items"]
            items = items.get("item") if isinstance(items, dict) else items
            return items or []
        except Exception as e:   # 응답 본문에 키가 실리지 않게 메시지만 남긴다
            last = str(e).replace(key, "***").replace(urllib.parse.quote(key, safe=""), "***")
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(last)


def build(key):
    now = datetime.now(KST)
    status = call("WthrWrnInfoService/getPwnStatus", key)
    if not status:
        raise RuntimeError("특보현황 응답이 비었다")
    s = status[0]
    eqk = call("EqkInfoService/getEqkMsg", key,
               fromTmFc=(now - timedelta(days=2)).strftime("%Y%m%d"), toTmFc=now.strftime("%Y%m%d"))
    return {
        "schema": 1,
        "app": APP,
        "source": "kma",
        "generated_at": now.isoformat(timespec="seconds"),
        "warn_tm_fc": str(s.get("tmFc") or ""),
        "warn_tm_ef": str(s.get("tmEf") or ""),
        "warnings": parse_t6(s.get("t6")),
        "preliminary": parse_t7(s.get("t7")),
        "t6": s.get("t6") or "",
        "t7": s.get("t7") or "",
        "earthquakes": quakes_from(eqk),
    }


def content_of(payload):
    """커밋 여부 판단용 — 생성시각만 바뀐 것은 내용 변경이 아니다."""
    return {k: v for k, v in payload.items() if k != "generated_at"}


def main():
    if "--selftest" in sys.argv:
        return selftest()
    key = load_key()
    payload = build(key)
    old = None
    if OUT.exists():
        try:
            old = json.loads(OUT.read_text(encoding="utf-8"))
        except Exception:
            old = None
    changed = old is None or content_of(old) != content_of(payload)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    w = payload["warnings"]
    print(f"[{APP}] 특보 {len(w)}종 · 예비 {len(payload['preliminary'])} · 지진 {len(payload['earthquakes'])}"
          f" · 발표 {payload['warn_tm_fc']} · {'내용 바뀜' if changed else '내용 같음'}")
    # 실행기(refresh_haneul.ps1)가 이 줄로 커밋 여부를 정한다
    print("CHANGED" if changed else "SAME")


def selftest():
    t6 = ("o 강풍주의보 : 서해5도(백령도.대청도), 전라남도(흑산도.홍도), 제주도(서귀포시남부 제외), 울릉도.독도\r\n"
          "o 풍랑주의보 : 동해남부남쪽안쪽먼바다, 제주도전해상\r\n"
          "o 대설주의보 : 전라남도(영광(낙월면 제외)), 제주도(제주도산지)\r\n"
          "o 호우경보 : 서울(서울동남권, 서울서북권), 경기도, 강원도(태백, 동해평지, 동해산지)")
    w = parse_t6(t6)
    assert [x["kind"] + x["level"] for x in w] == ["강풍주의보", "풍랑주의보", "대설주의보", "호우경보"], w
    gale = w[0]["groups"]
    assert gale[0] == {"sido": "인천", "all": False, "areas": ["옹진"], "raw": ["서해5도(백령도.대청도)"], "excludes": []}, gale[0]
    assert gale[1]["sido"] == "전남" and gale[1]["areas"] == ["흑산도.홍도"], gale[1]
    assert gale[2]["sido"] == "제주" and gale[2]["all"] and "서귀포시남부" in gale[2]["excludes"], gale[2]
    assert gale[3]["sido"] == "경북" and gale[3]["areas"] == ["울릉"], gale[3]
    assert w[1]["groups"] == [] and w[1]["sea"] == 2, w[1]
    assert w[2]["groups"][0]["areas"] == ["영광"], w[2]
    rain = w[3]["groups"]
    assert rain[0]["sido"] == "서울" and "강남" in rain[0]["areas"] and "마포" in rain[0]["areas"] and "노원" not in rain[0]["areas"]
    assert rain[1] == {"sido": "경기", "all": True, "areas": [], "raw": [], "excludes": []}, rain[1]
    assert rain[2]["areas"] == ["동해", "태백"], rain[2]
    p = parse_t7("(1) 대설 예비특보\r\no 11월 21일 새벽(00시~06시) : 전북자치도(고창, 순창, 정읍, 부안(위도면 제외))")
    assert p[0]["kind"] == "대설" and p[0]["groups"][0]["sido"] == "전북" and "부안" in p[0]["groups"][0]["areas"], p
    i = parse_intensity("최대진도 Ⅳ(경북,충북),Ⅲ(강원,세종,전북),Ⅱ(경기,경남,대구,대전,전남광주,충남)")
    assert i["경북"] == 4 and i["강원"] == 3 and i["광주"] == 2 and i["전남"] == 2 and "서울" not in i, i
    assert parse_intensity("최대진도 Ⅰ") == {} and max_intensity("최대진도 Ⅰ") == 1
    q = quakes_from([
        {"tmEqk": 20261117231017, "tmFc": 202611172310, "fcTp": 14, "inT": "최대진도 Ⅳ(경북)", "mt": 3.7},
        {"tmEqk": 20261117231017, "tmFc": 202611172314, "fcTp": 3, "inT": "최대진도 Ⅳ(경북,충북)", "mt": 3.6},
        {"tmEqk": 20261119041423, "tmFc": 202611190441, "fcTp": 2, "inT": "", "mt": 6.1},
    ])
    assert len(q) == 1 and q[0]["fc_tp"] == 3 and q[0]["mt"] == 3.6 and q[0]["intensity"]["충북"] == 4, q
    print("selftest ok")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print(f"[{APP}] 실패: {e}")
        sys.exit(1)
