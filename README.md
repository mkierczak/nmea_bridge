# nmea_bridge

GPS to NMEA bridge for a Cobra Marine VHF radio (MicroPython, Raspberry Pi Pico).

It reads NMEA 0183 from a Waveshare L76B GNSS module, validates each sentence's checksum, rewrites
`$GN…` talker IDs to `$GP…` (recomputing the checksum) because the radio rejects `$GN`, and forwards
every valid sentence of the selected types (default RMC, GGA, GSA, GSV, ZDA, from the `GP`/`GN` talkers) once, `\r\n`-terminated, over RS-485 at 4800 baud. A 1.3" SH1107 OLED shows
time, position, fix, satellites, DOP grades and stats.

## Hardware
- Raspberry Pi Pico W (the Wi-Fi feature needs the W; a plain Pico works with `WIFI_ENABLE = False`)
- Waveshare L76B GNSS module on UART0 (GP0/GP1), 4800 baud by default (`GPS_BAUDRATE`)
- Waveshare 2-CH RS485 module on UART1 (GP4/GP5), 4800 baud, to the radio
- SH1107 128x64 SPI OLED: SCK GP10, MOSI GP11, DC GP8, RST GP12, CS GP9
- Buttons: UP GP15, DOWN GP17 (active low)

All pins and rates are constants at the top of `main.py`.

## Deploy
`make deploy` (uses `mpremote`; it first writes `version.py` from `git describe`, which the System page shows) copies `main.py`, `NMEA.py`, `l76x.py`, `screens.py`, `jamming.py`, `spoofing.py`, `wifi.py`, `wificreds.py`,
`settings.py`, `nav.py`, `menu.py`, `bridge.py`, `ui.py`, `sh1107.py`, `writer.py` and the `roboto14.py` font to the Pico. `main.py` runs on boot.

When `mpy-cross` is installed, `make deploy` runs `make deploy-mpy`, which precompiles the modules and deploys
`.mpy` files instead (less RAM to load them, faster boot); without it the source files are deployed. Before
copying anything it compares the `.mpy` format version of `mpy-cross` with the board's firmware and stops on a
mismatch (the bytecode of `mpy-cross` 1.29 loads on firmware 1.24.1). `make deploy-py` always deploys the
source files. With `DEBUG` on, the free/used heap is printed at boot and every minute (`MEM ...`).
Measured on a Pico W (MicroPython 1.24.1, `make deploy` with `.mpy` modules, heap read after a garbage collection;
the board has 185 KiB of heap): the booted app holds about 125 KiB and leaves about **59 KiB free** (the System page
shows `heap 56k`: it rounds down to 4 KiB). Opening the menu costs about 3.5 KiB, loading the Wi-Fi code about
2.5 KiB and running the access point about 1 KiB more, and drawing every page changes nothing worth mentioning: in the
worst case about **55 KiB stay free**, and the figure is steady while the UI runs. The GPS thread's 4 KiB stack must be one contiguous block, which the heap no
longer offers by the time the modules have loaded, so `main.py` keeps a block back for it from the very start and
frees it just before the thread is started (without that the board stopped at that line with a `MemoryError`).

