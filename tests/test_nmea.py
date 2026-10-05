import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import NMEA


def with_checksum(body):
    csum = 0
    for c in body:
        csum ^= ord(c)
    return '${}*{:02X}'.format(body, csum)


def test_gn_rewritten_to_gp_with_new_checksum():
    p = NMEA.Parser()
    s = with_checksum('GNGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,')
    assert p.parse_sentence(s)
    out = p.last_valid_sentence
    assert out.startswith('$GPGGA')
    assert out.endswith('\r\n')
    assert p._valid_nmea_checksum(out.strip())


def test_non_gn_passed_through():
    p = NMEA.Parser()
    s = with_checksum('GPZDA,201530.00,04,07,2002,00,00')
    assert p.parse_sentence(s)
    assert p.last_valid_sentence == s + '\r\n'


def test_bad_checksum_is_invalid():
    p = NMEA.Parser()
    assert not p.parse_sentence('$GNGGA,1,2*00')
    assert p.sentences_invalid == 1
    assert p.sentence_last_invalid_type == 'GGA'


def test_garbage_does_not_raise():
    p = NMEA.Parser()
    for s in ['', '$', '$GNGGA,no,star', 'xyz', '$GN*ZZ']:
        assert not p.parse_sentence(s)
    assert p.sentences_invalid == 5


def test_short_payload_is_forwardable_but_counted_as_parse_error():
    p = NMEA.Parser()
    assert p.parse_sentence(with_checksum('GNGGA,123519'))      # envelope fine: safe to forward
    assert p.last_valid_sentence.startswith('$GPGGA') and p.last_valid_sentence.endswith('\r\n')
    assert p.sentences_valid == 1 and p.sentences_invalid == 0
    assert p.parse_errors == 1 and p.sentence_last_error_type == 'GGA'
    assert p.sentences_parsed == 0


def test_empty_fix_field_is_a_parse_error_not_an_invalid_sentence():
    p = NMEA.Parser()
    assert p.parse_sentence(with_checksum('GNGGA,,,,,,,,,,,,,,'))
    assert p.parse_errors == 1 and p.sentences_invalid == 0


def test_gsa_counts_all_twelve_slots():
    p = NMEA.Parser()
    prns = ','.join(str(n) for n in range(1, 13))
    assert p.parse_sentence(with_checksum('GNGSA,A,3,' + prns + ',1.0,1.0,1.0'))
    assert p.birds_GPS == 12


def test_rmc_void_does_not_store_position():
    p = NMEA.Parser()
    assert p.parse_sentence(with_checksum(
        'GNRMC,123519,V,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W'))
    assert p.lat == ''


def test_prefix_only_replacement():
    p = NMEA.Parser()
    assert p._fix_sentence('$GNXXX,$GN*00').startswith('$GPXXX,$GN*')


def test_snapshot_resets_counters_but_keeps_last_types():
    p = NMEA.Parser()
    p.parse_sentence(with_checksum('GPZDA,201530.00,04,07,2002,00,00'))
    p.parse_sentence('$GNGGA,1,2*00')
    snap = p.snapshot_and_reset()
    assert snap == {'rcv': 2, 'val': 1, 'inv': 1, 'par': 1, 'ign': 0}
    assert p.sentences_received == 0 and p.sentences_valid == 0
    assert p.sentence_last_valid_type == 'ZDA'
    assert p.sentence_last_invalid_type == 'GGA'


def test_gsv_sums_across_talkers():
    p = NMEA.Parser()
    assert p.parse_sentence(with_checksum('GPGSV,3,1,09,01,40,083,46'))
    assert p.birds_in_view == 9
    assert p.parse_sentence(with_checksum('GLGSV,1,1,04,65,40,083,46'))
    assert p.birds_in_view == 13
    assert p.parse_sentence(with_checksum('GPGSV,3,2,10,01,40,083,46'))
    assert p.birds_in_view == 14


def test_zda_parsed():
    p = NMEA.Parser()
    assert p.parse_sentence(with_checksum('GNZDA,201530.00,04,07,2002,00,00'))
    assert p.date == '04/07/2002'
    assert p.timezone == '00h00m'


def test_coordinate_strings():
    p = NMEA.Parser()
    p.lat, p.NS, p.lon, p.EW = '4807.038', 'N', '01131.000', 'E'
    assert p.get_lat_string() == 'N48' + chr(176) + '7.04'
    assert p.get_lon_string() == 'E011' + chr(176) + '31.0'
    p.lat = 'garbage'
    assert p.get_lat_string() == ''


def test_dop_grades():
    p = NMEA.Parser()
    for value, grade in [(0.5, 'A'), (1, 'B'), (1.9, 'B'), (2, 'C'), (5, 'D'),
                         (10, 'E'), (20, 'F'), (30, 'F')]:
        p.HDOP = value
        assert p.get_dop_string('HDOP') == grade
    p.HDOP = ''
    assert p.get_dop_string('HDOP') == '?'


def test_display_signature_changes_with_content():
    p = NMEA.Parser()
    before = p.display_signature()
    p.parse_sentence(with_checksum('GNZDA,201530.00,04,07,2002,00,00'))
    assert p.display_signature() != before


