# Screens and displayed values

The OLED is 128 x 64 pixels with an 8 x 8 font, so a line holds 16 characters and the screen about 7 lines
(Main uses a larger font for the position). This document lists every page, what each value means and where it
comes from. The drawing code is `screens.py`; the menu is `menu.py`.

## Navigation

| Key | On a page | In the menu |
|---|---|---|
| UP short | next page | previous item / increase value |
| DN short | previous page | next item / decrease value |
| UP long (1 s) | nothing | select / confirm |
| DN long (1 s) | back to Main; **on Main: open the menu** | back / cancel / leave the menu |
| UP held 3 s | Wi-Fi access point on/off | treated as a normal long press |

Page order (UP goes down this list and wraps around): **Main, Stats, Satellites, Signal, Spoofing, System,
Debug, Wi-Fi**. The Wi-Fi page exists only when `WIFI_ENABLE` is set. The screen can switch itself off after a
timeout (menu Display > Screen off); the first key press only wakes it, and a jamming or spoofing alert wakes it and
keeps it on. Pages are redrawn when a visible value changes (and at least every 500 ms when something did).

## 1. Main

```
12:34:56   OK   SPF?     <- UTC time, jamming label, spoofing label
-------------------------
N59°12.34                <- latitude  (large font)
E018°03.21               <- longitude (large font)
-------------------------
GPS 3D 9/14        BBB   <- fix, mode, satellites used/in view, DOP letters
```

| Item | Meaning |
|---|---|
| Time | UTC from the last RMC/GGA/ZDA time field, `hh:mm:ss`; `--:--:--` until a time has been received. |
| Jamming label (x = 72) | Blank while the jamming detector is off or still learning (`INIT`); otherwise `OK`, `LOW` (suspected signal degradation) or `JAM?` (strong evidence). See [jamming-detection.md](jamming-detection.md). |
| Spoofing label (right edge) | Blank when `OK` or the detector is off; `SPF?` (suspect) or `SPF!` (alert). If both labels would touch they shrink to `S?` / `S!`. See [spoofing-detection.md](spoofing-detection.md). |
| Latitude / longitude | Hemisphere letter, whole degrees, decimal minutes (2 decimals): `N59°12.34`. Replaced by `NO FIX` when there is no usable position. |
| `NO FIX` | Shown when the last GGA says no fix, no position sentence has arrived yet, or the last position sentence is older than 10 s. |
| Fix | Last GGA fix quality: `NO`, `GPS`, `DGPS` (differential/SBAS) or `?`. |
| Mode | From GSA: `2D`, `3D`, or blank when unknown. |
| `used/view` | Satellites used in the solution (GGA) / satellites in view (sum of the GSV totals of all systems). |
| DOP letters (right) | Three letters for PDOP, HDOP, VDOP, each classified: `A` < 1 (ideal), `B` 1-2 (excellent), `C` 2-5 (good), `D` 5-10 (moderate), `E` 10-20 (fair), `F` >= 20 (poor), `?` unknown. |

## 2. Stats

```
rx58/m d0                <- sentences per minute, dropped sentences
val: 100% GGA            <- share of received sentences that were valid, and the last valid type
inv: 0% RMC              <- invalid ones, and the type last seen invalid
par: 100% GSV            <- parsed ones, and the last parsed type
ign: 0% TXT              <- ignored ones, and the last ignored type
...
CN 41/40 n12/12          <- C/N0 now/baseline, tracked satellites now/baseline
```

The percentages are over a 10-second window of everything the GPS thread framed, refreshed every 10 s.

