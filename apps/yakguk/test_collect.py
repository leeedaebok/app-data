# -*- coding: utf-8 -*-
"""collect.py 의 해석 함수 단위 테스트. 실행: python -m unittest apps/yakguk/test_collect.py

입력값은 2026-09-11 실데이터에서 뽑은 문구들이다(dutyEtc·dutyInf·주소).
"""
import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collect as C  # noqa: E402


class RegionTest(unittest.TestCase):
    def test_full_and_short(self):
        self.assertEqual(C.region_of('서울특별시 마포구 백범로 199'), ('서울특별시', '마포구'))
        self.assertEqual(C.region_of('서울 은평구 통일로 1'), ('서울특별시', '은평구'))

    def test_city_with_gu(self):
        self.assertEqual(C.region_of('경기 수원시 영통구 봉영로 1'), ('경기도', '수원시 영통구'))
        self.assertEqual(C.region_of('경기도 군포시 산본로 1'), ('경기도', '군포시'))

    def test_gyeonggi_gwangju_is_not_gwangju_metro(self):
        # "경기도 광주시"를 광주광역시로 바꾸면 안 된다
        self.assertEqual(C.region_of('경기도 광주시 경안로 1'), ('경기도', '광주시'))

    def test_gwangju_jeonnam_merged(self):
        self.assertEqual(C.region_of('광주광역시 동구 금남로 1'), ('전남광주통합특별시', '광주 동구'))
        self.assertEqual(C.region_of('전라남도 목포시 영산로 1'), ('전남광주통합특별시', '목포시'))
        self.assertEqual(C.region_of('전남광주통합특별시 목포시 청호로 1'), ('전남광주통합특별시', '목포시'))

    def test_merged_name_with_old_gwangju_gu(self):
        # 새 이름 + 옛 광주 구 → '동구'로 두면 옛 표기('광주 동구')와 목록이 둘로 갈린다(9/11 실측)
        self.assertEqual(C.region_of('전남광주통합특별시 동구 금남로 1'), ('전남광주통합특별시', '광주 동구'))
        self.assertEqual(C.region_of('전남광주통합특별시 광산구 하남대로 1'), ('전남광주통합특별시', '광주 광산구'))

    def test_sejong_and_unknown(self):
        self.assertEqual(C.region_of('세종특별자치시 나성북1로 1'), ('세종특별자치시', '세종시'))
        self.assertEqual(C.region_of('어딘가 모를 주소'), (None, None))
        self.assertEqual(C.region_of(''), (None, None))


class MemoTest(unittest.TestCase):
    def test_lunch_variants(self):
        self.assertEqual(C.parse_lunch('평일 휴게시간 13:00-14:00'), '1300-1400')
        self.assertEqual(C.parse_lunch('휴게시간 13:00~14:00'), '1300-1400')
        self.assertEqual(C.parse_lunch('점심시간 13:00~14:00'), '1300-1400')
        self.assertEqual(C.parse_lunch('점심시간 12시30분~13시30분'), '1230-1330')

    def test_lunch_rejects_implausible(self):
        self.assertIsNone(C.parse_lunch('휴게시간 09:00-18:00'))  # 9시간 휴게는 오독
        self.assertIsNone(C.parse_lunch('일요일 및 공휴일 휴무'))
        self.assertIsNone(C.parse_lunch(''))

    def test_call_first(self):
        self.assertTrue(C.call_first('공휴일은 전화확인하세요'))
        self.assertTrue(C.call_first('일요일 2, 4주 운영'))
        self.assertTrue(C.call_first('격주 토요일 휴무'))
        self.assertFalse(C.call_first('연중무휴'))
        self.assertFalse(C.call_first('평일 휴게시간 13:00-14:00'))

    def test_public_night(self):
        self.assertTrue(C.is_public_night('은하약국', '공공심야약국 22:00 ~ 익일 1시까지', ''))
        self.assertTrue(C.is_public_night('', '공공야간약국운영으로 새벽1시까지', ''))
        self.assertFalse(C.is_public_night('심야약국', '', ''))  # 이름에 '심야'만으로는 공공 지정이 아니다