def sentence_with_checksum_text(body, cksum):
    return '${}*{}'.format(body, cksum)


def test_strict_checksum_format():
    body = 'GPGGA,1,2'
    csum = 0
    for c in body:
        csum ^= ord(c)
    good = '{:02X}'.format(csum)
    assert NMEA.valid_checksum(sentence_with_checksum_text(body, good))
    assert NMEA.valid_checksum(sentence_with_checksum_text(body, good.lower()))
    assert not NMEA.valid_checksum(sentence_with_checksum_text(body, '0x' + good))
    assert not NMEA.valid_checksum(sentence_with_checksum_text(body, '+' + good[1]))
    assert not NMEA.valid_checksum(sentence_with_checksum_text(body, '0' + good))     # three digits
    assert not NMEA.valid_checksum(sentence_with_checksum_text(body, good[1]))        # one digit
    assert not NMEA.valid_checksum(sentence_with_checksum_text(body, good + ' '))
    assert not NMEA.valid_checksum(body + '*' + good)                                  # no leading '$'


def gsv_msg(talker, m, n, sats, in_view=12):
    fields = ''.join(',{:02d},40,083,{}'.format(prn, cn) for prn, cn in sats)
    return with_checksum('{}GSV,{},{},{:02d}{}'.format(talker, n, m, in_view, fields))


CYCLE = [gsv_msg('GP', 1, 3, [(1, 40), (2, 41), (3, 42), (4, 43)]),
         gsv_msg('GP', 2, 3, [(5, 40), (6, 41), (7, 42), (8, 43)]),
         gsv_msg('GP', 3, 3, [(9, 40), (10, 41), (11, 42), (12, 43)])]


def test_gsv_cycle_with_lost_first_message_is_discarded():
    p = NMEA.Parser()
    for m in CYCLE:
        p.parse_sentence(m)
    assert p.cn0_stats()[0] == 12 and p.cn0_version == 1
    p.parse_sentence(CYCLE[1])
    p.parse_sentence(CYCLE[2])                     # message 1 was lost
    assert p.cn0_version == 1 and p.cn0_stats()[0] == 12     # previous complete cycle kept
    for m in CYCLE:                                # the next full cycle is accepted again
        p.parse_sentence(m)
    assert p.cn0_version == 2 and p.cn0_stats()[0] == 12


def test_gsv_cycle_with_missing_middle_message_is_discarded():
    p = NMEA.Parser()
    p.parse_sentence(CYCLE[0])
    p.parse_sentence(CYCLE[2])                     # message 2 lost
    assert p.cn0_version == 0 and p.cn0_stats()[0] == 0


def test_talker_that_stops_expires_and_version_is_bumped():
    p = NMEA.Parser()
    p.parse_sentence(gsv_msg('GP', 1, 1, [(1, 40), (2, 41)], in_view=8), 1000)
    p.parse_sentence(gsv_msg('BD', 1, 1, [(5, 38), (6, 39)], in_view=6), 1000)
    assert set(p.cn0_by_talker) == {'GP', 'BD'} and p.birds_in_view == 14
    version = p.cn0_version
    p.parse_sentence(gsv_msg('GP', 1, 1, [(1, 40), (2, 41)], in_view=8), 26000)   # BD silent for 25 s
    assert set(p.cn0_by_talker) == {'GP'} and 'BD' not in p.sats_by_talker
    assert p.birds_in_view == 8
    assert p.cn0_version > version + 1             # expiry bumped it (plus the new GP cycle)


def test_gsa_counts_expire_with_their_talker():
    p = NMEA.Parser()
    prns = ','.join(str(n) for n in range(1, 4))
    p.parse_sentence(with_checksum('BDGSA,A,3,' + prns + ',,,,,,,,,,1.0,1.0,1.0'), 1000)
    assert p.birds_BD == 3
    p.parse_sentence(with_checksum('GPZDA,201530.00,04,07,2002,00,00'), 30000)
    assert p.birds_BD == 0


def test_cn0_settled_waits_for_all_talkers():
    p = NMEA.Parser()
    assert p.cn0_settled(0)                                        # no timing known (tests): settled
    p.parse_sentence(gsv_msg('GP', 1, 1, [(1, 40)]), 1000)
    assert not p.cn0_settled(1100) and not p.cn0_settled(1000 + NMEA.GSV_SETTLE_MS - 1)
    assert p.cn0_settled(1000 + NMEA.GSV_SETTLE_MS)


def test_gn_gsa_with_system_ids_counts_each_system():
    p = NMEA.Parser()
    gps = ','.join(str(n) for n in range(1, 7)) + ',' * 6
    bd = '1,2,3' + ',' * 9
    assert p.parse_sentence(with_checksum('GNGSA,A,3,' + gps + ',1.0,1.0,1.0,1'))
    assert p.parse_sentence(with_checksum('GNGSA,A,3,' + bd + ',1.0,1.0,1.0,4'))
    assert p.birds_GPS == 6 and p.birds_BD == 3            # the second sentence did not overwrite the first


def test_pmtkspf_bumps_version():
    p = NMEA.Parser()
    assert p.spf_version == 0
    p.parse_sentence(with_checksum('PMTKSPF,2'))
    assert p.module_jam_status == 2 and p.spf_version == 1
