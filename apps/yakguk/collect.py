# -*- coding: utf-8 -*-
"""약국 찾기 앱 — 전국 약국 운영시간 + 교차검증 → 시도별 JSON.

설계 문서: D:\\claude_workspace\\yakguk\\docs\\CROSS_VALIDATION.md (규칙의 1차 출처)

소스
  S1 국립중앙의료원 약국 FullData (data.go.kr 15000576) — 운영시간의 유일한 원천
  S2 행정안전부 인허가 건강_약국 CSV (data.go.kr 15045036) — 영업상태·폐업·휴업
  S5 구글 공개 한국 공휴일 달력(ICS) — 공휴일·대체공휴일 (S4 한국천문연구원은 활용신청 후 추가)

주간 실행: refresh.bat 은 매일 돌지만 직전 발행 6일 이내면 즉시 건너뛴다. 강제는 --force.

🚨 이 수집기는 실패해도 exit 1 하지 않는다. refresh.bat 은 수집기 하나라도 실패하면
   **모든 앱의 푸시를 막는다** — 약국 API 장애가 공고알리미·동네복지까지 멈추게 된다.
   가드에 걸리면 data/yakguk/ 의 약국 파일을 건드리지 않고 validation.json 에만 사유를 남긴다.
   (대신 조용해지므로 감시가 validation.json 의 published 를 봐야 한다.)

인증키(S1): 환경변수 YAKGUK_NMC_KEY → %USERPROFILE%\\.yakguk_key → D:\\claude_workspace\\yakguk\\.env
  ⚠️ 포털의 **인코딩 키**를 URL 에 그대로 붙인다. requests params 로 넘기면 이중 인코딩된다.
"""
import argparse
import collections
import csv
import hashlib
import io
import json
import math
import os
import re
import sys
import time
import traceback
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone

import requests

try:  # cmd(cp949) 콘솔에서 인쇄가 죽지 않게
    sys.stdout.reconfigure(errors='replace')
    sys.stderr.reconfigure(errors='replace')
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(HERE)), 'data', 'yakguk')
KST = timezone(timedelta(hours=9))
SCHEMA = 1
APP = 'yakguk'
PUBLISH_INTERVAL_DAYS = 6

NMC_URL = 'https://apis.data.go.kr/B552657/ErmctInsttInfoInqireService/getParmacyFullDown'
MOIS_URL = 'https://file.localdata.go.kr/file/download/pharmacies/info'
MOIS_REFERER = 'https://file.localdata.go.kr/file/pharmacies/info'
ICS_URL = ('https://calendar.google.com/calendar/ical/'
           'ko.south_korea%23holiday%40group.v.calendar.google.com/public/basic.ics')
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/125.0 Safari/537.36')

# ── 시도 정규화 ───────────────────────────────────────────────────────────────
# 주소 첫 토막만 본다. "경기도 광주시"를 광주로 바꾸면 안 되기 때문이다.
# 광주·전남은 2026-07-01 전남광주통합특별시로 출범했다 — 옛 표기와 새 표기가 섞여 온다.
SIDO_ALIAS = {
    '서울': '서울특별시', '서울특별시': '서울특별시',
    '부산': '부산광역시', '부산광역시': '부산광역시',
    '대구': '대구광역시', '대구광역시': '대구광역시',
    '인천': '인천광역시', '인천광역시': '인천광역시',
    '대전': '대전광역시', '대전광역시': '대전광역시',
    '울산': '울산광역시', '울산광역시': '울산광역시',
    '세종': '세종특별자치시', '세종특별자치시': '세종특별자치시',
    '경기': '경기도', '경기도': '경기도',
    '강원': '강원특별자치도', '강원도': '강원특별자치도', '강원특별자치도': '강원특별자치도',
    '충북': '충청북도', '충청북도': '충청북도',
    '충남': '충청남도', '충청남도': '충청남도',
    '전북': '전북특별자치도', '전라북도': '전북특별자치도', '전북특별자치도': '전북특별자치도',
    '광주': '전남광주통합특별시', '광주광역시': '전남광주통합특별시',
    '전남': '전남광주통합특별시', '전라남도': '전남광주통합특별시',
    '전남광주통합특별시': '전남광주통합특별시',
    '경북': '경상북도', '경상북도': '경상북도',
    '경남': '경상남도', '경상남도': '경상남도',
    '제주': '제주특별자치도', '제주특별자치도': '제주특별자치도',
}
# 파일명은 ASCII 로 (URL 인코딩 사고 방지). 순서 = 앱의 지역 목록 순서.
SIDO_CODE = collections.OrderedDict([
    ('서울특별시', 'seoul'), ('부산광역시', 'busan'), ('대구광역시', 'daegu'),
    ('인천광역시', 'incheon'), ('대전광역시', 'daejeon'), ('울산광역시', 'ulsan'),
    ('세종특별자치시', 'sejong'), ('경기도', 'gyeonggi'), ('강원특별자치도', 'gangwon'),
    ('충청북도', 'chungbuk'), ('충청남도', 'chungnam'), ('전북특별자치도', 'jeonbuk'),
    ('전남광주통합특별시', 'jeonnam_gwangju'), ('경상북도', 'gyeongbuk'),
    ('경상남도', 'gyeongnam'), ('제주특별자치도', 'jeju'),
])