| Item | Meaning |
|---|---|
| `rx<n>/m` | Sentences received per minute (from the 10 s window; shown as `9999+` if larger). |
| `d<n>` | Sentences dropped because the queue between the GPS thread and the main loop was full (since boot). Should stay 0. |
| `val` | Sentences with a correct checksum and structure. |
| `inv` | Sentences with a bad checksum or broken framing. A few at start-up or after reconnecting are normal; a steady stream means a wrong baud rate or a noisy line. |
| `par` | Valid sentences whose type the bridge decodes (RMC, GGA, GSA, GSV, ZDA, PMTK acks, ...). |
| `ign` | Valid sentences of a type the bridge does not decode (still forwarded if the type is enabled in the menu). |
| type after each percentage | Three-letter type of the most recent sentence in that category. |
| `CN a/b nc/d` | Only while jamming detection is on. `a` mean C/N0 of the tracked satellites now, `b` the learned baseline mean, `c` satellites tracked now, `d` baseline satellite count. |

## 3. Satellites

```
sat  el C/N0
-------------------------
G05  42 ████████ 38      <- id, elevation, bar, C/N0
B21  17 ██████   29
```

The five strongest tracked satellites (C/N0 > 0), strongest first. `no satellites` when none are tracked.

| Column | Meaning |
|---|---|
| `sat` | `G` = GPS, `B` = BeiDou, followed by the PRN/satellite number. |
| `el` | Elevation in degrees above the horizon; `--` when the module does not report it. |
| bar | Length proportional to C/N0 (full bar = 50 dB-Hz or more). |
| number | C/N0 (signal-to-noise density) in dB-Hz. Typical open-sky values are 35-50. |

## 4. Signal (jamming indicator)

```
JAM OK                   <- detector state
no issue                 <- indicators that fire (cn0 sat fix mod)
CN0 41/40 dB             <- mean C/N0 now / baseline
sats 12/12               <- tracked satellites now / baseline
GP41 BD39 dB             <- mean C/N0 per constellation
mod:ok AIC+              <- module's own jamming status, interference cancellation
```

`Jamming: off` when the detector is disabled (menu Detection > Jamming).

| Item | Meaning |
|---|---|
| `JAM <state>` | `INIT` (learning the baseline, no verdict yet), `OK`, `LOW`, `JAM?`. |
| Reason line | Words for the indicators that were true at the last evaluation: `cn0` (mean C/N0 dropped), `sat` (fewer satellites tracked), `fix` (no fix although many satellites are in view), `mod` (module reports interference). `no issue` when none. |
| `CN0 a/b dB` | Current mean C/N0 / learned baseline. |
| `sats a/b` | Tracked satellites now / baseline. |
| `GP.. BD.. dB` | Mean C/N0 of the GPS and BeiDou satellites separately; `-` if that system is not tracked. |
| `mod:` | The L76B's own jamming detector (`$PMTKSPF`): `?` unknown, `ok`, `warn`, `CRIT`. |
| `AIC` | Active interference cancellation: `+` module acknowledged it as on, `-` refused, `?` no answer yet. |

## 5. Spoofing

```
SPF OK                   <- detector state
armed                    <- or: warm-up 12/30
K1 jump                  <- active indicators (up to 3)
S3 power
+1 more
latch 9:41               <- ALERT hold-off countdown
```

`Spoofing: off` when the detector is disabled.

| Item | Meaning |
|---|---|
| `SPF <state>` | `OK`, `SUSPECT` or `ALERT`. |
| `armed` / `warm-up n/N` | After boot the detector only learns for the first N valid fixes (menu Advanced > Warm-up, default 30); no indicator can fire during warm-up. |
| Indicator lines | Code and short name of each indicator currently counting, most severe first: `K1 jump` (position jump), `T1 time` (GPS time step), `K2 speed` (movement not explained by speed), `S1 flat` (uniform C/N0), `C1 GP/BD` (GPS vs BeiDou level offset changed), `K3 alt` (altitude step), `S2 elev` (C/N0 unrelated to elevation), `S3 power` (power rise / satellite change). `+n more` if more than three; `no indicators` when none. |
| `latch m:ss` | Once an ALERT was raised it stays for 10 minutes after the last strong evidence (menu Advanced > Latch min); this is the time left. |

Details of each indicator: [spoofing-detection.md](spoofing-detection.md).

## 6. System

```
up 1h23m45s
heap 87k free
drop0 inv0%
baud 4800 ok
fix1000ms GPS+BD
vb48139d
0123456789abcdef
```

