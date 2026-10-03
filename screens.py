from writer import Writer

SCREEN_MAIN, SCREEN_STATS, SCREEN_DEBUG = 0, 1, 2


def draw(oled, font_large, screen, parser, stats, dropped, no_fix):
    """Render one screen into the frame buffer (caller calls oled.show())."""
    oled.fill(0)
    if screen == SCREEN_MAIN:
        oled.text(parser.get_time_string() + ' UTC', 30, 3, 1)
        oled.hline(0, 14, 128, 1)
        if no_fix:
            oled.text('NO FIX', 40, 28, 1)
        else:
            Writer.set_textpos(oled, 17, 0)
            font_large.printstring(parser.get_lat_string())
            Writer.set_textpos(oled, 32, 0)
            font_large.printstring(parser.get_lon_string())
        oled.hline(0, 48, 128, 1)
        oled.text(parser.fix_type + ' ' + parser.mode + ' ' +
                  str(parser.birds_in_use) + '/' + str(parser.birds_in_view), 0, 54, 1)
        oled.text(parser.get_dop_string(type='PDOP') + parser.get_dop_string(type='HDOP') +
                  parser.get_dop_string(type='VDOP'), 104, 54, 1)
    elif screen == SCREEN_STATS:
        rcv = stats['rcv'] or 1  # avoid division by zero in empty windows
        oled.text("rcv: " + str(round(stats['rcvpm'])) + "/min drop:" + str(dropped), 0, 0, 1)
        for row, (key, last) in enumerate((('val', parser.sentence_last_valid_type),
                                           ('inv', parser.sentence_last_invalid_type),
                                           ('par', parser.sentence_last_parsed_type),
                                           ('ign', parser.sentence_last_ignored_type)), 1):
            oled.text(key + ": " + str(round(stats[key] / rcv * 100)) + '% ' + last, 0, row * 12, 1)
    elif screen == SCREEN_DEBUG:
        oled.text(parser.last_valid_sentence.strip()[:16], 0, 0, 1)
        oled.text(parser.date, 0, 10, 1)
        oled.hline(0, 20, 128, 1)
        oled.text('GPS:' + str(parser.birds_GPS), 0, 24, 1)
        oled.text('SBAS:' + str(parser.birds_SBAS), 0, 34, 1)
        oled.text('GLONASS:' + str(parser.birds_GLONASS), 0, 44, 1)
        oled.text('OTHER:' + str(parser.birds_OTHER), 0, 54, 1)
        oled.hline(0, 63, 128, 1)
