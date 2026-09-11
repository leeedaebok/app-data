# -*- coding: utf-8 -*-
"""약국 찾기 앱 — 전국 약국 운영시간 + 교차검증 → 시도별 JSON.

설계 문서: D:\\claude_workspace\\yakguk\\docs\\CROSS_VALIDATION.md (규칙의 1차 출처)

소스
  S1 국립중앙의료원 약국 FullData (data.go.kr 15000576) — 운영시간의 유일한 원천
  S2 행정안전부 인허가 건강_약국 CSV (data.go.kr 15045036) — 영업상태·폐업·휴업
  S3 건강보험심사평가원 약국정보 (data.go.kr 15001673) — 존재·개설일·좌표. 폐업 의심·좌표 어긋남의 캐스팅보트
  S4 한국천문연구원 특일 정보 (data.go.kr 15012690) — 관공서 공휴일 1차 기준
  S5 구글 공개 한국 공휴일 달력(ICS) — 공휴일 대조군(S4 장애 시 대체)

주간 실행: refresh.bat 은 매일 돌지만 직전 발행 6일 이내면 즉시 건너뛴다. 강제는 --force.
  단 공휴일이 10일 안에 있으면 **매일** 발행한다(publish_interval_days) — 명절 당번·임시 운영이 늦게 올라와도
  한 주를 놓치지 않게. 명절이 이 앱을 가장 많이 쓰는 때다(2026-09-11 운영자 승인).

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
HOLIDAY_LOOKAHEAD_DAYS = 10   # 이 안에 공휴일이 있으면 간격을 1일로

NMC_URL = 'https://apis.data.go.kr/B552657/ErmctInsttInfoInqireService/getParmacyFullDown'
MOIS_URL = 'https://file.localdata.go.kr/file/download/pharmacies/info'
MOIS_REFERER = 'https://file.localdata.go.kr/file/pharmacies/info'
ICS_URL = ('https://calendar.google.com/calendar/ical/'
           'ko.south_korea%23holiday%40group.v.calendar.google.com/public/basic.ics')
# S3 심평원 약국정보(15001673) — 존재 여부·개설일·좌표(WGS84). 이용허락: 공공누리 1유형(출처표시)
HIRA_URL = 'https://apis.data.go.kr/B551182/pharmacyInfoService/getParmacyBasisList'
# S4 한국천문연구원 특일 정보(15012690) — 관공서 공휴일·대체공휴일의 1차 기준
KASI_URL = 'https://apis.data.go.kr/B090041/openapi/service/SpcdeInfoService/getRestDeInfo'
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


def fetch_hira(key):
    items, page, total = [], 1, None
    while True:
        r = _get(f'{HIRA_URL}?serviceKey={key}&pageNo={page}&numOfRows=1000')
        root = ET.fromstring(r.content)
        code = root.findtext('header/resultCode')
        if code != '00':
            raise RuntimeError(f'S3 API resultCode={code} {root.findtext("header/resultMsg")}')
        total = int(root.findtext('body/totalCount') or 0)
        batch = [{c.tag: (c.text or '').strip() for c in it} for it in root.iter('item')]
        items.extend(batch)
        if not batch or len(items) >= total:
            break
        page += 1
        time.sleep(0.3)
    return items, total


def fetch_kasi(key, years):
    """{YYYYMMDD: 이름}. 대체공휴일은 관보 공포 뒤에야 올라온다 — 내년 것은 비어 있을 수 있다."""
    out = {}
    for y in years:
        r = _get(f'{KASI_URL}?serviceKey={key}&solYear={y}&numOfRows=100&_type=json')
        body = r.json()['response']
        if body['header']['resultCode'] != '00':
            raise RuntimeError(f'S4 API {body["header"]}')
        items = (body.get('body', {}).get('items') or {}).get('item') or []
        if isinstance(items, dict):  # 1건이면 배열이 아니라 객체로 온다
            items = [items]
        for it in items:
            if it.get('isHoliday') == 'Y':
                out[str(it['locdate'])] = it['dateName']
    return out


def merge_holidays(kasi, google):
    """CROSS_VALIDATION 4절. S4(천문연)가 1차 기준, S5(구글)는 대조군.
    둘 다 → holiday · 천문연만 → holiday(공식) · 구글만 → holiday?(앱이 평일·공휴일을 둘 다 보여준다).
    천문연을 못 받았으면(kasi=None) 구글의 이름 규칙으로 확정/불확실을 가른다."""
    if kasi is None:
        return google
    g = {h['date']: h for h in google}
    out = {}
    for d, name in kasi.items():
        out[d] = {'date': d, 'name': name, 'status': 'holiday', 'src': 'kasi+google' if d in g else 'kasi'}
    for d, h in g.items():
        if d not in out:
            out[d] = {'date': d, 'name': h['name'], 'status': 'holiday?', 'src': 'google'}
    return [out[k] for k in sorted(out)]


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
class HiraIndex:
    """S3 짝찾기. 이름+도로명 → 전화+이름앞2자 순(S2 와 같은 규칙)."""

    def __init__(self, hira):
        self.by_key = collections.defaultdict(list)
        self.by_phone = collections.defaultdict(list)
        for h in hira or []:
            self.by_key[(key_name(h.get('yadmNm')), key_road(h.get('addr')))].append(h)
            p = key_phone(h.get('telno'))
            if p:
                self.by_phone[p].append(h)

    def find(self, name, addr, tel):
        c = self.by_key.get((key_name(name), key_road(addr)))
        if c:
            return c[0]
        c = [h for h in self.by_phone.get(key_phone(tel), []) if key_name(h.get('yadmNm'))[:2] == key_name(name)[:2]]
        return c[0] if c else None


def _ymd(s):
    """'20240112' → '2024-01-12' (인허가 날짜 형식과 맞춘다)."""
    s = re.sub(r'\D', '', s or '')
    return f'{s[:4]}-{s[4:6]}-{s[6:8]}' if len(s) == 8 else ''


def build(nmc, mois, today, hira=None):
    """hira=None 이면 두 소스(S1×S2) 규칙, 목록이 오면 세 소스 규칙(CROSS_VALIDATION 2-2)."""
    hidx = HiraIndex(hira) if hira is not None else None
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
        h = hidx.find(it.get('dutyName'), it.get('dutyAddr'), it.get('dutyTel1')) if hidx else None
        if hidx is not None:
            verdict['s3_matched' if h else 's3_unmatched'] += 1
        if cand:
            verdict['s2_matched'] += 1
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
                closed_on = best.get('폐업일자') or ''
                reopened = [r for r in idx_phone.get(key_phone(it.get('dutyTel1')), []) if r['영업상태명'] == '영업/정상']
                if reopened:
                    v = 'ok'  # 같은 전화로 영업 중인 인허가가 따로 있다 = 이름·주소만 바뀐 것
                elif hidx is not None:
                    # 3자 판정(9/11 실측: 폐업 의심 178곳 중 177곳이 S3 에도 없었다)
                    if h is None:
                        v = 'closed'  # S2 폐업 + S3 없음 = 셋 중 둘이 "없다"
                    elif _ymd(h.get('estbDd')) > closed_on:
                        v = 'ok'  # S3 개설일이 폐업 뒤 = 재개업
                        verdict['reopened_by_s3'] += 1
                    else:
                        v, extra = 'closed?', {'closedOn': closed_on or None}  # 모순 — 표시만
                elif closed_on >= cutoff_closed:
                    v = 'closed'
                else:
                    v, extra = 'closed?', {'closedOn': closed_on or None}
            else:
                v = 'closed?'
            # 좌표 대조 (S2 는 EPSG:5174 라 변환이 필요 — pyproj 가 없으면 건너뛴다)
            if v in ('ok', 'suspended') and _TO_WGS and best.get('좌표정보(X)') and best.get('좌표정보(Y)'):
                try:
                    lon, lat = _TO_WGS.transform(float(best['좌표정보(X)']), float(best['좌표정보(Y)']))
                    a, b = float(it['wgs84Lat']), float(it['wgs84Lon'])
                    if haversine(a, b, lat, lon) > 1000:
                        fixed = _resolve_geo(a, b, lat, lon, h)
                        if fixed == 'nmc':
                            verdict['geo_nmc_ok'] += 1
                        elif fixed:
                            # S2·S3 가 서로 가깝고 S1 만 멀다 = S1 좌표가 틀렸다(9/11 실측 255/263)
                            extra['fixLat'], extra['fixLon'] = fixed
                            verdict['geo_fixed'] += 1
                        else:
                            extra['geo'] = '?'
                            verdict['geo?'] += 1
                except (ValueError, KeyError):
                    pass
        elif h is not None:
            v = 'ok'  # S2 에 짝이 없어도 S3 에 있으면 영업 중인 요양기관이다
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


GEO_AGREE_M = 200


def _resolve_geo(nlat, nlon, mlat, mlon, h):
    """S1·S2 가 1km+ 어긋날 때 S3 로 가른다. 'nmc'(S1 맞음) · (lat, lon)(S3 좌표로 교체) · None(모름)."""
    if not h:
        return None
    try:
        hlat, hlon = float(h['YPos']), float(h['XPos'])
    except (KeyError, ValueError):
        return None
    if haversine(nlat, nlon, hlat, hlon) < GEO_AGREE_M:
        return 'nmc'
    if haversine(mlat, mlon, hlat, hlon) < GEO_AGREE_M:
        return round(hlat, 6), round(hlon, 6)
    return None


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
    fix = (extra.pop('fixLat', None), extra.pop('fixLon', None))
    if fix[0] is not None:
        rec['lat'], rec['lon'] = fix
        rec['geoFixed'] = True  # 앱 표시용 아님 — 검증·추적용
    rec.update({k: val for k, val in extra.items() if val is not None})
    return rec


try:
    from pyproj import Transformer
    _TO_WGS = Transformer.from_crs('EPSG:5174', 'EPSG:4326', always_xy=True)
except Exception:  # pyproj 없으면 좌표 대조·S2 전용 약국을 건너뛴다(가드가 잡는다)
    _TO_WGS = None


# ── 가드 ──────────────────────────────────────────────────────────────────────
def guards(nmc, nmc_total, recs, verdict, holidays, prev, today, hira=None, hira_total=None):
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

    matched = verdict['s2_matched']
    add('s1_s2_match', matched / max(n, 1) >= 0.95, f'{matched / max(n, 1):.1%}', 'S1↔S2 짝 비율 ≥ 95%')
    if hira is not None:
        hn = len(hira)
        add('s3_complete', hn == hira_total and hn > 0, f'{hn}/{hira_total}', 'S3 받은 수 == totalCount')
        prev_h = prev.get('hira_total') if prev else None
        if prev_h:
            ch = abs(hn - prev_h) / prev_h
            add('s3_total_change', ch <= 0.03, f'{hn} vs {prev_h} ({ch:.1%})', 'S3 직전 대비 ±3%')
        else:
            add('s3_total_min', hn >= 20000, hn, 'S3 첫 발행 ≥ 20,000')
        m3 = verdict['s3_matched']
        add('s1_s3_match', m3 / max(n, 1) >= 0.95, f'{m3 / max(n, 1):.1%}', 'S1↔S3 짝 비율 ≥ 95% (9/11 98.1%)')
        gf = verdict['geo_fixed']
        add('geo_fixed_ratio', gf / max(n, 1) <= 0.03, gf, 'S3 로 좌표 교체 ≤ 3% (9/11 약 1%)')
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


def publish_interval_days(today, holidays):
    """발행 간격(일). 오늘부터 [HOLIDAY_LOOKAHEAD_DAYS] 안에 공휴일(확정·불확실 모두)이 있으면 1, 아니면 주간.
    holidays = 지난 발행의 holidays.json 목록([{date:'YYYYMMDD', status, name}]). 못 읽으면 주간.
    ⚠️발행 파일의 날짜는 **하이픈 없는 YYYYMMDD** 다 — 처음에 'YYYY-MM-DD' 로 짐작해 짰더니 실제 파일에서 추석을
      하나도 못 찾았다(테스트도 짐작한 형식으로 써서 초록불이었다). 둘 다 받는다."""
    end = today + timedelta(days=HOLIDAY_LOOKAHEAD_DAYS)
    for h in holidays or []:
        try:
            d = datetime.strptime(str(h['date']).replace('-', ''), '%Y%m%d').date()
        except (KeyError, TypeError, ValueError):
            continue
        if today <= d <= end:
            return 1
    return PUBLISH_INTERVAL_DAYS


def load_prev_holidays():
    p = os.path.join(OUT_DIR, 'holidays.json')
    try:
        return json.load(open(p, encoding='utf-8')).get('holidays', [])
    except (OSError, ValueError):
        return []


def load_prev():
    p = os.path.join(OUT_DIR, 'index.json')
    if not os.path.exists(p):
        return None
    try:
        idx = json.load(open(p, encoding='utf-8'))
        return {'generated_at': idx.get('generated_at'), 'nmc_total': idx.get('basis', {}).get('nmc_total'),
                'hira_total': idx.get('basis', {}).get('hira_total'),
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
        'schema': SCHEMA, 'app': APP, 'source': 'nmc+mois+hira', 'generated_at': now,
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
        interval = publish_interval_days(today, load_prev_holidays())
        # 20시간: 매일 04시 실행이 몇 분 늦게 돌아도 '1일' 간격에 걸려 하루를 통째로 거르지 않게
        if now_dt - last < (timedelta(hours=20) if interval == 1 else timedelta(days=interval)):
            print(f'[약국] 직전 발행 {prev["generated_at"][:10]} — 발행 간격({interval}일) 전이라 건너뜀')
            return 0
        if interval == 1:
            print(f'[약국] 공휴일 {HOLIDAY_LOOKAHEAD_DAYS}일 이내 — 매일 발행')

    val = {'generated_at': now, 'published': False, 'reasons': [], 'guards': [], 'verdicts': {}}
    try:
        key = load_key()
        t0 = time.time()
        nmc, nmc_total = fetch_nmc(key)
        print(f'[약국] S1 {len(nmc)}/{nmc_total} ({time.time() - t0:.0f}s)')
        mois = fetch_mois()
        print(f'[약국] S2 {len(mois)}행')
        # S3 는 못 받으면 발행을 건너뛴다 — 없이 내면 폐업 판정 규칙이 두 소스로 돌아가
        # 주마다 약국이 사라졌다 나타났다 한다(판정이 흔들리는 것보다 한 주 늦은 게 낫다)
        hira, hira_total = fetch_hira(key)
        print(f'[약국] S3 {len(hira)}/{hira_total}')
        google = fetch_holidays(today)
        try:
            kasi = fetch_kasi(key, (today.year, today.year + 1))
        except Exception as e:  # 공휴일 공식 소스 장애는 치명적이지 않다 — 구글 이름 규칙으로 물러난다
            print('[약국] S4 실패, S5 로 물러남:', e)
            kasi = None
        holidays = merge_holidays(kasi, google)
        val['holidays'] = {'kasi': None if kasi is None else len(kasi), 'google': len(google),
                           'unsure': [h['date'] + ' ' + h['name'] for h in holidays if h['status'] != 'holiday']}
        print(f'[약국] 공휴일 {len(holidays)}일 (확정 {sum(h["status"] == "holiday" for h in holidays)}, '
              f'천문연 {val["holidays"]["kasi"]})')

        recs, verdict, dropped, unknown_region = build(nmc, mois, today, hira=hira)
        val['verdicts'] = dict(verdict)
        val['dropped_closed'] = len(dropped)
        val['unknown_region'] = unknown_region
        g = guards(nmc, nmc_total, recs, verdict, holidays, prev, today, hira=hira, hira_total=hira_total)
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
            basis = {'nmc_total': len(nmc), 'hira_total': len(hira), 'nmc_fetched_at': now,
                     'mois_updated_at': mois_upd,
                     'holidays_source': 'kasi+google_ics' if kasi is not None else 'google_ics'}
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