class TimeTest(unittest.TestCase):
    def test_hhmm(self):
        self.assertTrue(C.valid_hhmm('0900'))
        self.assertTrue(C.valid_hhmm('2600'))  # 자정 넘김 표기(163곳)
        self.assertFalse(C.valid_hhmm('3100'))
        self.assertFalse(C.valid_hhmm('0960'))
        self.assertFalse(C.valid_hhmm('900'))
        self.assertFalse(C.valid_hhmm(''))


class KeyTest(unittest.TestCase):
    def test_road_key(self):
        self.assertEqual(C.key_road('경상남도 진주시 범골로 9, 1층 103호 (충무공동)'), '범골로9')
        self.assertEqual(C.key_road('서울특별시 영등포구 당산로31길 12-3'), '당산로31길12-3')

    def test_name_phone(self):
        self.assertEqual(C.key_name('365열린 온누리약국(본점)'), '365열린온누리본점')
        self.assertEqual(C.key_phone('055-752-3006'), '57523006')
        self.assertIsNone(C.key_phone('1234'))


def _nmc(hpid, name, addr, tel='02-111-2222', etc=''):
    d = {'hpid': hpid, 'dutyName': name, 'dutyAddr': addr, 'dutyTel1': tel,
         'wgs84Lat': '37.5', 'wgs84Lon': '127.0', 'dutyEtc': etc}
    for i in range(1, 6):
        d[f'dutyTime{i}s'], d[f'dutyTime{i}c'] = '0900', '1800'
    return d


def _mois(name, addr, st='영업/정상', tel='02-111-2222', closed='', s='', e=''):
    return {'사업장명': name, '도로명주소': addr, '영업상태명': st, '전화번호': tel, '폐업일자': closed,
            '휴업시작일자': s, '휴업종료일자': e, '좌표정보(X)': '', '좌표정보(Y)': '', '관리번호': '1'}


def _hira(name, addr, tel='02-111-2222', estb='20100101'):
    return {'yadmNm': name, 'addr': addr, 'telno': tel, 'estbDd': estb, 'XPos': '127.0', 'YPos': '37.5'}


class HolidayMergeTest(unittest.TestCase):
    google = [
        {'date': '20260501', 'name': '노동절', 'status': 'holiday?'},
        {'date': '20260925', 'name': '추석', 'status': 'holiday'},
        {'date': '20261231', 'name': '가짜 공휴일', 'status': 'holiday?'},
    ]

    def test_kasi_confirms_what_google_was_unsure_of(self):
        # 9/11 실측: 천문연이 2026 노동절·제헌절을 isHoliday=Y 로 준다 — 구글 단독일 땐 '확인 필요'였다
        got = {h['date']: h['status'] for h in C.merge_holidays({'20260501': '노동절', '20260925': '추석'}, self.google)}
        self.assertEqual(got['20260501'], 'holiday')
        self.assertEqual(got['20260925'], 'holiday')
        self.assertEqual(got['20261231'], 'holiday?')  # 구글에만 있는 날은 확정하지 않는다

    def test_kasi_only_day_is_official(self):
        got = {h['date']: h['status'] for h in C.merge_holidays({'20260717': '제헌절'}, [])}
        self.assertEqual(got, {'20260717': 'holiday'})

    def test_kasi_failure_falls_back_to_google(self):
        self.assertEqual(C.merge_holidays(None, self.google), self.google)


