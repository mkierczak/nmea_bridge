# nmea_bridge

GPS to NMEA bridge for a Cobra Marine VHF radio (MicroPython, Raspberry Pi Pico).

It reads NMEA 0183 from a Waveshare L76B GNSS module, validates each sentence's checksum, rewrites
`$GN…` talker IDs to `$GP…` (recomputing the checksum) because the radio rejects `$GN`, and forwards
every valid sentence once, `\r\n`-terminated, over RS-485 at 4800 baud. A 1.3" SH1107 OLED shows
time, position, fix, satellites, DOP grades and stats.

## Hardware
- Raspberry Pi Pico W (the Wi-Fi feature needs the W; a plain Pico works with `WIFI_ENABLE = False`)
- Waveshare L76B GNSS module on UART0 (GP0/GP1), 4800 baud by default (`GPS_BAUDRATE`)
- Waveshare 2-CH RS485 module on UART1 (GP4/GP5), 4800 baud, to the radio
- SH1107 128x64 SPI OLED: SCK GP10, MOSI GP11, DC GP8, RST GP12, CS GP9
- Buttons: UP GP15, DOWN GP17 (active low)

All pins and rates are constants at the top of `main.py`.

## Deploy
`make deploy` (uses `mpremote`) copies `main.py`, `NMEA.py`, `l76x.py`, `screens.py`, `jamming.py`, `spoofing.py`, `wifi.py`, `wificreds.py`,
`settings.py`, `nav.py`, `menu.py`, `sh1107.py`, `writer.py` and the font modules to the Pico. `main.py` runs on boot.

`make deploy-mpy` precompiles the modules with `mpy-cross` and deploys `.mpy` files instead (less RAM to
load them, faster boot; `mpy-cross` must match the firmware version). With `DEBUG` on, the free/used heap
is printed at boot and every minute (`MEM ...`): check it before and after enabling Wi-Fi.

## Configuration
The constants at the top of `main.py` are the **defaults**. Everything you will want to change while
running can be changed in the on-device menu (see below) and is stored in `settings.json` on the board;
only values that differ from the defaults are saved, so changing a default in `main.py` still applies
to settings you never touched. `FORWARD_TYPES` (default RMC, GGA, GSA, GSV, ZDA) selects which sentence
types are forwarded to the radio and over Wi-Fi; the display always uses all parsed sentences.

## Controls and menu
Two keys, classified when released: short (< 1 s) and long (>= 1 s).

| | UP | DOWN |
|---|---|---|
| **Pages** | short: next page; long: open the menu; **hold 3 s: Wi-Fi on/off** | short: previous page; long: back to Main |
| **Menu** | short: cursor up; long: select / toggle / start editing | short: cursor down; long: back (leaves the menu at the top level) |
| **Editing a value** | short: increase / next; long: confirm | short: decrease / previous; long: cancel (value reverts) |

Pages (short presses cycle through them): **Main**, **Stats** (link statistics), **Satellites**
(per-satellite C/N0 bars for GPS and BeiDou), **Signal** (jamming detector detail: mean vs baseline
C/N0, reasons in words, module jamming status, AIC), **Spoofing** (state, warm-up progress, active
indicators by name, alert latch time left), **System** (uptime, free heap, drops, GPS baud found at
boot, fix interval, board ID), **Debug**, **Wi-Fi**.

Menu: **GPS** (baudrate, GNSS mode), **Detection** (jamming and spoofing on/off, spoof action
display/block), **Radio output** (RMC/GGA/GSA/GSV/ZDA on/off), **Display** (contrast, screen-off timer),
**Wi-Fi** (on/off now, new password), **Advanced** (detector thresholds: C/N0 drop, satellite drop,
jamming enter/exit cycles, max speed, time jump, flat-C/N0 limit, altitude step, alert latch minutes,
warm-up fixes), **System** (reset all settings to defaults, reboot).
- Most settings apply **immediately** (while editing a number you see the effect; cancelling reverts).
  Settings marked `*` (GPS baudrate, GNSS mode) change the GPS module configuration done at boot: the
  menu shows `*reboot` and a "Reboot now" entry at the top until you reboot.
