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
        self.sentence_last_parsed_type = ''
        self.sentence_last_valid_type = ''
        self.sentence_last_invalid_type = ''
        self.sentence_last_ignored_type = ''
        
    def parse_sentence(self, sentence):
        """Parse one sentence. Returns True if it was valid (last_valid_sentence updated)."""
        sentence = sentence.strip()
        self.sentences_received += 1
        if self._validate_nmea(sentence):
            try:
                self._dispatch(sentence)
            except (ValueError, IndexError):
                # well-formed envelope but broken payload
                self.sentences_invalid += 1
                self.sentence_last_invalid_type = sentence[3:6]
                return False
            self.sentences_valid += 1
            self.last_valid_sentence = self._fix_sentence(sentence) + '\r\n'
            return True
        self.sentence_last_invalid_type = sentence[3:6]
        self.sentences_invalid += 1
        return False

    def _dispatch(self, sentence):
        sentence_type = sentence[3:6]
        self.sentence_last_valid_type = sentence_type
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

    def _parse_rmc(self, payload):
        self.time = payload[1]
        if payload[2] == 'A':  # ignore position from void fixes
            self.lat = payload[3]
            self.NS = payload[4]
            self.lon = payload[5]
            self.EW = payload[6]
        self.magvar = str(payload[10]) + payload[11]

    def _parse_gsv(self, payload):
        self.birds_in_view = payload[3]

    def _parse_gsa(self, payload):
        mode = int(payload[2])
        self.PDOP = payload[15]
        self.HDOP = payload[16]
        self.VDOP = payload[17]
        cnt_GPS = cnt_SBAS = cnt_GLONASS = cnt_OTHER = 0
        for bird in payload[3:15]:
            if len(bird) > 0:
                prn = int(bird)
                if 1 <= prn <= 32:
                    cnt_GPS += 1
                elif 33 <= prn <= 64:
                    cnt_SBAS += 1
                elif 65 <= prn <= 96:
                    cnt_GLONASS += 1
                else:
                    cnt_OTHER += 1
        self.mode = {2: '2D', 3: '3D'}.get(mode, '')
        self.birds_GPS = cnt_GPS
        self.birds_SBAS = cnt_SBAS
        self.birds_GLONASS = cnt_GLONASS
        self.birds_OTHER = cnt_OTHER

    def _parse_zda(self, payload):
        self.date = payload[2] + '/' + payload[3] + '/' + payload[4]
        self.timezone = payload[5] + 'h' + payload[6] + 'm'

    _handlers = {'GGA': _parse_gga, 'RMC': _parse_rmc, 'GSV': _parse_gsv,
                 'GSA': _parse_gsa, 'ZDA': _parse_zda}

    def _calculate_nmea_checksum(self, sentence):
        tmp = sentence.split('*')
        chksumdata = tmp[0].replace('$', '')
        csum = 0
        for c in chksumdata:
            csum ^= ord(c)
        return csum

    def _valid_nmea_checksum(self, sentence):
        tmp = sentence.split('*')
        if len(tmp) < 2:
            return False
        csum = self._calculate_nmea_checksum(sentence)
        try:
            return csum == int(tmp[1].strip(), 16)
        except ValueError:
            return False

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