class VerdictTest(unittest.TestCase):
    today = date(2026, 9, 11)

    def verdict_of(self, nmc, mois):
        recs, v, dropped, _ = C.build(nmc, mois, self.today)
        return {r['id']: r['v'] for r in recs}, v, dropped

    def test_ok(self):
        got, _, _ = self.verdict_of([_nmc('A', '가나약국', '서울특별시 마포구 백범로 1')],
                                    [_mois('가나약국', '서울특별시 마포구 백범로 1')])
        self.assertEqual(got, {'A': 'ok'})

    def test_suspended_in_window(self):
        got, _, _ = self.verdict_of([_nmc('A', '가나약국', '서울특별시 마포구 백범로 1')],
                                    [_mois('가나약국', '서울특별시 마포구 백범로 1', st='휴업', s='2026-09-01', e='2027-01-31')])
        self.assertEqual(got, {'A': 'suspended'})

    def test_suspension_ended_is_ok(self):
        got, _, _ = self.verdict_of([_nmc('A', '가나약국', '서울특별시 마포구 백범로 1')],
                                    [_mois('가나약국', '서울특별시 마포구 백범로 1', st='휴업', s='2026-01-01', e='2026-03-31')])
        self.assertEqual(got, {'A': 'ok'})

    def test_recent_closure_dropped(self):
        got, v, dropped = self.verdict_of([_nmc('A', '가나약국', '서울특별시 마포구 백범로 1', tel='02-999-0000')],
                                          [_mois('가나약국', '서울특별시 마포구 백범로 1', st='폐업', closed='2026-05-01', tel='02-999-0000')])
        self.assertEqual(got, {})
        self.assertEqual(dropped, ['A'])

    def test_old_closure_flagged_not_dropped(self):
        got, _, _ = self.verdict_of([_nmc('A', '가나약국', '서울특별시 마포구 백범로 1', tel='02-999-0000')],
                                    [_mois('가나약국', '서울특별시 마포구 백범로 1', st='폐업', closed='2017-10-10', tel='02-999-0000')])
        self.assertEqual(got, {'A': 'closed?'})

    def test_closed_but_same_phone_open_elsewhere_is_ok(self):
        # 같은 전화로 영업 중인 인허가가 따로 있으면 이름·주소만 바뀐 것 (9/11 실측 53곳)
        got, _, _ = self.verdict_of(
            [_nmc('A', '가나약국', '서울특별시 마포구 백범로 1', tel='02-555-1234')],
            [_mois('가나약국', '서울특별시 마포구 백범로 1', st='폐업', closed='2026-05-01', tel='02-555-1234'),
             _mois('새가나약국', '서울특별시 마포구 다른로 9', tel='02-555-1234')])
        self.assertEqual(got, {'A': 'ok'})

    def test_reopened_same_key_prefers_open(self):
        got, _, _ = self.verdict_of([_nmc('A', '가나약국', '서울특별시 마포구 백범로 1')],
                                    [_mois('가나약국', '서울특별시 마포구 백범로 1', st='폐업', closed='2015-01-01'),
                                     _mois('가나약국', '서울특별시 마포구 백범로 1')])
        self.assertEqual(got, {'A': 'ok'})

    # ── 3자 판정(S3 심평원) ────────────────────────────────────────────────
    def verdict3(self, nmc, mois, hira):
        recs, v, dropped, _ = C.build(nmc, mois, self.today, hira=hira)
        return {r['id']: r['v'] for r in recs}, v, dropped

    def test_old_closure_absent_in_hira_is_dropped(self):
        # 9/11 실측: 오래전 폐업 의심 152곳 중 151곳이 S3 에도 없었다 → 셋 중 둘이 "없다"
        got, _, dropped = self.verdict3([_nmc('A', '가나약국', '서울특별시 마포구 백범로 1', tel='02-999-0000')],
                                        [_mois('가나약국', '서울특별시 마포구 백범로 1', st='폐업', closed='2017-10-10', tel='02-999-0000')],
                                        hira=[])
        self.assertEqual(got, {})
        self.assertEqual(dropped, ['A'])

    def test_closure_but_hira_established_after_is_reopened(self):
        got, v, _ = self.verdict3([_nmc('A', '가나약국', '서울특별시 마포구 백범로 1', tel='02-999-0000')],
                                  [_mois('가나약국', '서울특별시 마포구 백범로 1', st='폐업', closed='2017-10-10', tel='02-999-0000')],
                                  hira=[_hira('가나약국', '서울특별시 마포구 백범로 1', estb='20180301')])
        self.assertEqual(got, {'A': 'ok'})
        self.assertEqual(v['reopened_by_s3'], 1)

    def test_closure_with_hira_established_before_is_flagged(self):
        # 모순(심평원엔 있는데 개설일이 폐업보다 앞) → 숨기지 않고 표시만
        got, _, _ = self.verdict3([_nmc('A', '가나약국', '서울특별시 마포구 백범로 1', tel='02-999-0000')],
                                  [_mois('가나약국', '서울특별시 마포구 백범로 1', st='폐업', closed='2017-10-10', tel='02-999-0000')],
                                  hira=[_hira('가나약국', '서울특별시 마포구 백범로 1', estb='20150101')])
        self.assertEqual(got, {'A': 'closed?'})

    def test_unmatched_in_mois_but_in_hira_is_ok(self):
        got, _, _ = self.verdict3([_nmc('A', '가나약국', '서울특별시 마포구 백범로 1')], [],
                                  hira=[_hira('가나약국', '서울특별시 마포구 백범로 1')])
        self.assertEqual(got, {'A': 'ok'})

    def test_resolve_geo(self):
        h = {'YPos': '37.5000', 'XPos': '127.0000'}
        # S1 이 S3 와 가깝다 → S1 맞음
        self.assertEqual(C._resolve_geo(37.5001, 127.0001, 37.6, 127.1, h), 'nmc')
        # S2 가 S3 와 가깝고 S1 만 멀다 → S3 좌표로 교체
        self.assertEqual(C._resolve_geo(37.6, 127.1, 37.5001, 127.0001, h), (37.5, 127.0))
        # 셋 다 어긋남 → 모름
        self.assertIsNone(C._resolve_geo(37.6, 127.1, 37.7, 127.2, h))
        self.assertIsNone(C._resolve_geo(37.6, 127.1, 37.7, 127.2, None))

    def test_lunch_and_call_flags_on_record(self):
        recs, _, _, _ = C.build([_nmc('A', '가나약국', '서울특별시 마포구 백범로 1', etc='평일 휴게시간 13:00-14:00 / 공휴일은 전화확인하세요')],
                                [_mois('가나약국', '서울특별시 마포구 백범로 1')], self.today)
        self.assertEqual(recs[0]['lunch'], '1300-1400')
        self.assertTrue(recs[0]['call'])
        self.assertEqual(recs[0]['t'][0], '0900-1800')
        self.assertEqual(recs[0]['t'][6], '')  # 일요일 미기재


