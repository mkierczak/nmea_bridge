class parser(object):
    
    def __init__(self):
        self.time = ''
        self.lat = ''
        self.lon = ''
        self.NS = ''
        self.EW = ''
        self.birds_in_use = 0
        self.birds_in_view = 0
        self.fix_type = 'NO FIX'
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
        sentence = sentence.strip()
        self.sentences_received += 1
        if self._validate_nmea(sentence):
            #print(f'Valid: {sentence}')
            self.sentences_valid += 1
            self.last_valid_sentence = self._fix_sentence(sentence)
            self._dispatch(sentence)
        else:
            print(f'INVALID: {sentence}')
            self.sentence_last_ignored_type = sentence[3:6]
            self.sentences_invalid += 1
        
    def _dispatch(self, sentence):
        sentence_type = sentence[3:6]
        self.sentence_last_valid_type = sentence_type
        if sentence_type == 'GGA':
            self.sentence_last_parsed_type = sentence_type
            self.sentences_parsed += 1
            tmp = sentence.split('*')
            payload = tmp[0].split(',')
            self.time = payload[1]
            self.lat = payload[2]
            self.NS = payload[3]
            self.lon = payload[4]
            self.EW = payload[5]
            fix = int(payload[6])
            if fix == 0:
                self.fix_type = 'NO FIX'
            elif fix == 1:
                self.fix_type = 'GPS'
            elif fix == 2:
                self.fix_type = 'DGPS'
            self.birds_in_use = payload[7]
            self.HDOP = payload[8]
        elif sentence_type == 'RMC':
            self.sentence_last_parsed_type = sentence_type
            self.sentences_parsed += 1
            tmp = sentence.split('*')
            payload = tmp[0].split(',')
            self.time = payload[1]
            self.lat = payload[3]
            self.NS = payload[4]
            self.lon = payload[5]
            self.EW = payload[6]
            self.magvar = str(payload[10]) + payload[11]
        elif sentence_type == 'GSV':
            self.sentence_last_parsed_type = sentence_type
            self.sentences_parsed += 1
            tmp = sentence.split('*')
            payload = tmp[0].split(',')
            self.birds_in_view = payload[3]
        elif sentence_type == 'GSA':
            self.sentence_last_parsed_type = sentence_type
            self.sentences_parsed += 1
            tmp = sentence.split('*')
            payload = tmp[0].split(',')
            mode = int(payload[2])
            if mode == 2:
                self.mode = '2D'
            elif mode == 3:
                self.mode = '3D'
            else:
                self.mode = ''
            self.PDOP = payload[15]
            self.HDOP = payload[16]
            self.VDOP = payload[17]
            cnt_GPS = 0
            cnt_SBAS = 0
            cnt_GLONASS = 0
            cnt_OTHER = 0
            for bird in payload[3:14]:
                if len(bird) > 0:
                    prn = int(bird)
                    if prn >= 1 and prn <= 32:
                        cnt_GPS += 1
                    elif prn >= 33 and prn <= 64:
                        cnt_SBAS += 1
                    elif prn >= 65 and prn <= 96:
                        cnt_GLONASS += 1
                    else:
                        cnt_OTHER += 1
            self.birds_GPS = cnt_GPS
            self.birds_SBAS = cnt_SBAS
            self.birds_GLONASS = cnt_GLONASS
            self.birds_OTHER = cnt_OTHER
        else:
            self.sentence_last_ignored_type = sentence_type
            self.sentences_ignored += 1
        
    def _nmea_checksum(self, sentence):
        sentence = sentence.split('*')
        cksum = sentence[1]
        chksumdata = sentence[0].replace('$', '')
        csum = 0
        for c in chksumdata:
            csum ^= ord(c)
        try:
            if hex(csum) == hex(int(cksum.strip(), 16)):
                return True
            else:
                #print(f'data: {chksumdata} -- CHKSUM: {cksum}')
                return False
        except:
            return False
    
    def _fix_sentence(self, sentence):
        sentence = sentence.replace('GN', 'GP')
        sentence = sentence.split('*')
        cksum = sentence[1]
        chksumdata = sentence[0].replace('$', '')
        csum = 0
        for c in chksumdata:
            csum ^= ord(c)
        string_value = '{:02X}'.format(csum)
        return sentence[0] + '*' + string_value

    def _validate_nmea(self, sentence):
        try:
            if not sentence.startswith('$'):
                return False
            if not "*" in sentence:
                return False
            if len(sentence) > 82:
                return False
            crc_check = self._nmea_checksum(sentence)
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
    
    def get_lat_string(self):
        if len(self.lat) > 0:
            try:
                deg = self.lat[0:2]
                mm = round(float(self.lat[2:]),2)
                NS = self.NS
                #tmp = f'{NS}{deg}°{mm}'
                tmp = "{}{}{}{}".format(NS, deg, chr(176), mm)
                return tmp
            except:
                return ''
        else:
            return ''

    def get_lon_string(self):
        if len(self.lon) > 0:
            try:
                deg = self.lon[0:3]
                mm = round(float(self.lon[3:]),2)
                EW = self.EW
                #tmp = f'{EW}{deg}°{mm}'
                tmp = "{}{}{}{}".format(EW, deg, chr(176), mm)
                return tmp
            except:
                return ''
        else:
            return ''
    
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