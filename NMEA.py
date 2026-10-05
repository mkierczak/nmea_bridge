try:
    from utime import ticks_diff as _ticks_diff
except ImportError:
    def _ticks_diff(a, b):
        return a - b

TALKER_TTL_MS = 20 * 1000     # per-talker GSV/GSA data older than this is dropped (talker went silent)
GSV_SETTLE_MS = 800           # a GSV cycle counts as complete once no talker finished for this long
_HEX = '0123456789abcdefABCDEF'


def _to_units(value, hemisphere, deg_digits):
    """'ddmm.mmmm' / 'dddmm.mmmm' -> signed integer in 1e-4 arc-minutes (exact, no float rounding)."""
    deg = int(value[:deg_digits])
    mins, _, frac = value[deg_digits:].partition('.')
    units = (deg * 60 + int(mins)) * 10000 + int((frac + '0000')[:4])
    return -units if hemisphere in ('S', 'W') else units


def _days_from_civil(y, m, d):
    """Days since 1970-01-01 (proleptic Gregorian)."""
    y -= m <= 2
    era = (y if y >= 0 else y - 399) // 400
    yoe = y - era * 400
    doy = (153 * (m + (-3 if m > 2 else 9)) + 2) // 5 + d - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def valid_checksum(sentence):
    """True if 'sentence' ('$...*hh') has exactly two hex digits after '*' matching the XOR of the body."""
    if not sentence.startswith('$'):
        return False
    body, star, cksum = sentence[1:].partition('*')
    if not star or len(cksum) != 2 or cksum[0] not in _HEX or cksum[1] not in _HEX:
        return False
    csum = 0
    for c in body:
        csum ^= ord(c)
    return csum == int(cksum, 16)


