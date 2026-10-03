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


def test_short_payload_counted_invalid_not_crash():
    p = NMEA.Parser()
    assert not p.parse_sentence(with_checksum('GNGGA,123519'))
    assert p.sentences_valid == 0


def test_gga_empty_fix_field_is_invalid():
    p = NMEA.Parser()
    assert not p.parse_sentence(with_checksum('GNGGA,,,,,,,,,,,,,,'))


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
