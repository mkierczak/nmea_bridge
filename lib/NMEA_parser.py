"""
NMEA parser Copyright (c) Marcin Kierczak 2024.
License:
Uses the chain of responsibility OOP design pattern internally
"""

class NMEA_parser(object):
    
    def __init__(self):
        # Build the list of handlers using instance methods
        self.handlers = [
            self._gga_handler,
            self._rmc_handler,
            self._gll_handler,
            self._vtg_handler,
            self._gsa_handler
        ]
        
        # Initialize internal state
        self.state = {}
        self.state['lat'] = None
        self.state['lon'] = None
        self.state['NS'] = None
        self.state['EW'] = None
        self.state['altitude'] = None
        self.state['fix'] = 0
        self.state['time'] = None
        self.state['date'] = None
        self.state['birds_in_view'] = None
        self.state['birds_in_use'] = None
        self.state['birds'] = {}
        self.state['HDOP'] = None
        self.state['PDOP'] = None
        self.state['VDOP'] = None
        self.state['mode'] = None
        self.state['SOG'] = None # Speed Over Ground
        self.state['CMG'] = None # Course Made Good, True
        self.state['var'] = None # magnetic variation
        self.state['var_EW'] = None # magnetic variation direction E/W
        
        self.stats = {}
        self.stats['sentences_received'] = 0
        self.stats['sentences_parsed'] = 0
        self.stats['sentences_failed'] = 0
        self.stats['sentences_unsupported'] = 0
        self.stats['sentence_counters'] = {}
        self.stats['last_valid_fix'] = 0
        self.stats['last_sentence_time'] = None

    def _build_chain(self):
        next_handler = self._default_handler
        for handler in reversed(self.handlers):
            # Capture handler and next_handler correctly in the closure
            next_handler = (lambda h, nh: lambda s: h(s, nh))(handler, next_handler)
        return next_handler

    def handle_nmea_sentence(self, nmea_sentence):
        chain = self._build_chain()
        chain(nmea_sentence)

    def _validate_nmea_checksum(sentence):
        sentence = sentence.strip()
        # Check if the sentence starts with '$' and does contain '*'
        if not sentence.startswith("$") or "*" not in sentence:
            return False

        # Extract the checksum from the sentence
        checksum_str = sentence.split("*")[-1]

        # Calculate the expected checksum
        expected_checksum = 0
        for char in sentence[1:-3]:  # Exclude the leading '$' and trailing '*' and checksum itself
            expected_checksum ^= ord(char)

        # Convert the checksum string to integer
        try:
            checksum = int(checksum_str, 16)
        except ValueError:
            return False  # Invalid checksum format

        # Compare the calculated checksum with the expected checksum
        return checksum == expected_checksum

    def _gga_handler(self, nmea_sentence, next_handler):
        if nmea_sentence.startswith('$GPGGA'):
            print(f"Processing GGA sentence: {nmea_sentence}")
            # Parsing logic for GGA sentences
            return True
        else:
            return next_handler(nmea_sentence)

    def _rmc_handler(self, nmea_sentence, next_handler):
        if nmea_sentence.startswith('$GPRMC'):
            print(f"Processing RMC sentence: {nmea_sentence}")
            # Parsing logic for RMC sentences
            return True
        else:
            return next_handler(nmea_sentence)

    def _gll_handler(self, nmea_sentence, next_handler):
        if nmea_sentence.startswith('$GPGLL'):
            print(f"Processing GLL sentence: {nmea_sentence}")
            # Parsing logic for GLL sentences
            return True
        else:
            return next_handler(nmea_sentence)

    def _vtg_handler(self, nmea_sentence, next_handler):
        if nmea_sentence.startswith('$GPVTG'):
            print(f"Processing VTG sentence: {nmea_sentence}")
            # Parsing logic for VTG sentences
            return True
        else:
            return next_handler(nmea_sentence)

    def _gsa_handler(self, nmea_sentence, next_handler):
        if nmea_sentence.startswith('$GPGSA'):
            print(f"Processing GSA sentence: {nmea_sentence}")
            # Parsing logic for GSA sentences
            return True
        else:
            return next_handler(nmea_sentence)

    def _default_handler(self, nmea_sentence):
        print(f"No handler could process the sentence: {nmea_sentence}")
        return False

# Demo if run as main
if __name__ == "__main__":
    parser = NMEA_parser()
    nmea_sentences = [
        '$GPGGA,...',  # GGA sentence
        '$GPRMC,...',  # RMC sentence
        '$GPGLL,...',  # GLL sentence
        '$GPVTG,...',  # VTG sentence
        '$GPGSA,...',  # GSA sentence
        '$GPRTE,...'   # Unhandled sentence type
    ]

    # Process sentences
    for sentence in nmea_sentences:
        print("\nProcessing sentence:")
        parser.handle_nmea_sentence(sentence)