def region_of(addr):
    """주소 → (정규 시도, 시군구 표기). 인식 못 하면 (None, None)."""
    t = (addr or '').split()
    if not t or t[0] not in SIDO_ALIAS:
        return None, None
    sido = SIDO_ALIAS[t[0]]
    rest = t[1:]
    if sido == '세종특별자치시':
        return sido, '세종시'
    if not rest:
        return sido, None
    # 옛 광주광역시 주소는 구 이름이 다른 시와 겹치므로(동구·서구…) '광주 동구'로 적는다
    if t[0] in ('광주', '광주광역시'):
        return sido, '광주 ' + rest[0]
    # 새 이름으로 적힌 옛 광주 주소('전남광주통합특별시 동구 …') — 전남엔 '구'로 끝나는 시군이 없다
    if t[0] == '전남광주통합특별시' and rest[0].endswith('구'):
        return sido, '광주 ' + rest[0]
    sgg = rest[0]
    if sgg.endswith('시') and len(rest) > 1 and rest[1].endswith('구'):
        sgg = f'{sgg} {rest[1]}'
    return sido, sgg


# ── 메모 칸 해석 ──────────────────────────────────────────────────────────────
_LUNCH_RE = re.compile(
    r'(?:휴게|점심|브레이크)[^0-9]{0,12}(\d{1,2})\s*[:시]?\s*(\d{2})?\s*분?\s*[~\-–]\s*(\d{1,2})\s*[:시]?\s*(\d{2})?')
_CALL_RE = re.compile(r'전화\s*(?:확인|문의|주세요|주시)|격주|교대|번갈아|당번제|\d\s*[,·]\s*\d\s*주|\d째\s*주|째주')
_NIGHT_RE = re.compile(r'공공\s*(?:심야|야간)')


def parse_lunch(text):
    """'휴게시간 13:00-14:00' → '1300-1400'. 그럴듯하지 않으면 None."""
    m = _LUNCH_RE.search(text or '')
    if not m:
        return None
    h1, m1, h2, m2 = int(m.group(1)), int(m.group(2) or 0), int(m.group(3)), int(m.group(4) or 0)
    a, b = h1 * 60 + m1, h2 * 60 + m2
    if not (10 * 60 <= a <= 16 * 60 and 0 < b - a <= 150):
        return None
    return f'{h1:02d}{m1:02d}-{h2:02d}{m2:02d}'


def call_first(text):
    return bool(_CALL_RE.search(text or ''))


def is_public_night(*texts):
    return any(_NIGHT_RE.search(t or '') for t in texts)


def valid_hhmm(v):
    return bool(re.fullmatch(r'\d{4}', v or '')) and int(v[:2]) <= 30 and int(v[2:]) < 60


