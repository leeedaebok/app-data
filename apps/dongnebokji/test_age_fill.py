# -*- coding: utf-8 -*-
"""age_fill 단위테스트 — `python -m pytest apps/dongnebokji/test_age_fill.py -q`

실제 보조금24 원문에서 가져온 문장으로 짰다. **채우는 경우보다 비워 두는 경우가 많다** —
잘못 채우면 받을 수 있는 제도가 앱에서 사라지기 때문이다.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from age_fill import fill_age, ja_age_ok, parse_text_age, self_applies  # noqa: E402


def row(name, groups, **kw):
    return {'name': name, 'target_groups': groups, **kw}


# ── 본문 파서: 명확한 한 범위만 ─────────────────────────────────────────────

def test_range_man_isang_iha():
    assert parse_text_age('청년: 만 19세 이상 39세 이하 구민') == (19, 39)


def test_range_tilde_and_fullwidth():
    assert parse_text_age('대상 청소년(9~24세)') == (9, 24)
    assert parse_text_age('대상 청소년(9∼24세)') == (9, 24)
    assert parse_text_age('청년(19-34세)') == (19, 34)


def test_range_with_space_after_man():
    assert parse_text_age('- 청년\n  ㆍ만19세 ~ 39세 이하') == (19, 39)


def test_upper_only_and_miman():
    assert parse_text_age('청년미취업자(만 34세 이하)') == (None, 34)
    assert parse_text_age('만 40세 미만의 청년') is None  # 앞에 대상어가 없다
    assert parse_text_age('대상: 만 40세 미만의 청년') == (None, 39)


def test_same_range_repeated_is_one():
    # 보조금24 는 지원대상 문단을 두 번 싣는 일이 흔하다.
    t = '대상 청년(19~39세) / 대상 청년(19~39세)'
    assert parse_text_age(t) == (19, 39)


def test_multiple_ranges_give_up():
    # HPV 접종: 12-17세 여성 청소년 또는 18-26세 저소득 여성 → OR 갈래
    assert parse_text_age('HPV 대상: 12~17세 여성 청소년, 18~26세 저소득층 여성') is None


def test_stray_age_gives_up():
    # 묶이지 않은 "12세 남성 청소년"이 남으면 목록형이다.
    assert parse_text_age('대상 청소년 12~17세 여성, 12세 남성 청소년') is None


def test_other_person_age_gives_up():
    # 나이가 신청자가 아니라 막내 자녀의 것
    assert parse_text_age('다자녀가구: 막내 자녀가 19세 이하인 가구') is None
    assert parse_text_age('최연소 자녀가 18세 이하인 2자녀 이상 가정') is None


def test_exception_list_is_not_target_age():
    # 인천 청년월세: 원가구 소득 미고려 사유 목록의 "30세 이상"
    assert parse_text_age('원가구 소득·재산 미고려 : ①30세 이상 ②혼인') is None


def test_generation_and_dates_are_not_age():
    assert parse_text_age('장학생 신청은 1세대 1명') is None
    assert parse_text_age('2008. 1. 1. ~ 2014. 12. 31. 출생자') is None


# ── JA 코드 게이트 ────────────────────────────────────────────────────────

def test_ja_default_or_missing_is_rejected():
    assert not ja_age_ok(0, 120, ['청년'], '중랑구 청년')
    assert not ja_age_ok(None, 39, ['청년'], '중랑구 청년')


def test_ja_needs_pure_age_groups():
    # 수급자·노인이 섞인 체육시설 감면 → OR 갈래
    assert not ja_age_ok(13, 120, ['저소득', '청소년', '노인'], '학생, 노인(65세 이상)')


def test_ja_rejects_when_group_contradicts():
    # 예방접종 13-64 에 영유아가 태깅돼 있다
    assert not ja_age_ok(13, 64, ['영유아', '청소년'], '어린이 국가예방접종')


def test_ja_rejects_when_school_level_not_covered():
    # 인천대교 장학금 18-40 인데 본문에 고등학생
    assert not ja_age_ok(18, 40, ['청소년', '청년'], '관내 고등학생, 대학교 재학생')


def test_ja_rejects_school_out_youth_narrower_than_law():
    # 학교 밖 청소년은 법상 9-24세. 13-18 은 좁다.
    assert not ja_age_ok(13, 18, ['청소년'], '도내 학교 밖 청소년지원센터에 등록된 청소년')
    assert ja_age_ok(9, 24, ['청소년'], '학교 밖 청소년 도움센터에 등록한 청소년')


def test_ja_rejects_leaving_care_too_narrow():
    # 자립준비청년 17-18 → 보호종료 5년 이내(23세)가 빠진다
    assert not ja_age_ok(17, 18, ['청년'], '서초구에서 거주중인 자립준비청년(보호종료아동)')


def test_ja_rejects_enlistment_without_18():
    # 만 18세 지원 입영이 있다
    assert not ja_age_ok(19, 37, ['청년'], '입영(소집)하는 현역병 및 사회복무요원')
    assert ja_age_ok(18, 32, ['청년'], '입영(소집)하는 현역병, 보충역')


def test_ja_rejects_everyone_or_no_youth_word():
    assert not ja_age_ok(18, 64, ['청년'], '전국민대상')
    assert not ja_age_ok(19, 120, ['청년'], '인천 지역 주민')
    assert not ja_age_ok(19, 34, ['청년'], '면접 참여 구직자')
    assert not ja_age_ok(18, 65, ['청년'], '대학생 원거리 통학자 - 연령 기준 : 없음')


def test_ja_rejects_text_age_outside_code():
    assert not ja_age_ok(18, 39, ['청년'], '청년(만 45세 이하)')


def test_ja_accepts_clean_youth_case():
    assert ja_age_ok(19, 39, ['청년'], '○ 중랑구 청년')


# ── 신청자 = 앱 사용자 본인인가 ────────────────────────────────────────────

def test_self_applies_adult_only():
    assert self_applies(19, 39, {'청년'})
    assert not self_applies(13, 19, {'청소년'})      # 교복비 — 부모가 찾는다
    assert not self_applies(9, 24, {'청소년'})
    assert self_applies(None, 34, {'청년'})
    assert not self_applies(None, 18, {'청소년'})


# ── fill_age 통합 ─────────────────────────────────────────────────────────

def test_fill_never_overwrites_existing():
    r = row('청년월세', ['청년'], age_min=19, age_max=34)
    assert fill_age(r, '대상 청년(19~39세)', (19, 39)) is None
    r = row('청년월세', ['청년'], age_max=34)
    assert fill_age(r, '대상 청년(19~39세)', (19, 39)) is None


def test_fill_jungnang_youth_hall_from_ja():
    r = row('중랑청년청 운영', ['청년'])
    assert fill_age(r, '○ 중랑구 청년\n○ 중랑구 청년', (19, 39)) == (19, 39, 'ja')


def test_fill_school_out_youth_stays_empty():
    # 원격 결함 예시. 코드는 9-24 로 맞지만 신청자가 부모일 수 있어 앱 필터에 걸면 부모가 빠진다.
    r = row('학교밖청소년 교육참여활동비 지급', ['청소년'])
    text = '○ 서울특별시교육청학교밖청소년도움센터에 등록한 청소년 중 지급요건 충족자'
    assert fill_age(r, text, (9, 24)) is None


def test_fill_text_wins_when_clear():
    r = row('청년 면접수당', ['청년'])
    assert fill_age(r, '대상: 만 19세 이상 39세 이하 미취업 청년', (0, 120)) == (19, 39, 'text')


def test_fill_rejects_mixed_non_age_audience_in_text():
    # 익산 신혼부부·청년 전세: 태그는 청년뿐이지만 신혼부부(나이 무관) 갈래가 있다
    r = row('익산시 신혼부부 청년 주택 전세보증금 대출이자 지원사업', ['청년'])
    text = '신혼부부 및 청년으로서 - 청년 ㆍ만19세 ~ 39세 이하 - 신혼부부 ㆍ혼인 7년 이내'
    assert fill_age(r, text, (19, 39)) is None


def test_fill_rejects_name_with_other_audience():
    r = row('신혼부부 결혼 축하 바우처 지원', ['청년'])
    assert fill_age(r, '달성군에 주민등록을 두고 혼인신고 한 가정', (19, 50)) is None


def test_fill_only_youth_tags():
    # 위탁아동 0-17 은 위탁부모가 신청 → 채우지 않는다
    assert fill_age(row('가정위탁아동 양육보조금', ['아동']), '○ 가정위탁아동', (0, 17)) is None
    # 퇴직 교직원 19-64 → 66세 퇴직자가 빠진다
    assert fill_age(row('사립학교 교직원 퇴직수당', ['중장년']), '퇴직 교직원', (19, 64)) is None


def test_fill_nothing_when_no_signal():
    r = row('달성 장학금(수능우수)', ['청년'])
    assert fill_age(r, '대학교 신입생 중 수능성적 우수자', None) is None