- The menu closes itself after 60 s without a key press. The screen-off timer (Display menu) switches
  the OLED off after the chosen idle time; the first key press only wakes it, and an active jamming or
  spoofing alert wakes it and keeps it on.
- Corrupt or invalid entries in `settings.json` are ignored; "Reset defaults" deletes the file.
- The menu code is loaded only while the menu is open (RAM).

### Wi-Fi: NMEA over TCP and UDP (Pico W)
The access point is **off at boot**. Hold the **UP button for 3 s** to switch it on or off (a 1-3 s
press opens the menu). The WPA2 network serves the NMEA stream on port `WIFI_PORT` (10110)
as a TCP server (up to `WIFI_MAX_CLIENTS`, slow or dead clients are dropped) and as UDP broadcast to the
AP subnet (`192.168.4.255`). Connect OpenCPN, SignalK, Navionics etc. to `192.168.4.1:10110`. The
short-press cycle has a Wi-Fi screen with state, **SSID, password**, IP and client count.

**Per-device credentials:** with `WIFI_SSID = ''` and `WIFI_PASSWORD = ''` (the defaults) both are
unique to the board and shown on the Wi-Fi screen, even while the AP is off:
- **SSID** `NMEABridge-` plus 4 characters (e.g. `NMEABridge-K7X2`) derived from the board's unique ID
  (`machine.unique_id()`), so it is the same after every reboot, reflash or file deletion and nothing is
  stored. The suffix is only about 20 bits (31^4, about 920,000 combinations): fine for boats within
  radio range, but two boards could in principle share a name; set `WIFI_SSID` to choose another.