# ── 짝짓기 키 ─────────────────────────────────────────────────────────────────
def key_phone(s):
    d = re.sub(r'\D', '', s or '')
    return d[-8:] if len(d) >= 8 else None


def key_name(s):
    return re.sub(r'[\s()（）\[\]·.,\-]', '', s or '').replace('약국', '')


def key_road(s):
    s = re.sub(r'\(.*?\)', '', s or '')
    m = re.search(r'([가-힣0-9]+(?:로|길)\s*\d+(?:-\d+)?)', s)
    return re.sub(r'\s', '', m.group(1)) if m else None


def haversine(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    return 2 * r * math.asin(math.sqrt(math.sin(dp / 2) ** 2 +
                                       math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2))


# ── 수집 ──────────────────────────────────────────────────────────────────────
def load_key():
    k = os.getenv('YAKGUK_NMC_KEY')
    if k:
        return k.strip()
    for p in (os.path.join(os.path.expanduser('~'), '.yakguk_key'),
              r'D:\claude_workspace\yakguk\.env'):
        if os.path.exists(p):
            txt = open(p, encoding='utf-8').read().strip()
            for line in txt.splitlines():
                if line.startswith('NMC_PHARM_KEY_ENC='):
                    return line.split('=', 1)[1].strip()
            if '=' not in txt.splitlines()[0]:
                return txt.splitlines()[0].strip()
    raise RuntimeError('S1 인증키 없음(YAKGUK_NMC_KEY / ~/.yakguk_key / yakguk/.env)')


def _get(url, **kw):
    """일시 장애 재시도 3회. SSL EOF·연결 끊김이 흔하다."""
    last, timeout = None, kw.pop('timeout', 90)
    for i in range(3):
        try:
            r = requests.get(url, timeout=timeout, **kw)
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            last = e
            time.sleep(3 * (i + 1))
    raise last


def fetch_nmc(key):
    items, page, total = [], 1, None
    while True:
        r = _get(f'{NMC_URL}?serviceKey={key}&pageNo={page}&numOfRows=1000')
        root = ET.fromstring(r.content)
        code = root.findtext('header/resultCode')
        if code != '00':
            raise RuntimeError(f'S1 API resultCode={code} {root.findtext("header/resultMsg")}')
        total = int(root.findtext('body/totalCount') or 0)
        batch = [{c.tag: (c.text or '').strip() for c in it} for it in root.iter('item')]
        items.extend(batch)
        if not batch or len(items) >= total:
            break
        page += 1
        time.sleep(0.3)
    return items, total


def fetch_mois():
    r = _get(MOIS_URL, headers={'User-Agent': UA, 'Referer': MOIS_REFERER,
                                'Accept-Language': 'ko-KR,ko;q=0.9'}, timeout=300)
    raw = r.content
    for enc in ('utf-8-sig', 'cp949'):  # 헤더는 UTF-8 이라지만 실제는 cp949 였다(2026-09-11)
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise RuntimeError('S2 CSV 인코딩 판별 실패')
    rows = list(csv.DictReader(io.StringIO(text)))
    need = {'사업장명', '영업상태명', '폐업일자', '휴업시작일자', '휴업종료일자', '도로명주소', '전화번호'}
    if not rows or not need <= set(rows[0]):
        raise RuntimeError(f'S2 CSV 칸이 바뀌었다: {sorted(set(rows[0]) if rows else [])[:12]}')
    return rows


# 관공서 공휴일로 확정 취급하는 이름(음력·고정). 나머지는 holiday? — CROSS_VALIDATION 4절.
CONFIRMED_HOLIDAY_NAMES = ('새해첫날', '삼일절', '어린이날', '부처님오신날', '현충일', '광복절',
                           '설날', '추석', '개천절', '한글날', '크리스마스', '성탄절')
FIXED_MMDD = ('0101', '0301', '0505', '0606', '0815', '1003', '1009', '1225')


def fetch_holidays(today):
    r = _get(ICS_URL, headers={'User-Agent': UA})
    text = r.content.decode('utf-8')
    years = {str(today.year), str(today.year + 1)}
    out = {}
    for ev in re.findall(r'BEGIN:VEVENT(.*?)END:VEVENT', text, flags=re.S):
        d = re.search(r'DTSTART;VALUE=DATE:(\d{8})', ev)
        s = re.search(r'SUMMARY:(.*)', ev)
        desc = re.search(r'DESCRIPTION:(.*)', ev)
        if not (d and s) or d.group(1)[:4] not in years:
            continue
        if not desc or not desc.group(1).strip().startswith('공휴일'):
            continue  # '기념일'(어버이날 등)은 쉬는 날이 아니다
        name = s.group(1).strip()
        base = name.replace('쉬는 날', '').replace('연휴', '').strip()
        confirmed = ('선거' in base) or any(base.startswith(n) for n in CONFIRMED_HOLIDAY_NAMES)
        out[d.group(1)] = {'date': d.group(1), 'name': name, 'status': 'holiday' if confirmed else 'holiday?'}
    return [out[k] for k in sorted(out)]


# ── 조립·교차검증 ─────────────────────────────────────────────────────────────
def build(nmc, mois, today):
    idx_key = collections.defaultdict(list)
    idx_phone = collections.defaultdict(list)
    for r in mois:
        idx_key[(key_name(r['사업장명']), key_road(r['도로명주소']))].append(r)
        p = key_phone(r['전화번호'])
        if p:
            idx_phone[p].append(r)

    tstr = today.strftime('%Y-%m-%d')
    cutoff_closed = (today - timedelta(days=730)).strftime('%Y-%m-%d')
    verdict = collections.Counter()
    matched_mois_ids = set()
    recs, dropped, unknown_region = [], [], 0

    for it in nmc:
        sido, sgg = region_of(it.get('dutyAddr'))
        if not sido:
            unknown_region += 1
            continue
        how, cand = None, idx_key.get((key_name(it.get('dutyName')), key_road(it.get('dutyAddr'))), [])
        if cand:
            how = 'name_road'
        else:
            p = key_phone(it.get('dutyTel1'))
            cand = [r for r in idx_phone.get(p, []) if key_name(r['사업장명'])[:2] == key_name(it.get('dutyName'))[:2]] if p else []
            how = 'phone' if cand else None

        v, extra = 'unmatched', {}
        if cand:
            best = sorted(cand, key=lambda r: r['영업상태명'] != '영업/정상')[0]
            matched_mois_ids.add(id(best))
            st = best['영업상태명']
            if st == '영업/정상':
                v = 'ok'
            elif st == '휴업':
                s, e = best.get('휴업시작일자', ''), best.get('휴업종료일자', '')
                if s and s <= tstr and (not e or tstr <= e):
                    v, extra = 'suspended', {'suspUntil': e or None}
                else:
                    v = 'ok'
            elif st == '폐업':
                reopened = [r for r in idx_phone.get(key_phone(it.get('dutyTel1')), []) if r['영업상태명'] == '영업/정상']
                if reopened:
                    v = 'ok'  # 같은 전화로 영업 중인 인허가가 따로 있다 = 이름·주소만 바뀐 것
                elif (best.get('폐업일자') or '') >= cutoff_closed:
                    v = 'closed'
                else:
                    v, extra = 'closed?', {'closedOn': best.get('폐업일자') or None}
            else:
                v = 'closed?'
            # 좌표 대조 (S2 는 EPSG:5174 라 변환이 필요 — pyproj 가 없으면 건너뛴다)
            if v in ('ok', 'suspended') and _TO_WGS and best.get('좌표정보(X)') and best.get('좌표정보(Y)'):
                try:
                    lon, lat = _TO_WGS.transform(float(best['좌표정보(X)']), float(best['좌표정보(Y)']))
                    if haversine(float(it['wgs84Lat']), float(it['wgs84Lon']), lat, lon) > 1000:
                        extra['geo'] = '?'
                        verdict['geo?'] += 1
                except (ValueError, KeyError):
                    pass
        verdict[v] += 1
        if v == 'closed':
            dropped.append(it.get('hpid'))
            continue
        recs.append(make_rec(it, sido, sgg, v, extra))

    # S2 영업 중인데 S1 에 없는 곳 → 운영시간 없이 넣는다(결과 누락 불만 대응)
    nmc_keys = {(key_name(i.get('dutyName')), key_road(i.get('dutyAddr'))) for i in nmc}
    nmc_phones = {key_phone(i.get('dutyTel1')) for i in nmc}
    for r in mois:
        if r['영업상태명'] != '영업/정상' or id(r) in matched_mois_ids:
            continue
        if (key_name(r['사업장명']), key_road(r['도로명주소'])) in nmc_keys or key_phone(r['전화번호']) in nmc_phones:
            continue
        sido, sgg = region_of(r['도로명주소'] or r.get('지번주소'))
        if not sido or not _TO_WGS or not (r.get('좌표정보(X)') and r.get('좌표정보(Y)')):
            verdict['no_hours_skipped'] += 1
            continue
        try:
            lon, lat = _TO_WGS.transform(float(r['좌표정보(X)']), float(r['좌표정보(Y)']))
        except ValueError:
            verdict['no_hours_skipped'] += 1
            continue
        verdict['no_hours'] += 1
        recs.append({
            'id': 'L' + re.sub(r'\D', '', r.get('관리번호', ''))[-12:],
            'name': r['사업장명'], 'addr': r['도로명주소'] or r.get('지번주소', ''),
            'tel': r['전화번호'], 'lat': round(lat, 6), 'lon': round(lon, 6),
            'sido': sido, 'sgg': sgg, 't': [''] * 8, 'v': 'no_hours', 'src': 'mois',
        })
    return recs, verdict, dropped, unknown_region


def make_rec(it, sido, sgg, v, extra):
    t = []
    for i in range(1, 9):
        s, c = it.get(f'dutyTime{i}s', ''), it.get(f'dutyTime{i}c', '')
        t.append(f'{s}-{c}' if valid_hhmm(s) and valid_hhmm(c) else '')
    etc, inf = it.get('dutyEtc', ''), it.get('dutyInf', '')
    rec = {
        'id': it.get('hpid'), 'name': it.get('dutyName'), 'addr': it.get('dutyAddr'),
        'tel': it.get('dutyTel1'), 'lat': round(float(it['wgs84Lat']), 6), 'lon': round(float(it['wgs84Lon']), 6),
        'sido': sido, 'sgg': sgg, 't': t, 'v': v, 'src': 'nmc',
    }
    if etc:
        rec['etc'] = etc[:300]
    if inf:
        rec['inf'] = inf[:200]
    if it.get('dutyMapimg'):
        rec['map'] = it['dutyMapimg'][:120]
    lunch = parse_lunch(etc) or parse_lunch(inf)
    if lunch:
        rec['lunch'] = lunch
    if call_first(etc) or call_first(inf):
        rec['call'] = True
    if is_public_night(it.get('dutyName'), inf, etc):
        rec['night'] = True
    rec.update({k: val for k, val in extra.items() if val is not None})
    return rec


try:
    from pyproj import Transformer
    _TO_WGS = Transformer.from_crs('EPSG:5174', 'EPSG:4326', always_xy=True)
except Exception:  # pyproj 없으면 좌표 대조·S2 전용 약국을 건너뛴다(가드가 잡는다)
    _TO_WGS = None


# ── 가드 ──────────────────────────────────────────────────────────────────────
def guards(nmc, nmc_total, recs, verdict, holidays, prev, today):
    g = []

    def add(name, ok, value, rule):
        g.append({'name': name, 'ok': bool(ok), 'value': value, 'rule': rule})

    n = len(nmc)
    add('s1_complete', n == nmc_total and n > 0, f'{n}/{nmc_total}', 'S1 받은 수 == totalCount')
    prev_total = prev.get('nmc_total') if prev else None
    if prev_total:
        ch = abs(n - prev_total) / prev_total
        add('s1_total_change', ch <= 0.03, f'{n} vs {prev_total} ({ch:.1%})', '직전 대비 ±3%')
    else:
        add('s1_total_min', n >= 20000, n, '첫 발행 ≥ 20,000')

    weekday = sum(1 for i in nmc if i.get('dutyTime1s') and i.get('dutyTime1c'))
    add('weekday_hours_fill', weekday / max(n, 1) >= 0.95, f'{weekday / max(n, 1):.1%}', '월요일 운영시간 기재율 ≥ 95%')

    matched = sum(verdict[k] for k in ('ok', 'suspended', 'closed', 'closed?'))
    add('s1_s2_match', matched / max(n, 1) >= 0.95, f'{matched / max(n, 1):.1%}', 'S1↔S2 짝 비율 ≥ 95%')
    cl = verdict['closed'] + verdict['closed?']
    add('closed_ratio', cl / max(n, 1) <= 0.02, f'{cl} ({cl / max(n, 1):.2%})', '폐업·폐업의심 ≤ 2%')
    add('suspended_ratio', verdict['suspended'] / max(n, 1) <= 0.01, verdict['suspended'], '휴업 ≤ 1%')

    bad_geo = sum(1 for r in recs if not (33 <= r['lat'] <= 39 and 124 <= r['lon'] <= 132))
    add('geo_bbox', bad_geo == 0, bad_geo, '좌표 위도 33-39 · 경도 124-132')

    by_sido = collections.Counter(r['sido'] for r in recs)
    missing = [s for s in SIDO_CODE if by_sido[s] == 0]
    add('all_sido_present', not missing, missing, '모든 시도 ≥ 1')
    if prev and prev.get('sido_counts'):
        worst = []
        for s, c in by_sido.items():
            p = prev['sido_counts'].get(s)
            if p and abs(c - p) / p > 0.10:
                worst.append(f'{s} {p}->{c}')
        add('sido_change', not worst, worst, '시도별 직전 대비 ±10%')

    this_year = str(today.year)
    have = {h['date'] for h in holidays if h['status'] == 'holiday'}
    miss = [md for md in FIXED_MMDD if this_year + md not in have]
    add('fixed_holidays', not miss, miss, f'{this_year} 고정 공휴일 8개 포함')
    return g


# ── 쓰기 ──────────────────────────────────────────────────────────────────────
def dump(path, obj):
    body = json.dumps(obj, ensure_ascii=False, separators=(',', ':'))
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(body)
    return body


def load_prev():
    p = os.path.join(OUT_DIR, 'index.json')
    if not os.path.exists(p):
        return None
    try:
        idx = json.load(open(p, encoding='utf-8'))
        return {'generated_at': idx.get('generated_at'), 'nmc_total': idx.get('basis', {}).get('nmc_total'),
                'sido_counts': {r['name']: r['count'] for r in idx.get('regions', [])}}
    except Exception:
        return None


def write_validation(obj):
    os.makedirs(OUT_DIR, exist_ok=True)
    dump(os.path.join(OUT_DIR, 'validation.json'), obj)


def publish(recs, holidays, basis, now):
    os.makedirs(OUT_DIR, exist_ok=True)
    groups = collections.defaultdict(list)
    for r in recs:
        groups[r['sido']].append(r)
    regions = []
    for sido, code in SIDO_CODE.items():
        rows = sorted(groups.get(sido, []), key=lambda r: (r.get('sgg') or '', r['name'] or ''))
        fname = f's_{code}.json'
        # 지역 파일엔 generated_at 을 넣지 않는다 — 넣으면 내용이 같아도 매주 전 파일에 diff 가 난다
        body = dump(os.path.join(OUT_DIR, fname),
                    {'schema': SCHEMA, 'app': APP, 'region': sido, 'count': len(rows), 'pharmacies': rows})
        lats, lons = [r['lat'] for r in rows], [r['lon'] for r in rows]
        regions.append({
            'code': code, 'name': sido, 'file': fname, 'count': len(rows),
            'hash': hashlib.sha1(body.encode('utf-8')).hexdigest()[:12],
            'bbox': [round(min(lats), 4), round(min(lons), 4), round(max(lats), 4), round(max(lons), 4)] if rows else None,
            'sgg': sorted({r['sgg'] for r in rows if r.get('sgg')}),
        })
    dump(os.path.join(OUT_DIR, 'holidays.json'),
         {'schema': SCHEMA, 'app': APP, 'count': len(holidays), 'holidays': holidays})
    dump(os.path.join(OUT_DIR, 'index.json'), {
        'schema': SCHEMA, 'app': APP, 'source': 'nmc+mois', 'generated_at': now,
        'basis': basis, 'count': sum(r['count'] for r in regions), 'regions': regions,
    })
    return regions


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--force', action='store_true', help='주간 간격 무시')
    ap.add_argument('--dry-run', action='store_true', help='수집·검증만, 파일 안 씀')
    a = ap.parse_args()

    now_dt = datetime.now(KST)
    now, today = now_dt.isoformat(timespec='seconds'), now_dt.date()
    prev = load_prev()
    if prev and prev.get('generated_at') and not a.force:
        last = datetime.fromisoformat(prev['generated_at'])
        if now_dt - last < timedelta(days=PUBLISH_INTERVAL_DAYS):
            print(f'[약국] 직전 발행 {prev["generated_at"][:10]} — 주간 간격 전이라 건너뜀')
            return 0

    val = {'generated_at': now, 'published': False, 'reasons': [], 'guards': [], 'verdicts': {}}
    try:
        key = load_key()
        t0 = time.time()
        nmc, nmc_total = fetch_nmc(key)
        print(f'[약국] S1 {len(nmc)}/{nmc_total} ({time.time() - t0:.0f}s)')
        mois = fetch_mois()
        print(f'[약국] S2 {len(mois)}행')
        holidays = fetch_holidays(today)
        print(f'[약국] 공휴일 {len(holidays)}일 (확정 {sum(h["status"] == "holiday" for h in holidays)})')

        recs, verdict, dropped, unknown_region = build(nmc, mois, today)
        val['verdicts'] = dict(verdict)
        val['dropped_closed'] = len(dropped)
        val['unknown_region'] = unknown_region
        g = guards(nmc, nmc_total, recs, verdict, holidays, prev, today)
        val['guards'] = g
        failed = [x for x in g if not x['ok']]
        if not _TO_WGS:
            failed.append({'name': 'pyproj_missing', 'ok': False, 'value': None, 'rule': 'pip install pyproj'})
        for x in g:
            print(f'  {"OK " if x["ok"] else "NG "} {x["name"]:<20} {x["value"]}  ({x["rule"]})')
        if failed:
            val['reasons'] = [f['name'] for f in failed]
            print(f'[약국] 가드 걸림 {val["reasons"]} — 지난 데이터 유지, 발행 안 함')
        elif a.dry_run:
            val['reasons'] = ['dry_run']
            print('[약국] dry-run — 파일 안 씀')
            return 0
        else:
            mois_upd = max((r.get('데이터갱신시점') or '' for r in mois), default='')
            basis = {'nmc_total': len(nmc), 'nmc_fetched_at': now, 'mois_updated_at': mois_upd,
                     'holidays_source': 'google_ics'}
            regions = publish(recs, holidays, basis, now)
            val['published'] = True
            val['sido_counts'] = {r['name']: r['count'] for r in regions}
            print(f'[약국] 발행 {sum(r["count"] for r in regions)}곳 · {len(regions)}개 시도 · 판정 {dict(verdict)}')
    except Exception as e:
        val['reasons'] = ['exception']
        val['error'] = f'{type(e).__name__}: {str(e)[:300]}'
        print('[약국] 실패 — 지난 데이터 유지:', val['error'])
        traceback.print_exc()
    if not a.dry_run:
        write_validation(val)
    return 0  # 🚨 절대 1 을 돌려주지 말 것 — refresh.bat 이 다른 앱 푸시까지 막는다


if __name__ == '__main__':
    sys.exit(main())
