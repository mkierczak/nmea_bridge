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
`make deploy` (uses `mpremote`) copies `main.py`, `NMEA.py`, `l76x.py`, `screens.py`, `jamming.py`, `sh1107.py`,
`writer.py` and the font modules to the Pico. `main.py` runs on boot.

## Configuration
Constants at the top of `main.py`. `FORWARD_TYPES` selects which sentence types are forwarded to the
radio (default: RMC, GGA, GSA, GSV, ZDA; an empty tuple forwards nothing). The display always uses
all parsed sentences regardless of this list.

## Jamming / signal-degradation indicator
`jamming.py` watches per-satellite C/N0 from GSV and the fix status, learns a baseline of normal
conditions, and shows `OK` / `LOW` / `JAM?` at the top right of the main screen (blank while the
baseline is still being learned). Details (mean vs baseline C/N0, tracked satellites, reason letters
C/N/F) are on the stats and debug screens. NMEA exposes no RF/AGC data, so obstruction, indoor use or
an antenna fault look the same as jamming: treat `JAM?` as "signal degraded, jamming possible".
Thresholds are constants at the top of `jamming.py`; tune them with `tools/replay.py` on a recorded
NMEA log. Set `JAM_DETECT = False` in `main.py` to disable it. At init the module is also asked to
enable Active Interference Cancellation (`$PMTK286,1`); the debug screen shows `AIC+` (acked),
`AIC-` (rejected) or `AIC?` (no reply, probably unsupported on the L76B).

## Buttons
- UP short: next screen (main / stats); UP long: debug screen
- DOWN short: previous screen; DOWN long: main screen

## Watchdog
The watchdog is armed when the first GPS sentence arrives (so there is no reboot loop on the bench
without a GPS) and is fed only while sentences keep arriving. If nothing arrives for
`GPS_SILENCE_TIMEOUT` (30 s) afterwards, the board resets.

## Tests
The parser has no hardware dependency; run on a desktop:

    python3 -m pytest tests