| Line | Meaning |
|---|---|
| `up` | Time since boot (hours, minutes, seconds). |
| `heap` | Free MicroPython memory in KiB, measured after a garbage collection every 3 s and rounded down to 4 KiB. If it keeps falling, report it. |
| `drop` / `inv` | Dropped sentences (capped at `999`) and the share of invalid sentences in the last window (capped at 100). |
| baud line | `baud 4800 ok`: module already at the configured rate. `b4800<9600`: the module was found at 9600 and switched to 4800. `baud 4800 ?`: no module was heard (check wiring/power; the bridge keeps looking). |
| `fix..ms <gnss>` | Configured fix interval and GNSS mode (`GPS` or `GPS+BD`). |
| `v...` | Software version from `git describe` at deploy time (`-dirty` = uncommitted changes, `dev` = no git). |
| last line | First 16 hex digits of the board's unique ID (the Wi-Fi name suffix is derived from it). |

## 7. Debug

```
$GNGGA,123456.00  <- start of the last valid sentence
220326       AIC+ <- date (ddmmyy), interference cancellation status
-------------------------
GPS:8                    <- satellites used, per system (GSA)
SBAS:0
BD:6        S:K1 ..
OTHER:0     why:CN
-------------------------
```

| Item | Meaning |
|---|---|
| Top line | First 16 characters of the last checksum-valid sentence. |
| Date | `ddmmyy` from the last RMC/ZDA. |
| `AIC+/-/?` | As on the Signal page. |
| `GPS`, `SBAS`, `BD`, `OTHER` | Satellites used in the solution, per system, from GSA. |
| `S:` | Reason string of the spoofing detector (first 8 characters), only when it is on. |
| `why:` | Reason letters of the jamming detector (`C` cn0, `N` sats, `F` fix, `M` module), only when it is on. |

## 8. Wi-Fi

```
WiFi: ON sta0
NMEABridge-AB12
PW k4x9mhq2
IP 192.168.4.1
TCP clients 1/4
UP 3s: toggle
```

| Item | Meaning |
|---|---|
| `WiFi: <state>` | `OFF`, `ON sta<n>`, or `ERR` (the access point could not start). Off after every boot. `sta<n>` is the number of phones associated with the access point at the Wi-Fi level, before any TCP connection: if a join attempt fails but this number briefly shows 1, the phone reached the radio and failed later (address or password stage). |
| SSID | Network name: `NMEABridge-` plus four characters derived from the board ID. |
| `PW` | The WPA2 password (8 characters, no look-alike characters such as `0/o` or `1/l`). Generated on first boot and stored; menu Wi-Fi > New password makes a new one. A phone that saved the network with an older password must forget it first. |
| `IP` | The access point's address (`-` while off). Connect clients to this address, TCP port 10110 (or receive UDP broadcasts on that port). |
| `TCP clients n/m` | Connected TCP clients / maximum. |

## Menu

Opened with a long DN press on the Main page; leaves with a long DN press at the top level (or after 60 s without a key,
which also reverts an unconfirmed edit). The title row shows the current submenu and `*reboot` when a changed setting needs a
reboot; items marked `*` after the label are applied only at the next boot. The bottom line shows the key hints, or
`save failed` when the settings file could not be written.

| Submenu | Items |
|---|---|
| GPS | Baudrate*, GNSS mode* |
| Detection | Jamming, Spoofing, Spoof act. (`display` / `block`) |
| Radio output | RMC, GGA, GSA, GSV, ZDA (which sentence types go to the radio) |
| Display | Contrast, Screen off (`never`, `30s`, `60s`, `5m`) |
| Wi-Fi | Wi-Fi now (on/off), New password |
| Advanced | Detector thresholds: CN0 drop, Sats drop%, Jam enter, Jam exit, Max speed, Time jump, Flat C/N0, Alt step, Latch min, Warm-up |
| System | Log raw, Reset defaults, Reboot now |

The advanced thresholds are explained in the two detection documents.
