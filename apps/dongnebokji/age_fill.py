# -*- coding: utf-8 -*-
"""비어 있는 age_min/age_max 를 **보수적으로** 채운다(2026-09-15).

왜 비었나(원격 전수 9/15, 청소년·청년 태그 1,468건 중 926건 무연령):
  - `welfare_local/extract_worker.py` 는 LLM 에 **본문 텍스트만** 줬다. 보조금24
    `supportConditions` 의 연령 코드(JA0110 하한 / JA0111 상한)는 한 번도 안 넘겼다.
  - 본문에 "N세"가 있는 건 926건 중 **47건(5%)** 뿐이고, 그마저 대부분이
    "청소년(13-18세) 또는 노인(65세 이상)" 같은 **OR 갈래**라 한 범위로 쓰면 틀린다.
    나머지는 "중·고등학생", "대학생", "학교 밖 청소년"처럼 **신분으로만** 적혀 있다.
  - JA 코드는 926건 중 455건이 0-120 이 아닌 값을 갖는다. 그런데 **그대로 믿으면
    틀린다** — 표본 30건 중 7건이 오류였다(예방접종 13-64 → 3세·70세가 빠진다,
    체육시설 감면 13-120 → 어린이 7-12세가 빠진다, 자립준비청년 17-18 → 보호종료
    5년 이내인 23세가 빠진다, 한부모 명절격려금 7-19 → 부모가 빠진다).

원칙: 앱은 "확실히 못 받는 것만" 뺀다. 잘못 채우면 받을 수 있는 제도가 사라지므로
**애매하면 None 을 돌려준다.** 기존 값(LLM 이 채운 것)은 절대 덮지 않는다.
"""
import re

AGE_GROUPS = {'영유아', '아동', '청소년', '청년', '중장년', '노인'}
# 채우는 대상은 **청소년·청년 태그만**(이번 결함의 범위이자 전수 검토한 범위).
#   아동·영유아는 JA 가 위탁아동 0-17 처럼 '아동 나이'인데 신청자가 위탁부모라 앱에서
#   부모가 빠지고, 중장년·노인은 "퇴직 교직원 퇴직수당 19-64" 처럼 66세 퇴직자가 빠진다
#   (2026-09-15 dry-run 에서 확인). 넓히려면 먼저 그 태그로 전수 검토할 것.
FILL_GROUPS = {'청소년', '청년'}

# 태그된 대상군이 요구하는 최소 조건(만 나이). 이 범위와 어긋나는 코드는 OR 갈래나
# 오입력이라 보고 버린다.
_GROUP_NEEDS = {
    '영유아': lambda lo, hi: lo <= 5,
    '아동':   lambda lo, hi: lo <= 12 and hi >= 6,
    '청소년': lambda lo, hi: lo <= 18 and hi >= 13,
    # 청년은 조례마다 19-34 / 19-39 / 18-45 로 갈린다. 상한이 24 아래면 "고교생까지"를
    # 청년으로 태깅한 것이라(예: 장흥군 장학금 6-19 에 대학생 포함) 코드를 믿지 않는다.
    '청년':   lambda lo, hi: lo <= 29 and hi >= 24,
    '중장년': lambda lo, hi: lo <= 64 and hi >= 40,
    '노인':   lambda lo, hi: hi >= 65,
}

# 본문에 이 신분이 보이면 JA 범위가 그 신분의 만 나이를 덮어야 한다.
# (학년 ↔ 만 나이는 생일에 따라 한 살 흔들린다. 앱이 출생연도로 ±1 을 이미 두므로
#  여기서는 "그 학년의 막내·맏이가 확실히 걸리는" 선만 요구한다.)
_TEXT_NEEDS = [
    (re.compile(r'유치원|유아|영아'),                       lambda lo, hi: lo <= 5),
    (re.compile(r'초등|초\s*[·,ㆍ]\s*중|초중고|어린이'),      lambda lo, hi: lo <= 7 and hi >= 11),
    (re.compile(r'중학|중\s*[·,ㆍ]\s*고|중고등|중1|중3'),     lambda lo, hi: lo <= 13 and hi >= 14),
    (re.compile(r'고등학생|고등학교|고교|고\s*[·,ㆍ]\s*대|고1|고3'), lambda lo, hi: lo <= 16 and hi >= 17),
    (re.compile(r'대학|전문대'),                             lambda lo, hi: hi >= 23),
    # 학교 밖 청소년·위기청소년은 법 정의가 9-24세(청소년기본법)라 코드가 13-18 이면 좁다.
    (re.compile(r'학교\s*밖|위기\s*청소년|청소년\s*복지|청소년\s*상담|쉼터'),
                                                             lambda lo, hi: lo <= 13 and hi >= 24),
    (re.compile(r'자립\s*준비|보호\s*종료'),                  lambda lo, hi: hi >= 23),
    # 병역은 만 18세 지원 입영이 있고 연기로 30대 초반까지 간다.
    (re.compile(r'입영|군\s*복무|현역|사회복무|군\s*장병'),    lambda lo, hi: lo <= 18 and hi >= 30),
]
# 이 표현이 있으면 나이가 "경과 기간"으로 정해져 코드가 근사치일 가능성이 크다 → JA 안 씀.
_TEXT_REJECT = re.compile(
    r'졸업\s*후|졸업생|졸업자|졸업한|이내의?\s*(자|청년|아동)|만학|'
    r'전\s*국민|누구나|(?m:주민\s*$)|연령\s*(기준|제한)?\s*[:：]?\s*(없음|무관|제한\s*없)')