- **Password:** 12 random characters (no look-alike characters, from the Pico's hardware entropy)
  generated on first boot and saved in `wifi_password.txt` on the board. Delete the file
  (`mpremote fs rm :wifi_password.txt`) and reboot for a new one.

Setting `WIFI_SSID` or `WIFI_PASSWORD` in `main.py` overrides the generated one (password 8-63
characters; open networks are not supported). The password is stored in plain text on the board and
shown on the OLED, so anyone with physical access to the device can read it.
The forwarded sentence types are the same as for the radio (Radio output menu); `WIFI_FORWARD_TALKERS`
chooses the talker IDs (default GP, GN and BeiDou BD); when `SPOOF_ACTION = 'block'` is active the blocked sentences are not sent over Wi-Fi
either. Everyone who joins the network can read the vessel's position.

Resources (estimates, measure on your board): the app needs roughly 70-100 KB of heap; the Wi-Fi code is
imported only when first switched on and adds on the order of 10-30 KB. Keep the Wi-Fi board and its
antenna away from the GNSS antenna (the 2.4 GHz radio and board noise can lower C/N0; compare the
C/N0 figures on the stats screen with Wi-Fi on and off). Starting the AP can block the main loop for
a second or two, so a few GPS sentences may be dropped (see `drop:` on the stats screen).

### GPS baudrate
`GPS_BAUDRATE` (default 4800; allowed 4800, 9600, 14400, 19200, 38400, 57600, 115200). At boot the
firmware listens at that rate; if the module is silent or garbled it probes the other rates, sends
`$PMTK251,<rate>` to switch the module and verifies, so it works whether the module is still at its
old rate (e.g. first boot after changing the setting) or already at the new one. Probing can take up
to about 10 s; if the module is never heard the UART stays at `GPS_BAUDRATE`. (Whether the module
keeps the rate across its own power cycle is not documented, which is why this runs on every boot.)

At <= 4800 baud the fix interval is set to 1 s (`FIX_INTERVAL_MS`, otherwise 800 ms). 4800 baud is
only about 480 B/s: GPS-only output fits (worst fix cycle roughly 460 B), but with `GNSS_MODE =
'GPS+BD'` the cycle that carries GSA/GSV (every 5th fix) is roughly 660 B and exceeds one second of
line time, so GSV/GSA may be delayed or dropped by the module (its behaviour when overloaded is not
documented). With `DEBUG` on, the estimated load is printed at boot with a warning; if you see
truncated sentences, raise `GPS_BAUDRATE` or use `GNSS_MODE = 'GPS'`. The spoofing time check (T1)
widens its tolerance by the worst-case line time of a cycle so this delay does not raise false alarms.

## Jamming / signal-degradation indicator
Full algorithm description: [docs/jamming-detection.md](docs/jamming-detection.md).

`jamming.py` watches per-satellite C/N0 from GSV and the fix status, learns a baseline of normal
conditions, and shows `OK` / `LOW` / `JAM?` at the top right of the main screen (blank while the
baseline is still being learned). Details (mean vs baseline C/N0, tracked satellites, reason letters
C/N/F/M) are on the Signal page. NMEA exposes no RF/AGC data, so obstruction, indoor use or
an antenna fault look the same as jamming: treat `JAM?` as "signal degraded, jamming possible".
Thresholds are constants at the top of `jamming.py`; tune them with `tools/replay.py` on a recorded
NMEA log. Set `JAM_DETECT = False` in `main.py` to disable it. At init the module is also asked to
enable Active Interference Cancellation (`$PMTK286,1`); the debug screen shows `AIC+` (acked),
`AIC-` (rejected) or `AIC?` (no reply, probably unsupported on the L76B).

## Spoofing-suspicion indicator
Full algorithm description: [docs/spoofing-detection.md](docs/spoofing-detection.md).

`spoofing.py` raises `SPF?` (suspect) or `SPF!` (alert, held for about 11 min) at the top right of the
main screen. It is **heuristic**: the L76B gives NMEA only (no raw measurements, no RAIM, no signal
authentication), so a careful spoofer (smooth drift, consistent time, realistic power) will pass.
Treat it as "spoofing suspected", never as proof or protection. Indicators (letters on the debug
screen):

| | strength | meaning |
|---|---|---|
| K1 | strong | position jump that persists (implied speed above 60 kn; a single glitch is ignored) |
| T1 | strong | GPS time steps relative to the Pico's own clock, or goes backwards |
| K2 | medium | position change over ~10 s disagrees with the reported speed |
| S1 | medium | satellites of one constellation have suspiciously uniform C/N0 |
| C1 | medium | GPS vs BeiDou mean C/N0 offset moved away from its learned baseline |
| K3 | weak | altitude step between fixes |
| S2 | weak | C/N0 not correlated with elevation |
| S3 | weak | sudden C/N0 rise or abrupt change of the tracked satellites |

ALERT = any strong indicator or two medium ones within 60 s; SUSPECT = one medium or two weak. No
indicator fires during the first 30 fixes after boot. `SPOOF_ACTION` in `main.py` is `'display'`
(default: only show it) or `'block'` (also stop forwarding `SPOOF_BLOCK_TYPES`, default RMC and GGA,
to the radio during an ALERT, so the radio shows "no position" instead of a suspect one). Measure the
false-alarm rate on recorded logs (`tools/replay.py`) before using `'block'`.

`GNSS_MODE` selects GPS-only or GPS+BeiDou (the L76B supports no other constellations; BeiDou
appears as `$BD…` sentences, which are not forwarded to the radio: see `FORWARD_TALKERS`). The module's
own jamming detector (`$PMTK838,1`, reports `$PMTKSPF`) also feeds the jamming indicator (reason `M`).
Module replies to the configuration commands (`$PMTK001`: 251 baud, 286 AIC, 353 search mode, 838
jamming detector) are printed when `DEBUG` is on.

## Watchdog
The watchdog is armed when the first GPS sentence arrives (so there is no reboot loop on the bench
without a GPS) and is fed only while sentences keep arriving. If nothing arrives for
`GPS_SILENCE_TIMEOUT` (30 s) afterwards, the board resets.

## Tests
The parser has no hardware dependency; run on a desktop:

    python3 -m pytest tests