class Parser(object):
    
    def __init__(self):
        self.time = '??'
        self.timezone = '??'
        self.date = ''
        self.lat = ''
        self.lon = ''
        self.NS = ''
        self.EW = ''
        self.birds_in_use = 0
        self.birds_in_view = 0
        self.fix_type = 'NO'
        self.HDOP = 30
        self.PDOP = 30
        self.VDOP = 30
        self.mode = ''
        self.magvar = ''
        self.birds_GPS = 0
        self.birds_GLONASS = 0
        self.birds_SBAS = 0
        self.birds_OTHER = 0
        self.last_valid_sentence = ''
        
        # Stats
        self.sentences_received = 0
        self.sentences_valid = 0
        self.sentences_invalid = 0
        self.sentences_parsed = 0
        self.sentences_ignored = 0
        self.parse_errors = 0       # valid envelope but a field the display parser could not read
        self.sentence_last_error_type = ''
        self.sentence_last_parsed_type = ''
        self.sentence_last_valid_type = ''
        self.sentence_last_invalid_type = ''
        self.sentence_last_ignored_type = ''
        self._view_by_talker = {}
        self._cn0_partial = {}      # talker -> C/N0 list of the GSV cycle being received
        self.cn0_by_talker = {}     # talker -> C/N0 list of the last complete GSV cycle
        self.cn0_version = 0        # bumped whenever a GSV cycle completes
        self.pmtk_acks = {}         # PMTK command number -> ack flag (3 = success)
        self.module_jam_status = 0  # $PMTKSPF: 0 unknown, 1 healthy, 2 warning, 3 critical
        self.sats_by_talker = {}    # talker -> [(prn, elevation or None, cn0)] of last complete GSV cycle
        self._sats_partial = {}
        self._gsv_next = {}         # talker -> next expected GSV message number (0 = wait for message 1)
        self._cn0_ms = {}           # talker -> rx time of its last completed GSV cycle
        self._gsa_ms = {}           # talker -> rx time of its last GSA
        self.cn0_ms = None          # rx time of the last completed GSV cycle (or data expiry), None in tests
        self.spf_version = 0        # bumped whenever the module reports $PMTKSPF
        self.birds_BD = 0
        # fix data for plausibility checks (valid RMC / GGA only)
        self.fix_count = 0          # bumped on every valid (status A) RMC
        self.lat_u = self.lon_u = 0 # signed position in 1e-4 arc-minutes
        self.sog_kn = None
        self.cog_deg = None
        self.utc_days = 0           # days since 1970-01-01 of the last RMC
        self.utc_ms = 0             # milliseconds of day of the last RMC
        self.rx_ms = None           # local ticks_ms when the last RMC was received
        self._rx_ms = None
        self.alt_m = None
        self.alt_version = 0

    def snapshot_and_reset(self):
        """Return counters as a dict and zero them. Last-seen types are kept."""
        snap = {'rcv': self.sentences_received, 'val': self.sentences_valid,
                'inv': self.sentences_invalid, 'par': self.sentences_parsed,
                'ign': self.sentences_ignored}
        self.sentences_received = 0
        self.sentences_valid = 0
        self.sentences_invalid = 0
        self.sentences_parsed = 0
        self.sentences_ignored = 0
        return snap

    def display_signature(self):
        """Tuple of everything the screens show; equal tuples mean no redraw is needed."""
        return (self.time, self.date, self.lat, self.lon, self.NS, self.EW,
                self.fix_type, self.mode, self.birds_in_use, self.birds_in_view,
                self.PDOP, self.HDOP, self.VDOP, self.birds_GPS, self.birds_SBAS,
                self.birds_GLONASS, self.birds_BD, self.birds_OTHER, self.last_valid_sentence,
                self.sentence_last_valid_type, self.sentence_last_invalid_type,
                self.sentence_last_parsed_type, self.sentence_last_ignored_type,
                self.cn0_stats(), self.pmtk_acks.get(286))

    def parse_sentence(self, sentence, rx_ms=None):
        """Parse one sentence. Returns True if the *envelope* is valid ('$', checksum, length): such a
        sentence is safe to forward (last_valid_sentence holds it, GN rewritten to GP) even when a field
        of it could not be read for the display, which is only counted in parse_errors.

        rx_ms is the local ticks_ms at which the sentence arrived (used for time checks and expiry)."""
        sentence = sentence.strip()
        self._rx_ms = rx_ms
        self.sentences_received += 1
        if not self._validate_nmea(sentence):
            self.sentence_last_invalid_type = sentence[3:6]
            self.sentences_invalid += 1
            return False
        self.sentences_valid += 1
        self.sentence_last_valid_type = sentence[3:6]
        self.last_valid_sentence = self._fix_sentence(sentence) + '\r\n'
        try:
            self._dispatch(sentence)
        except (ValueError, IndexError):
            self.parse_errors += 1
            self.sentence_last_error_type = sentence[3:6]
        if rx_ms is not None:
            self._expire(rx_ms)
        return True

    def cn0_settled(self, now_ms):
        """True when the last GSV cycle is old enough that all talkers have finished (or no timing known)."""
        return self.cn0_ms is None or _ticks_diff(now_ms, self.cn0_ms) >= GSV_SETTLE_MS

    def _expire(self, now_ms):
        """Drop data of talkers that stopped sending so their last values do not linger."""
        changed = False
        for talker in list(self._cn0_ms):
            if _ticks_diff(now_ms, self._cn0_ms[talker]) > TALKER_TTL_MS:
                for d in (self._cn0_ms, self.cn0_by_talker, self.sats_by_talker, self._view_by_talker,
                          self._cn0_partial, self._sats_partial, self._gsv_next):
                    d.pop(talker, None)
                changed = True
        if changed:
            self.birds_in_view = sum(self._view_by_talker.values())
            self.cn0_version += 1
            self.cn0_ms = now_ms
        for talker in list(self._gsa_ms):
            if _ticks_diff(now_ms, self._gsa_ms[talker]) > TALKER_TTL_MS:
                del self._gsa_ms[talker]
                self._set_birds(talker, [])

    def _dispatch(self, sentence):
        sentence_type = sentence[3:6]
        handler = self._handlers.get(sentence_type)
        if handler is None:
            self.sentence_last_ignored_type = sentence_type
            self.sentences_ignored += 1
            return
        payload = sentence.split('*')[0].split(',')
        handler(self, payload)
        self.sentence_last_parsed_type = sentence_type
        self.sentences_parsed += 1

    def _parse_gga(self, payload):
        fix = int(payload[6])
        self.time = payload[1]
        self.lat = payload[2]
        self.NS = payload[3]
        self.lon = payload[4]
        self.EW = payload[5]
        self.fix_type = {0: 'NO', 1: 'GPS', 2: 'DGPS'}.get(fix, '?')
        self.birds_in_use = payload[7]
        self.HDOP = payload[8]
        if fix > 0:
            try:
                self.alt_m = float(payload[9])
                self.alt_version += 1
            except (ValueError, IndexError):
                pass

    def _parse_rmc(self, payload):
        self.time = payload[1]
        if payload[2] == 'A':  # ignore position from void fixes
            self.lat = payload[3]
            self.NS = payload[4]
            self.lon = payload[5]
            self.EW = payload[6]
            self._store_fix(payload)
        self.magvar = str(payload[10]) + payload[11]

    def _store_fix(self, payload):
        """Numeric fix data for plausibility checks. Never raises: a sentence that is otherwise
        fine must still be forwarded, so bad optional fields just leave the fix data unchanged."""
        try:
            lat_u = _to_units(payload[3], payload[4], 2)
            lon_u = _to_units(payload[5], payload[6], 3)
            t, _, frac = payload[1].partition('.')
            ms = (int(t[0:2]) * 3600 + int(t[2:4]) * 60 + int(t[4:6])) * 1000 + int((frac + '000')[:3])
            d = payload[9]
            days = _days_from_civil(2000 + int(d[4:6]), int(d[2:4]), int(d[0:2]))
        except (ValueError, IndexError):
            return
        self.lat_u, self.lon_u, self.utc_days, self.utc_ms = lat_u, lon_u, days, ms
        try:
            self.sog_kn = float(payload[7]) if payload[7] else None
            self.cog_deg = float(payload[8]) if payload[8] else None
        except ValueError:
            self.sog_kn = self.cog_deg = None
        self.rx_ms = self._rx_ms
        self.fix_count += 1

    def _parse_gsv(self, payload):
        # each talker (GP/GL/...) reports its own satellites in view; show the total
        talker = payload[0][1:3]
        self._view_by_talker[talker] = int(payload[3])
        self.birds_in_view = sum(self._view_by_talker.values())
        # per-satellite groups of 4 fields: PRN, elevation, azimuth, C/N0 (dB-Hz; 0/empty = not tracked)
        total_msgs, msg_no = int(payload[1]), int(payload[2])
        sats = []
        for base in range(4, len(payload) - 3, 4):
            el = payload[base + 1]
            sats.append((int(payload[base]) if payload[base] else 0,
                         int(el) if el else None,
                         int(payload[base + 3]) if payload[base + 3] else 0))
        cn0 = [c for _, _, c in sats]
        # a GSV cycle is messages 1..total in order; a gap (a lost message) discards the partial cycle
        if msg_no == 1:
            self._cn0_partial[talker] = []
            self._sats_partial[talker] = []
            self._gsv_next[talker] = 2
        elif self._gsv_next.get(talker) == msg_no:
            self._gsv_next[talker] = msg_no + 1
        else:
            self._gsv_next[talker] = 0
            self._cn0_partial.pop(talker, None)
            self._sats_partial.pop(talker, None)
            return
        self._cn0_partial[talker].extend(cn0)
        self._sats_partial[talker].extend(sats)
        if msg_no == total_msgs:
            self.cn0_by_talker[talker] = self._cn0_partial.pop(talker)
            self.sats_by_talker[talker] = self._sats_partial.pop(talker)
            self._gsv_next[talker] = 0
            self.cn0_version += 1
            if self._rx_ms is not None:
                self._cn0_ms[talker] = self.cn0_ms = self._rx_ms

    def cn0_stats(self):
        """(tracked satellite count, mean C/N0, max C/N0) over the last complete GSV cycles."""
        tracked = [c for lst in self.cn0_by_talker.values() for c in lst if c > 0]
        if not tracked:
            return 0, 0, 0
        return len(tracked), sum(tracked) / len(tracked), max(tracked)

    def _parse_pmtk(self, payload):
        if payload[0] == '$PMTK001':  # ack: $PMTK001,<cmd>,<flag>
            self.pmtk_acks[int(payload[1])] = int(payload[2])
        elif payload[0] == '$PMTKSPF':  # module's jamming detector status (PMTK838)
            self.module_jam_status = int(payload[1])
            self.spf_version += 1

    _SYSTEM_TALKER = {1: 'GP', 2: 'GL', 4: 'BD'}   # NMEA 4.10 GSA system ID -> talker

    def _parse_gsa(self, payload):
        mode = int(payload[2])
        self.PDOP = payload[15]
        self.HDOP = payload[16]
        self.VDOP = payload[17]
        talker = payload[0][1:3]
        if talker == 'GN' and len(payload) > 18 and payload[18]:
            # a combined talker with a system ID: count each system on its own so the GSA of one
            # system does not overwrite the other's
            talker = self._SYSTEM_TALKER.get(int(payload[18]), 'GN')
        prns = [int(bird) for bird in payload[3:15] if len(bird) > 0]
        self.mode = {2: '2D', 3: '3D'}.get(mode, '')
        self._set_birds(talker, prns)
        if self._rx_ms is not None:
            self._gsa_ms[talker] = self._rx_ms

    def _set_birds(self, talker, prns):
        if talker == 'BD' or talker == 'GB':   # BeiDou (L76B: GPS and BeiDou only)
            self.birds_BD = len(prns)
        elif talker == 'GL':
            self.birds_GLONASS = len(prns)
        else:                                  # GP / GN: classify by PRN range
            cnt_GPS = cnt_SBAS = cnt_OTHER = 0
            for prn in prns:
                if 1 <= prn <= 32:
                    cnt_GPS += 1
                elif 33 <= prn <= 64:
                    cnt_SBAS += 1
                else:
                    cnt_OTHER += 1
            self.birds_GPS = cnt_GPS
            self.birds_SBAS = cnt_SBAS
            self.birds_OTHER = cnt_OTHER

    def _parse_zda(self, payload):
        self.date = payload[2] + '/' + payload[3] + '/' + payload[4]
        self.timezone = payload[5] + 'h' + payload[6] + 'm'

    _handlers = {'GGA': _parse_gga, 'RMC': _parse_rmc, 'GSV': _parse_gsv,
                 'GSA': _parse_gsa, 'ZDA': _parse_zda, 'TK0': _parse_pmtk, 'TKS': _parse_pmtk}

    def _calculate_nmea_checksum(self, sentence):
        tmp = sentence.split('*')
        chksumdata = tmp[0][1:] if tmp[0].startswith('$') else tmp[0]
        csum = 0
        for c in chksumdata:
            csum ^= ord(c)
        return csum

    def _valid_nmea_checksum(self, sentence):
        return valid_checksum(sentence)

    def _fix_sentence(self, sentence):
        if sentence.startswith('$GN'):
            sentence = '$GP' + sentence[3:]
            new_checksum = self._calculate_nmea_checksum(sentence)
            tmp = sentence.split('*')
            string_value = '{:02X}'.format(new_checksum)
            fixed = tmp[0] + '*' + string_value
        else:
            fixed = sentence
        #print(fixed)
        return fixed

    def _validate_nmea(self, sentence):
        try:
            if not sentence.startswith('$'):
                return False
            if not "*" in sentence:
                return False
            if len(sentence) > 82:
                return False
            crc_check = self._valid_nmea_checksum(sentence)
            return crc_check
        except:
            return False
    
    def get_time_string(self):
        if len(self.time) > 0:
            try:
                hh = self.time[0:2]
                mm = self.time[2:4]
                ss = self.time[4:6]
                tmp = f'{hh}:{mm}:{ss}'
                return tmp
            except:
                return ''
        else:
            return ''
    
    def _coord_string(self, value, hemisphere, deg_digits):
        if len(value) > 0:
            try:
                mm = round(float(value[deg_digits:]), 2)
                return "{}{}{}{}".format(hemisphere, value[0:deg_digits], chr(176), mm)
            except ValueError:
                return ''
        return ''

    def get_lat_string(self):
        return self._coord_string(self.lat, self.NS, 2)

    def get_lon_string(self):
        return self._coord_string(self.lon, self.EW, 3)

    def get_dop_string(self, type = 'HDOP'):
        try:
            if type == 'PDOP':
                dop = float(self.PDOP)
            elif type == 'HDOP':
                dop = float(self.HDOP)
            elif type == 'VDOP':
                dop = float(self.VDOP)
            else:
                dop = float(self.HDOP)
            if dop < 1:
                return("A") # Ideal
            elif dop >= 1 and dop < 2:
                return("B") # Excellent
            elif dop >= 2 and dop < 5:
                return("C") # Good
            elif dop >= 5 and dop < 10:
                return("D") # Moderate
            elif dop >= 10 and dop < 20:
                return("E") # Fair
            else:
                return("F") # Poor
        except:
            return("?")