# JA 를 쓰려면 본문(또는 제도명)에 **나이로 정해지는 대상**이 적혀 있어야 한다.
# "인천 지역 주민"·"면접 참여 구직자"에 붙은 19-34 는 태그만 청년이지 대상은 누구나다.
_YOUTH_WORD = re.compile(r'청년|청소년|아동|어린이|유아|영아|학생|학교|대학|입학|학년|입영|군\s*복무|군\s*장병|현역|사회복무|자립\s*준비|보호\s*종료')


def _norm(t):
    t = str(t or '')
    for a in '∼～〜':
        t = t.replace(a, '~')
    for a in '－–—':
        t = t.replace(a, '-')
    return t


# "N세" — 1-2자리 숫자. 앞이 숫자·점이면 날짜 조각이다. 뒤가 '대'면 "1세대".
_N = r'(?<![\d.])(\d{1,2})'
_SE = r'\s*세(?!대)'
_RANGE = re.compile(
    r'(?:만\s*)?' + _N + r'(?:\s*세(?!대))?\s*(?:이상)?\s*(?:~|-|부터)\s*(?:만\s*)?'
    + _N + _SE + r'\s*(이하|미만|까지)?')
_ONE = re.compile(r'(?:만\s*)?' + _N + _SE + r'\s*(이하|미만|이상|초과)')
_ANY = re.compile(_N + _SE)

# 나이 표현 바로 앞에 이게 있으면 **신청자가 아닌 사람**(자녀·막내)의 나이다.
_OTHER_PERSON = re.compile(r'자녀|막내|최연소|손자|아이가|아동이 있는|부양')
# 나이 표현 앞 15자 안에 대상어가 있어야 "대상 나이"로 본다. "①30세 이상 ②혼인"
# (청년월세의 부모소득 미고려 사유) 같은 **예외 목록**을 대상 나이로 읽지 않기 위해서다.
_SUBJECT = re.compile(r'청년|청소년|아동|어린이|학생|대상|연령|나이|자\s*[:：]|인\s*[:：]')
# 본문에 나이 무관 대상이 **함께** 적혀 있으면 OR 갈래다 — "신혼부부 및 청년(만19-39세)"에
# 19-39 를 걸면 45세 신혼부부가 빠진다. 태그가 청년 하나여도 본문이 이러면 채우지 않는다.
_OTHER_GROUP = re.compile(
    r'신혼|부부|한부모|조손|장애|수급|차상위|노인|어르신|경로|임신|임산|출산|다자녀|보훈|유공|'
    r'혼인|결혼|학부모|농업인|어업인|외국인|다문화|이탈주민|여성|근로자|노동자|재직자|소상공|자영업')


def parse_text_age(text):
    """본문에서 **하나뿐인** 대상 나이 범위를 뽑는다. 애매하면 None.

    반환: (age_min, age_max) — 한쪽은 None 일 수 있다. 둘 다 없으면 None.
    """
    t = _norm(text)
    if not t:
        return None
    found, used = [], []
    for m in _RANGE.finditer(t):
        lo, hi, tail = int(m.group(1)), int(m.group(2)), m.group(3)
        if tail == '미만':
            hi -= 1
        if lo > hi:
            return None
        found.append((m.start(), lo, hi))
        used.append((m.start(), m.end()))

    def _free(s, e):
        return all(e <= a or s >= b for a, b in used)

    for m in _ONE.finditer(t):
        if not _free(m.start(), m.end()):
            continue
        n, kind = int(m.group(1)), m.group(2)
        lo = hi = None
        if kind == '이하':
            hi = n
        elif kind == '미만':
            hi = n - 1
        elif kind == '이상':
            lo = n
        else:  # 초과
            lo = n + 1
        found.append((m.start(), lo, hi))
        used.append((m.start(), m.end()))

    # 어느 표현에도 안 묶인 "N세"가 남아 있으면(예: "12세 남성 청소년") 목록형이다 → 포기.
    for m in _ANY.finditer(t):
        if _free(m.start(), m.end()):
            return None
    if not found:
        return None

    for pos, _lo, _hi in found:
        before = t[max(0, pos - 15):pos]
        if _OTHER_PERSON.search(before) or not _SUBJECT.search(before):
            return None

    ranges = {(lo, hi) for _, lo, hi in found if lo is not None and hi is not None}
    los = {lo for _, lo, hi in found if lo is not None and hi is None}
    his = {hi for _, lo, hi in found if hi is not None and lo is None}
    if ranges:
        if len(ranges) != 1 or los or his:
            return None
        return next(iter(ranges))
    if len(los) > 1 or len(his) > 1:
        return None
    lo = next(iter(los), None)
    hi = next(iter(his), None)
    if lo is not None and hi is not None and lo > hi:
        return None
    return (lo, hi)


