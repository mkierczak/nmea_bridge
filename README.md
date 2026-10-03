# nmea_bridge

GPS to NMEA bridge for a Cobra Marine VHF radio (MicroPython, Raspberry Pi Pico).

It reads NMEA 0183 from a Waveshare L76B GNSS module, validates each sentence's checksum, rewrites
`$GN…` talker IDs to `$GP…` (recomputing the checksum) because the radio rejects `$GN`, and forwards
every valid sentence once, `\r\n`-terminated, over RS-485 at 4800 baud. A 1.3" SH1107 OLED shows
time, position, fix, satellites, DOP grades and stats.

## Hardware
- Raspberry Pi Pico
- Waveshare L76B GNSS module on UART0 (GP0/GP1), 9600 baud
- Waveshare 2-CH RS485 module on UART1 (GP4/GP5), 4800 baud, to the radio
- SH1107 128x64 SPI OLED: SCK GP10, MOSI GP11, DC GP8, RST GP12, CS GP9
- Buttons: UP GP15, DOWN GP17 (active low)

All pins and rates are constants at the top of `main.py`.

## Deploy
Copy `main.py`, `NMEA.py`, `l76x.py`, `sh1107.py`, `writer.py` and the font modules to the Pico
(e.g. with `mpremote cp`). `main.py` runs on boot.

## Buttons
- UP short: next screen (main / stats); UP long: debug screen
- DOWN short: previous screen; DOWN long: main screen

## Watchdog
The watchdog is fed only while GPS sentences keep arriving. If nothing arrives for
`GPS_SILENCE_TIMEOUT` (30 s) the board resets. Without a GPS attached it will therefore reboot
about every 35 s.

## Tests
The parser has no hardware dependency; run on a desktop:

    python3 -m pytest tests
