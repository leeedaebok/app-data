# -*- coding: utf-8 -*-
"""동네복지 — 시군구별 복지 제도 JSON 생성.

다른 앱의 collect.py 와 달리 **여기서 OpenAPI 를 직접 치지 않는다.**
원본 수집과 LLM 구조화는 무거워서(9,909건 × sonnet) 별도 파이프라인에 있다:

    autoblog_local/welfare_local/extract_worker.py   ← 월 1회 수동 실행
    autoblog_local/welfare_local/data/out_*.jsonl    ← 그 산출물

이 스크립트는 그 산출물을 읽어 **배포용 지역 파일로 굽는 일만** 한다.
그래서 refresh.bat 이 매일 돌아도 결과가 같으면 git diff 가 없어 푸시되지 않는다.

⚠️ out_*.jsonl 이 없으면 **실패로 종료한다**(exit 1). 빈 데이터를 밀어내면
   앱이 죽지 않은 채 빈 화면이 되는데, 그건 크래시 리포트로 안 잡힌다.
"""
import collections
import glob
import json
import os
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(HERE)), 'data', 'dongnebokji')
SRC_DIR = os.getenv(
    'WELFARE_SRC',
    r'D:\claude_workspace\autoblog_local\welfare_local\data',
)
KST = timezone(timedelta(hours=9))
SCHEMA = 1

SIDO = ['서울특별시', '부산광역시', '대구광역시', '인천광역시', '대전광역시', '울산광역시',
        '세종특별자치시', '경기도', '강원특별자치도', '충청북도', '충청남도', '전북특별자치도',
        '전남광주통합특별시', '경상북도', '경상남도', '제주특별자치도']

ORD = {'현금': 0, '감면': 1, '물품·바우처': 2, '서비스·돌봄': 3, '교육·행사': 5}
KEEP = ('id', 'name', 'one_line', 'amount', 'benefit_type', 'target_groups', 'income_req',
        'age_min', 'age_max', 'extra_conditions', 'residency_months', 'deadline',
        'url', 'tel', 'recv', 'org', 'field',
        'detail_url', 'purpose', 'body', 'who', 'criteria', 'docs', 'how', 'law', 'pay_type')


def die(msg):
    print(f'[동네복지] 실패: {msg}', file=sys.stderr)
    sys.exit(1)


def load_rows():
    files = sorted(glob.glob(os.path.join(SRC_DIR, 'out_*.jsonl')))
    if not files:
        die(f'추출본이 없습니다: {SRC_DIR}\\out_*.jsonl '
            f'(welfare_local/extract_worker.py 를 먼저 돌릴 것)')
    rows, seen = [], set()
    for f in files:
        with open(f, encoding='utf-8') as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get('id') in seen:
                    continue
                seen.add(r['id'])
                rows.append(r)
    if len(rows) < 5000:
        die(f'추출본이 너무 적습니다({len(rows)}건). 잘린 파일일 수 있습니다.')
    return rows


def sido_of(org):
    return next((k for k in SIDO if (org or '').startswith(k)), None)


def _clean(v, limit):
    t = str(v or '').replace('\r\n', '\n').replace('\r', '\n').strip()
    return t[:limit] if t and t != 'None' else None


def load_source_text():
    """원문(정부24 상세) 캐시. 요약만 내보내면 '제목만 알려주는 앱'이 된다.

    상한은 '화면에서 읽을 분량'으로 잡는다 — 전문을 다 담으면 배포 파일이 4배가 되고
    그만큼을 읽는 사람도 없다. 넘치는 분량은 detail_url(정부24)로 넘긴다.
    """
    lst = os.path.join(SRC_DIR, 'serviceList.json')
    det = os.path.join(SRC_DIR, 'serviceDetail.json')
    if not (os.path.exists(lst) and os.path.exists(det)):
        die(f'원문 캐시가 없습니다: {SRC_DIR}\\service*.json')
    L = {s['서비스ID']: s for s in json.load(open(lst, encoding='utf-8'))}
    D = {d['서비스ID']: d for d in json.load(open(det, encoding='utf-8'))}
    return L, D


def load_tag_fix():
    """'전체주민' 오태깅 교정본(welfare_local/retag_everyone.py 산출).

    직업·자격 한정 제도가 '전체주민'으로 태깅돼 앱의 '누구나' 목록에 떴다.
    원본 out_*.jsonl 은 건드리지 않고 여기서 덧씌운다 — 되돌리기 쉽게.
    ⚠️키워드로 거르지 않는다. '의료원'·'기관' 같은 어휘를 쓰면
      "삼척의료원 방문재활 서비스"(주민 대상이 맞다)까지 걸린다.
    """
    p = os.path.join(SRC_DIR, 'tag_fix.json')
    if not os.path.exists(p):
        print('[동네복지] 경고: tag_fix.json 없음 — 원본 태그를 그대로 쓴다', file=sys.stderr)
        return {}
    return json.load(open(p, encoding='utf-8'))