class PublishIntervalTest(unittest.TestCase):
    # 실제 holidays.json 형식(하이픈 없음)
    H = [{'date': '20260924', 'status': 'holiday', 'name': '추석'},
         {'date': '20260925', 'status': 'holiday', 'name': '추석'},
         {'date': '20261003', 'status': 'holiday', 'name': '개천절'}]

    def test_weekly_when_no_holiday_near(self):
        self.assertEqual(C.publish_interval_days(date(2026, 9, 1), self.H), C.PUBLISH_INTERVAL_DAYS)

    def test_daily_within_lookahead(self):
        self.assertEqual(C.publish_interval_days(date(2026, 9, 14), self.H), 1)   # 추석 10일 전
        self.assertEqual(C.publish_interval_days(date(2026, 9, 25), self.H), 1)   # 당일

    def test_boundary_day_before_lookahead(self):
        self.assertEqual(C.publish_interval_days(date(2026, 9, 13), self.H), C.PUBLISH_INTERVAL_DAYS)  # 11일 전

    def test_past_holiday_does_not_count(self):
        self.assertEqual(C.publish_interval_days(date(2026, 10, 4), self.H), C.PUBLISH_INTERVAL_DAYS)

    def test_hyphen_format_also_accepted(self):
        self.assertEqual(C.publish_interval_days(date(2026, 9, 20), [{'date': '2026-09-25'}]), 1)

    def test_real_published_file_has_chuseok_in_range(self):
        # 발행된 파일로 — 형식을 짐작해 테스트를 짜면 초록불이 거짓말을 한다
        h = C.load_prev_holidays()
        if not h:
            self.skipTest('발행 파일 없음')
        self.assertEqual(C.publish_interval_days(date(2026, 9, 15), h), 1)

    def test_bad_or_missing_holidays_fall_back_to_weekly(self):
        self.assertEqual(C.publish_interval_days(date(2026, 9, 20), None), C.PUBLISH_INTERVAL_DAYS)
        self.assertEqual(C.publish_interval_days(date(2026, 9, 20), [{'date': 'x'}, {}]), C.PUBLISH_INTERVAL_DAYS)


if __name__ == '__main__':
    unittest.main()