def _text_ages(text):
    return [int(m.group(1)) for m in _ANY.finditer(_norm(text))]


def ja_age_ok(lo, hi, groups, text):
    """보조금24 JA0110/JA0111 을 이 제도에 써도 되는가."""
    if lo is None or hi is None:
        return False
    try:
        lo, hi = int(lo), int(hi)
    except (TypeError, ValueError):
        return False
    if (lo, hi) == (0, 120) or lo > hi or lo < 0 or hi > 120:
        return False
    g = set(groups or [])
    # 나이 대상군만 붙은 제도만. 한부모·저소득·장애인 등이 섞이면 신청자가 부모·가구이거나
    # OR 갈래("수급자, 청소년, 노인")라 한 범위로 못 줄인다.
    if not g or not g <= AGE_GROUPS:
        return False
    if any(not _GROUP_NEEDS[x](lo, hi) for x in g):
        return False
    t = _norm(text)
    if _TEXT_REJECT.search(t) or not _YOUTH_WORD.search(t):
        return False
    if any(pat.search(t) and not need(lo, hi) for pat, need in _TEXT_NEEDS):
        return False
    # 본문에 적힌 나이가 코드 범위 밖이면(한 살 여유) 둘 중 하나가 틀렸다.
    if any(n < lo - 1 or n > hi + 1 for n in _text_ages(t)):
        return False
    return True


def fill_age(row, text, ja=None):
    """row 에 나이가 없을 때만 채울 값을 돌려준다: (age_min, age_max, source) 또는 None.

    text: 원문 지원대상·선정기준(제도명은 row['name'] 에서 알아서 붙인다).
    ja:   (JA0110, JA0111) 또는 None.
    source: 'text' | 'ja'. 기존 값이 하나라도 있으면 None(덮지 않는다).
    """
    text = f"{row.get('name') or ''}\n{text or ''}"
    if row.get('age_min') is not None or row.get('age_max') is not None:
        return None
    groups = row.get('target_groups') or []
    g = set(groups)
    if not g or not g <= FILL_GROUPS:
        return None
    if _OTHER_GROUP.search(_norm(text)):
        return None
    parsed = parse_text_age(text)
    if parsed:
        lo, hi = parsed
        # 태그와 모순되면 버린다(한쪽 경계만 있으면 없는 쪽을 넓게 본다).
        clo = 0 if lo is None else lo
        chi = 120 if hi is None else hi
        if all(_GROUP_NEEDS[x](clo, chi) for x in g) and self_applies(lo, hi, g):
            return (lo, hi, 'text')
        return None
    if ja and ja_age_ok(ja[0], ja[1], groups, text) and self_applies(int(ja[0]), int(ja[1]), g):
        return (int(ja[0]), int(ja[1]), 'ja')
    return None


ADULT_MIN = 18


def self_applies(lo, hi, groups):
    """이 나이 범위로 거르면 **앱 사용자 본인**을 거르는 것인가.

    🚨앱은 온보딩의 "태어난 해"(= 사용자 **본인** 나이)로 거른다(`Profile.kt`).
      그런데 초·중·고 입학준비금·교복비·학교 밖 청소년 제도는 **부모가 찾아서 신청**한다.
      여기에 6-19 를 채우면 45세 부모가 '청소년' 칩을 골라도 그 제도가 사라진다.
      → 하한이 성인(18세 이상)이라 **본인이 신청하는** 범위만 채운다.
      예외: 태그가 '청년' 하나이고 상한만 있는 "만 39세 이하" 는 청년 본인 제도라 채운다.
    """
    if lo is not None and lo >= ADULT_MIN:
        return True
    return lo is None and hi is not None and hi >= 24 and set(groups) == {'청년'}