**Deploying to (or poking at) a board that is running the app.** Two things matter once the GPS has armed
the 5 s hardware watchdog. `mpremote` soft-resets the board on connect; with the GPS thread on the second
core every later flash write then hangs until the watchdog resets the board. And interrupting the app stops
it feeding the watchdog. So `make deploy` / `make deploy-mpy` use `mpremote resume` (no soft reset), first
start a small timer that feeds the watchdog while files are copied, install `main.py` last, and end with a
hard reset that starts the new version. For your own diagnostics use `mpremote connect PORT resume exec`
(a plain `exec` loses the program's variables and can trigger the same hang) and keep the watchdog fed
for sessions longer than 5 s, for example with the timer from the `maintenance` target in the Makefile.

## Configuration
The constants at the top of `main.py` are the **defaults**. Everything you will want to change while
running can be changed in the on-device menu (see below) and is stored in `settings.json` on the board;
only values that differ from the defaults are saved, so changing a default in `main.py` still applies
to settings you never touched. `FORWARD_TYPES` (default RMC, GGA, GSA, GSV, ZDA) selects which sentence
types are forwarded to the radio and over Wi-Fi; the display always uses all parsed sentences.

## Controls and menu

Every page and menu item, with the meaning of each displayed value: [docs/screens.md](docs/screens.md).

![Main page](docs/img/main.png) ![Satellites page](docs/img/satellites.png)
Two keys, classified when released: short (< 1 s) and long (>= 1 s).

| | UP | DOWN |
|---|---|---|
| **Pages** | short: next page; **hold 3 s: Wi-Fi on/off** (a shorter long press does nothing); long on the Anchor / MOB page: drop or raise the anchor / clear the mark | short: previous page; long: back to Main, and **on the Main page: open the menu**; **hold 3 s: man overboard** (marks the position) |
| **Menu** | short: cursor up; long: select / toggle / start editing | short: cursor down; long: back (leaves the menu at the top level) |
| **Editing a value** | short: increase / next; long: confirm | short: decrease / previous; long: cancel (value reverts) |

**Hold both keys for 2 s** to switch between the two page loops. The **main loop** (short presses cycle through it):
**Main**, **Speed** (COG and SOG gauges), **GPS** (satellites used, mean C/N0, the interference-cancellation tag and
the jamming and spoofing probability gauges), **Anchor** (anchor watch with a drag alarm), **MOB** (while a
man-overboard mark exists: distance and bearing back to it) and, while the access point is on, **Wi-Fi**. The **debug
loop** has the details: **Alerts** (the last alerts with time and reason), **Stats** (link statistics),
**Satellites** (per-satellite C/N0 gauges for GPS and BeiDou), **Signal** (jamming detector detail: mean vs baseline
C/N0, reasons in words, module jamming status, AIC), **Spoofing** (the eight indicators as lit tiles, warm-up
progress, alert latch time left), **System** (uptime, free heap, drops, GPS baud found at boot, fix interval,
software version, board ID) and **Debug**. A long DOWN leaves the debug loop, and so does a new `MEDIUM`/`HIGH`
alert or two minutes without a key.

**Man overboard:** hold DOWN for 3 s on any page to mark the position (hold it for 3 s again to lift the mark) (banner, blink, buzzer, and the MOB page with
distance and bearing back to it; the mark is stored in `mob.json` and survives a reboot). **Anchor:** a long UP on the Anchor page drops the anchor at the current position;
the alarm radius is in the menu (Anchor > Radius). Both alarms use the buzzer if you wire one and set `PIN_BUZZER` in
`main.py`.

Menu: **GPS** (baudrate, GNSS mode), **Detection** (jamming and spoofing on/off, spoof action
display/block), **Radio output** (RMC/GGA/GSA/GSV/ZDA on/off), **Display** (contrast, screen-off timer),
**Wi-Fi** (on/off now, new password), **Advanced** (detector thresholds: C/N0 drop, satellite drop,
jamming enter/exit cycles, max speed, time jump, flat-C/N0 limit, altitude step, alert latch minutes,
warm-up fixes), **System** (Log raw, reset all settings to defaults, reboot).
- Most settings apply **immediately** (while editing a number you see the effect; cancelling reverts).
  Settings marked `*` (GPS baudrate, GNSS mode) change the GPS module configuration done at boot: the
  menu shows `*reboot` and a "Reboot now" entry at the top until you reboot.
- The menu closes itself after 60 s without a key press. The screen-off timer (Display menu) switches
  the OLED off after the chosen idle time; the first key press only wakes it, and an active jamming or
  spoofing alert wakes it and keeps it on.
- Corrupt or invalid entries in `settings.json` are ignored; "Reset defaults" deletes the file.
- The menu code is loaded only while the menu is open (RAM).

### Wi-Fi: NMEA over TCP and UDP (Pico W)
The access point is **off at boot**. Hold the **UP button for 3 s** to switch it on or off (the menu is
opened with a long DOWN press on the Main page). The WPA2 network serves the NMEA stream on port `WIFI_PORT` (10110)
as a TCP server (up to `WIFI_MAX_CLIENTS`, slow or dead clients are dropped) and as UDP broadcast to the
AP subnet (`192.168.4.255`). Connect OpenCPN, SignalK, Navionics etc. to `192.168.4.1:10110`. The
short-press cycle has a Wi-Fi screen with state, **SSID, password**, IP and client count.

**Per-device credentials:** with `WIFI_SSID = ''` and `WIFI_PASSWORD = ''` (the defaults) both are
unique to the board and shown on the Wi-Fi screen, even while the AP is off:
- **SSID** `NMEABridge-` plus 4 characters (e.g. `NMEABridge-K7X2`) derived from the board's unique ID
  (`machine.unique_id()`), so it is the same after every reboot, reflash or file deletion and nothing is
  stored. The suffix is only about 20 bits (31^4, about 920,000 combinations): fine for boats within
  radio range, but two boards could in principle share a name; set `WIFI_SSID` to choose another.
- **Password:** 8 random characters (the WPA2 minimum; no look-alike characters, about 40 bits, from the
  Pico's hardware entropy) generated on first boot and saved in `wifi_password.txt` on the board. A shorter
  password is easier to type but weaker against an offline guess of a captured handshake; set a longer
  `WIFI_PASSWORD` in `main.py` if that matters to you. A password made by an older version keeps its
  length until you regenerate it: **Menu > Wi-Fi > New password**, or delete the file
  (`mpremote fs rm :wifi_password.txt`) and reboot.

Setting `WIFI_SSID` or `WIFI_PASSWORD` in `main.py` overrides the generated one (password 8-63
characters; open networks are not supported). The password is stored in plain text on the board and
shown on the OLED, so anyone with physical access to the device can read it.
The forwarded sentence types are the same as for the radio (Radio output menu); `WIFI_FORWARD_TALKERS`
chooses the talker IDs (default GP, GN and BeiDou BD); when `SPOOF_ACTION = 'block'` is active the blocked sentences are not sent over Wi-Fi
either. Everyone who joins the network can read the vessel's position.

Resources (measured, see the Deploy section): the app needs roughly 125 KiB of the 185 KiB heap; the Wi-Fi code is
imported only when first switched on and adds about 3-4 KiB on top of the 59 KiB that are free at boot. Keep the Wi-Fi board and its
antenna away from the GNSS antenna (the 2.4 GHz radio and board noise can lower C/N0; compare the
C/N0 figures on the stats screen with Wi-Fi on and off). Starting the AP can block the main loop for
a second or two, so a few GPS sentences may be dropped (see the `d` figure, dropped sentences, on the stats screen).

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
conditions, and reports the probability of jamming as `OK` / `LOW` / `MEDIUM` / `HIGH` (the main screen shows
a bolt icon with a one-to-three-bar level meter for the last three, a tick while all is well; `MEDIUM` and `HIGH` are alerts with a banner; `?` is shown
while the baseline is still being learned). Details (mean vs baseline C/N0, tracked satellites, reason letters
C/N/F/M) are on the Signal page. NMEA exposes no RF/AGC data, so obstruction, indoor use or
an antenna fault look the same as jamming: treat `MEDIUM`/`HIGH` as "signal degraded, jamming possible".
Thresholds are constants at the top of `jamming.py`; tune them with `tools/replay.py` on a recorded
NMEA log. Set `JAM_DETECT = False` in `main.py` to disable it. At init the module is also asked to
enable Active Interference Cancellation (`$PMTK286,1`); the debug screen shows `AIC+` (acked),
`AIC-` (rejected) or `AIC?` (no reply, probably unsupported on the L76B).

## Spoofing-suspicion indicator
Full algorithm description: [docs/spoofing-detection.md](docs/spoofing-detection.md).

`spoofing.py` reports the probability of spoofing as `OK` / `LOW` / `MEDIUM` / `HIGH` (the main screen shows
a ghost icon with a one-to-three-bar level meter for the last three, an empty ghost while all is well; `MEDIUM` and `HIGH` are alerts with a banner, `HIGH` is held for
about 11 min). It is **heuristic**: the L76B gives NMEA only (no raw measurements, no RAIM, no signal
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

HIGH = any strong indicator or two medium ones within 60 s; MEDIUM = one medium or two weak; LOW = one weak. No
indicator fires during the first 30 fixes after boot. `SPOOF_ACTION` in `main.py` is `'display'`
(default: only show it) or `'block'` (also stop forwarding `SPOOF_BLOCK_TYPES`, default RMC and GGA,
to the radio while the level is HIGH, so the radio shows "no position" instead of a suspect one). Measure the
false-alarm rate on recorded logs (`tools/replay.py`) before using `'block'`.

`GNSS_MODE` selects GPS-only or GPS+BeiDou (the L76B supports no other constellations; BeiDou
appears as `$BD…` sentences, which are not forwarded to the radio: see `FORWARD_TALKERS`). The module's
own jamming detector (`$PMTK838,1`, reports `$PMTKSPF`) also feeds the jamming indicator (reason `M`).
Module replies to the configuration commands (`$PMTK001`: 251 baud, 286 AIC, 353 search mode, 838
jamming detector) are printed when `DEBUG` is on.

## Reliability
The radio path is deliberately separated from everything else (`bridge.py`, tested on a desktop):
- **Forwarding never depends on the display parser.** A sentence with a correct checksum is forwarded
  even if a field of it could not be read for the display (counted in `parse_errors`, not as invalid).
- **Failures are contained.** Detectors, Wi-Fi, logging and statistics each run inside a guard: an
  exception is counted, shown on the REPL, and after repeated failures the part is switched off for 30 s
  and retried. The UI loop is independent of the radio path. Only a persistently failing sentence path
  (20 consecutive unexpected failures) resets the board.
- **Stale positions are not sent.** After a stall (Wi-Fi start, flash write) RMC/GGA sentences that waited
  more than 3 s are dropped instead of being sent late as if they were current.
- **Watchdog.** It is armed by the first *checksum-valid* GPS sentence (line noise never arms it, and a
  bench without a GPS does not reboot-loop) and is fed only while valid sentences keep arriving
  (`GPS_SILENCE_TIMEOUT_MS`, 30 s, in `bridge.py`) and the GPS thread is healthy.
- **GPS thread.** It catches and reports its own exceptions and restarts (backing off 1 s). If it is dead
  before the first sentence the board resets after 5 s. If no valid sentence arrives for 30 s it looks for
  the module again (baud probe plus configuration), at most once a minute: a module that was power-cycled
  falls back to 9600 baud and its default settings.
- **Module configuration is acknowledged.** Each PMTK command is sent, its `$PMTK001` acknowledgement is
  awaited and the command is resent if it is lost. On a real L76B the module silently drops commands that
  arrive while it restarts its engine (after the fix-interval, power-mode, search-mode and EASY
  commands): with the old 100 ms spacing only 4 of the 11 commands took effect, so the fix interval stayed
  at 800 ms and BeiDou was never enabled. Commands that still fail are listed in `gps.config_failed`
  (printed with `DEBUG`).
- **Buttons** are debounced (30 ms); a release without a recorded press is ignored; the Wi-Fi gesture
  (hold UP 3 s) is only a long press inside the menu.
- **Saving settings** on a full or read-only filesystem shows `save failed` instead of crashing; the
  change stays active until the next reboot. Settings and the Wi-Fi password file are written via a
  temporary file and rename.

## Recording logs and replaying them
`DEBUG` is **off** by default (printing to a USB console that is attached but not being read can block the
main loop). To capture a log for tuning the detectors, switch on **Menu > System > Log raw**: every framed
sentence of every talker (including `$BD...`, `$PMTK...` and sentences that fail their checksum) is printed
as `<arrival ms> <sentence>`, before any rewriting. Capture it on the computer:

    mpremote repl | tee log.txt

(if the console cannot keep up, logging switches itself off and `Log raw` shows `off` again). Then replay
it through the same radio-path code the board runs, with the same detectors:

    python3 tools/replay.py log.txt --baud 4800 --gnss gps+bd
    python3 tools/replay.py log.txt --set cn0_drop_db=8 --set max_speed_kn=40 -q

It prints every state change and a summary: time in each state and alarm episodes per hour, which for a
log recorded in normal conditions is the false-alarm rate. `--set` takes the advanced thresholds from the
menu; `--baud`/`--gnss` set the GPS link rate and mode (they determine the GPS-time check's tolerance).
Details in [docs/jamming-detection.md](docs/jamming-detection.md) and
[docs/spoofing-detection.md](docs/spoofing-detection.md).

## Development and tests
The logic is hardware-free and tested on a desktop. Install the tools once and run everything CI runs:

    pip install -r requirements-dev.txt
    make check          # pytest, ruff, documentation link check, mpy-cross compile of all modules

or individually `make test`, `make lint`, `make docs-check`, `make mpy`. `make check-clean` runs `make check` in a
clean copy of the repository files, the way CI sees them (generated files such as `version.py` are absent). `tools/mpy_smoke.py` runs the
same pure logic under real MicroPython (`micropython tools/mpy_smoke.py`, unix port); CI runs it as a
non-blocking job.