def main():
    rows = load_rows()
    LIST, DET = load_source_text()
    TAGFIX = load_tag_fix()
    fixed = 0
    for r in rows:
        f = TAGFIX.get(r.get('id'))
        if f is not None and isinstance(f.get('target_groups'), list):
            if set(f['target_groups']) != set(r.get('target_groups') or []):
                fixed += 1
            r['target_groups'] = f['target_groups']
    print(f'[동네복지] 태그 교정 적용 {fixed}건 / 교정본 {len(TAGFIX)}건')
    orgmap_path = os.path.join(SRC_DIR, 'org_map.json')
    orgmap = {}
    if os.path.exists(orgmap_path):
        orgmap = {k: tuple(v[:2]) for k, v in
                  json.load(open(orgmap_path, encoding='utf-8')).items()}

    by_sgg, by_sido, skipped = collections.defaultdict(list), collections.defaultdict(list), 0
    for r in rows:
        if not r.get('personal_benefit'):
            skipped += 1
            continue
        org = (r.get('org') or '').strip()
        sd = sido_of(org)
        if sd:
            if r.get('orgtype') == '시군구' and org != sd:
                by_sgg[org].append(r)
            else:
                by_sido[sd].append(r)
            continue
        hit = orgmap.get(org)
        if not hit:
            skipped += 1
            continue
        sd2, sgg2 = hit
        (by_sgg[f'{sd2} {sgg2}'] if sgg2 else by_sido[sd2]).append(r)

    if len(by_sgg) < 200:
        die(f'시군구가 {len(by_sgg)}개뿐입니다(정상 227). 지역 매칭이 깨졌을 수 있습니다.')

    os.makedirs(OUT_DIR, exist_ok=True)
    for old in glob.glob(os.path.join(OUT_DIR, 'r*.json')):
        os.remove(old)

    now = datetime.now(KST).isoformat(timespec='seconds')

    def slim(r):
        d = {k: r[k] for k in KEEP if r.get(k) not in (None, '', [], 'None')}
        src, dt = LIST.get(r.get('id'), {}), DET.get(r.get('id'), {})
        for key, field, limit in (
            ('detail_url', src.get('상세조회URL'), 200),
            ('purpose', dt.get('서비스목적') or src.get('서비스목적요약'), 300),
            ('body', dt.get('지원내용') or src.get('지원내용'), 800),
            ('who', dt.get('지원대상') or src.get('지원대상'), 600),
            ('criteria', dt.get('선정기준') or src.get('선정기준'), 800),
            ('docs', dt.get('구비서류'), 600),
            ('how', dt.get('신청방법') or src.get('신청방법'), 400),
            ('law', dt.get('법령') or dt.get('자치법규') or dt.get('행정규칙'), 200),
            ('pay_type', src.get('지원유형'), 60),
        ):
            val = _clean(field, limit)
            if val:
                d[key] = val
        return d

    def sort_key(r):
        return (ORD.get(r.get('benefit_type'), 4), 0 if r.get('amount') else 1, r.get('name') or '')

    index = []
    for i, (org, items) in enumerate(sorted(by_sgg.items())):
        sd = sido_of(org)
        local = [slim(x) for x in sorted(items, key=sort_key)]
        metro = [slim(x) for x in sorted(by_sido.get(sd, []), key=sort_key)]
        fn = f'r{i:04d}.json'
        payload = {
            'schema': SCHEMA, 'app': 'dongnebokji', 'generated_at': now,
            'region': org, 'sido': sd,
            'count': len(local) + len(metro),
            'local': local, 'metro': metro,
        }
        with open(os.path.join(OUT_DIR, fn), 'w', encoding='utf-8') as fh:
            json.dump(payload, fh, ensure_ascii=False)
        index.append({'name': org, 'sido': sd, 'file': fn,
                      'n_local': len(local), 'n_metro': len(metro)})

    with open(os.path.join(OUT_DIR, 'index.json'), 'w', encoding='utf-8') as fh:
        json.dump({
            'schema': SCHEMA, 'app': 'dongnebokji', 'source': 'gov24+bokjiro',
            'generated_at': now, 'count': len(index), 'regions': index,
        }, fh, ensure_ascii=False)

    total = sum(x['n_local'] + x['n_metro'] for x in index)
    print(f'[동네복지] 지역 {len(index)}개 / 제도 {total}건 / 제외 {skipped}건 → {OUT_DIR}')


if __name__ == '__main__':
    main()
