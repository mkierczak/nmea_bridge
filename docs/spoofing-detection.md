# Spoofing-suspicion detection

Implementation: [`spoofing.py`](../spoofing.py) (class `SpoofDetector`). Inputs come from
[`NMEA.py`](../NMEA.py) (`Parser`), wiring and the optional "block" action are in
[`bridge.py`](../bridge.py) (set up by [`main.py`](../main.py)), the user interface is in [`screens.py`](../screens.py), and thresholds can be
changed in the menu (see [Parameters](#8-parameters)). See also
[jamming detection](jamming-detection.md).

## Contents
1. [Purpose and what the result means](#1-purpose-and-what-the-result-means)
2. [Background and threat model](#2-background-and-threat-model)
3. [Inputs](#3-inputs)
4. [Architecture at a glance](#4-architecture-at-a-glance)
5. [The indicators in detail](#5-the-indicators-in-detail)
6. [From indicators to a state](#6-from-indicators-to-a-state)
7. [Alert action: display or block](#7-alert-action-display-or-block)
8. [Parameters](#8-parameters)
9. [Detection latency](#9-detection-latency)
10. [Worked examples](#10-worked-examples)
11. [Where you see it](#11-where-you-see-it)
12. [False positives and what the detector cannot catch](#12-false-positives-and-what-the-detector-cannot-catch)
13. [Known limitations and implementation notes](#13-known-limitations-and-implementation-notes)
14. [Tuning and validation](#14-tuning-and-validation)
15. [Tests](#15-tests)
16. [Code map](#16-code-map)
17. [References](#17-references)

---

## 1. Purpose and what the result means

GNSS **spoofing** means broadcasting counterfeit satellite signals so that a receiver computes a wrong
position or time. For this device the consequence is concrete: the bridge hands the receiver's
position to a VHF radio (and to chartplotters over Wi-Fi), and a radio uses that position, for
example, in a DSC distress call.

The detector looks for **inconsistencies** that a genuine receiver on a real vessel would not show:
positions that jump, a GPS clock that disagrees with the board's own clock, movement that does not
match the reported speed, and signal-strength patterns that look more like one transmitter than a
sky full of satellites. It reports the **probability** of spoofing in four levels; `MEDIUM` and `HIGH`
are alerts (banner, blink, the display stays on):

| Main page | Level | Meaning |
|---|---|---|
| *(nothing)* | `OK` | no relevant indicator in the last 60 s |
| ghost, 1 bar | `LOW` | a single weak indicator: worth knowing, not an alert |
| ghost, 2 bars | `MEDIUM` | **alert**: some evidence of inconsistency (a single medium indicator, or two different weak ones); clears when the evidence is older than 60 s |
| ghost, 3 bars | `HIGH` | **alert**: strong evidence (a strong indicator, or two different medium ones); stays up for about 11 min after the last such evidence (60 s evidence window + 10 min latch, see section 6) |

**Important.** This is a heuristic *suspicion* indicator. The L76B gives NMEA sentences only: no raw
pseudoranges, no RAIM integrity monitoring and no signal authentication (such as Galileo OSNMA, which
this module does not support). A competent spoofer that is careful about time, power and trajectory
(smooth drift, consistent speed, plausible signal levels, a spoofer that is physically close and
controlling all satellites) will **pass** these checks. Treat a raised flag as "look at the chart
and your other instruments", and a clear display as "no obvious inconsistency", never as proof of
integrity.

## 2. Background and threat model

Civil GNSS signals are public and unauthenticated, so anyone with a signal generator can produce
signals a receiver will accept. Interference of this kind has been reported in several sea areas.
Attack styles, roughly from easiest to hardest to detect:

| Attack | What it does | How this detector can notice |
|---|---|---|
| **Simulator / replay with a position jump** | Broadcasts a consistent but false scenario at higher power than the real signals; the receiver locks onto it and the position jumps to the false location (or the time steps). | K1 (persistent jump), T1 (time step), K3 (altitude step), S3 (power rise / satellites change), S1 (uniform power). |
| **Spoof with inconsistent dynamics** | Position changes but speed/Doppler-derived velocity or the receiver clock behaves differently. | K2 (movement vs speed), T1. |
| **Single-transmitter signature** | All counterfeit satellites come from one antenna: similar power for every PRN, and power unrelated to the elevation the receiver computes for each satellite. | S1 (uniform C/N0), S2 (no C/N0-vs-elevation correlation). |
| **Partial spoofing (one constellation only)** | Only GPS is faked; BeiDou stays genuine, or the reverse. | C1 (GPS vs BeiDou level offset shifts), also K1/T1 when the solution mixes both. |
| **Careful "drag-off"** | Starts matched to the true position and the true signals, then moves the position slowly and smoothly below plausible-speed limits with realistic power. | Largely **not detectable** here (see section 12). |

Physical intuition behind the signal-statistics indicators (S1, S2, S3, C1): real satellites are at
very different distances and elevations, so their received power varies by several dB; low
satellites are typically weaker because of antenna gain pattern, longer atmospheric path and
multipath. A single spoofing antenna tends to produce **flat** power across the fake satellites, a
power level unrelated to elevation, and, to win lock, usually **more** power than the genuine
signals, which shows up as a sudden rise. These patterns are well known but not universal, which is why
the statistical checks are *medium* or *weak* evidence, never strong.

## 3. Inputs

All come from valid NMEA sentences parsed by `NMEA.Parser`. Only sentences with a correct checksum
are used.

| Quantity | Sentence / field | Parser attribute | Used by |
|---|---|---|---|
| Position | RMC lat/lon, **status `A` only** | `lat_u`, `lon_u` (signed integers in 1e-4 arc-minutes, exact) | K1, K2 |
| GPS UTC time and date | RMC time (`hhmmss.sss`) and date (`ddmmyy`) | `utc_days` (days since 1970-01-01), `utc_ms` (ms of day) | K1/K2 (elapsed time), T1 |
| Local arrival time of the RMC | stamped in the GPS reader thread when the line ends | `rx_ms` (`ticks_ms`) | T1 |
| Speed over ground | RMC field 7 (knots) | `sog_kn` | K2 |
| Course over ground | RMC field 8 | `cog_deg` (parsed, **not used** by any check) | - |
| Altitude | GGA field 9 (only when fix > 0) | `alt_m`, `alt_version` | K3 |
| Per-satellite PRN, elevation, C/N0 | GSV | `sats_by_talker[talker] = [(prn, elevation or None, cn0)]`, `cn0_version` | S1, S2, S3, C1 |
| Fix counter | each valid RMC with status `A` and parseable position/time/date | `fix_count` | drives `_on_fix` |

If an RMC is valid but lacks parseable position, time or date fields, the numeric fix data are left
untouched and `fix_count` does not advance, so the detector simply sees nothing from that sentence
(the sentence is still forwarded to the radio; extra fields never invalidate a sentence).

**Position encoding and distance.** NMEA gives `ddmm.mmmm` (degrees and minutes). The parser converts
it exactly to an integer count of 1e-4 arc-minutes: 1 unit = 1e-4 min x 1852 m/min = **0.1852 m**.
Integer arithmetic avoids the rounding of the Pico's 32-bit floats. Distance between two fixes uses a
flat-earth approximation:

```
dlat = (lat2 - lat1) * 0.1852                       [m]
dlon = (lon2 - lon1) * 0.1852 * cos(mean latitude)  [m]
distance = sqrt(dlat^2 + dlon^2)
```

adequate for the tens of kilometres these checks cover (the longest compared gap is 10 min at
60 kn, about 18 km). The longitude difference is wrapped to +/-180 degrees, so a vessel crossing the
antimeridian is not mistaken for one that jumped across the planet.

## 4. Architecture at a glance

```
GPS thread (core 1)              main loop (core 0)
 reads UART, builds lines,        takes sentences from the queue, parser.parse_sentence()
 stamps rx_ms at end of line  ->  for each valid RMC / GGA:        spoof.evaluate(now)
 pushes (rx_ms, line)             then forwards (or blocks, section 7) the sentence
                                  once per second also: spoof.evaluate(now)  (expires old evidence)

evaluate(now):
   new RMC fix?        -> _on_fix      : T1, K1, K2   (kinematics, once per fix)
   new GGA altitude?   -> _on_altitude : K3
   new GSV cycle settled? -> _on_gsv   : S1, S2, S3, C1  (signal statistics, once per cycle)
   _update_state(now)  -> prune evidence older than WINDOW_MS, apply decision rule, latch
```

Each indicator, when it fires and the detector is **armed**, records `events[code] = now`. The state
is then derived from the set of codes younger than 60 s.

**Warm-up (armed).** Indicators are ignored until `WARMUP_FIXES` (30) valid fixes have been seen
since boot (about 30 s at 1 Hz). A receiver's first fixes after a cold start can be coarse and then
jump as the solution converges; flagging that would be a false alarm. The statistical baselines
(S3, C1) do **not** learn during warm-up either: a receiver that is still acquiring satellites
would otherwise set the baseline (the first samples are often unrepresentative).

**One evaluation per settled GSV cycle.** `_on_gsv` runs only once a cycle has *settled*: a GSV cycle
counts as finished when no constellation completed another one for `GSV_SETTLE_MS` (800 ms). With GPS+BeiDou
both constellations are therefore evaluated together, once per cycle, on complete data (the earlier
behaviour was one evaluation per constellation on half-updated data). The parser also discards a GSV cycle
with a lost or out-of-order message and drops the data of a constellation that has been silent for
20 s (`TALKER_TTL_MS`), so a partial or dead constellation never reaches the detector.

## 5. The indicators in detail

Strength: **strong** (alone enough for `HIGH`), **medium** (two different ones give `HIGH`, one gives
`MEDIUM`), **weak** (one gives `LOW`, two different ones give `MEDIUM`; never `HIGH` on their own).

### K1 - persistent position jump (strong)

*Idea.* A vessel cannot teleport. Between consecutive fixes the distance travelled is bounded by the
maximum plausible speed. A spoofer that moves the solution to another place violates that bound.

*Rule.* For two positions `a` (earlier) and `b` with GPS time difference `dt` (seconds, from RMC time and
date; fixes with `dt <= 0` or `dt > GAP_MAX_MS` = 10 min are never compared):

```
allowed = MAX_SPEED_KN / 1.943844 * dt + JUMP_MARGIN_M      # metres
jump    = distance(a, b) > allowed
```

Default: 60 kn = 30.9 m/s plus a 30 m allowance for position noise, so about 55 m between 0.8 s fixes,
61 m between 1 s fixes, 184 m for a 5 s gap.

*Confirmation (glitch rejection).* A single outlier (multipath, a bad fix) must not raise a strong
alarm, so a jump is accepted only if the new position **persists**:

```
trusted   = last accepted position          candidate = position right after a suspected jump

no candidate:   if jump(trusted -> cur):  candidate = cur          (wait for the next fix)
                else:                     trusted   = cur
candidate set:  if not jump(candidate -> cur):   FLAG K1   (stayed at the new place)
                elif not jump(trusted -> cur):   nothing   (returned: it was a glitch)
                else:                            FLAG K1   (erratic jumping)
                trusted = cur; candidate = None
```

After a flagged jump `trusted` becomes the new position, so the detector does not keep flagging every
fix; the alert is kept up by the latch (section 6). If the spoofing later stops and the position jumps
back, that is another jump and flags K1 again. While a candidate is pending, K2 is not evaluated. The
K2 window that is running when the jump happens still starts at the old position, so after about
10 s K2 normally fires as well (observed in simulation as `K1K2`), which adds supporting evidence.

*Blind spots.* Gaps longer than `GAP_MAX_MS` are not compared (a spoof that begins after a long
signal outage is invisible to K1). Jumps smaller than the allowance, and any movement slower than the
speed limit, pass.

### T1 - GPS time against the board's own clock (strong)

*Idea.* The Pico has an independent oscillator. Two consecutive RMC sentences are about one fix
interval apart both by GPS time and by the Pico's `ticks_ms`. A time-spoofing attack (or a
receiver clock step) makes the GPS interval disagree with the local one.

*Rule.* On every fix, with `dt_gps` from RMC time/date and `dt_local = ticks_diff(rx_ms, previous rx_ms)`:

```
flag T1 if   dt_gps < 0                                         (time went backwards)
          or ( 0 <= dt_local <= TIME_CHECK_MAX_MS (5 s)
               and |dt_gps - dt_local| > time_tolerance )
```

*Why consecutive fixes, not absolute offset.* Comparing consecutive intervals cancels the fixed
offset and the board crystal's slow drift (tens of ppm = a few ms per minute); only a real step (or
large jitter) shows up. Gaps longer than 5 s are skipped because a receiver legitimately re-syncs
its clock after an outage.

*Tolerance and the GPS link rate.* `rx_ms` is taken when the line has been *received*, so it includes
the serial transmission time. At low baud rates the first sentence of a cycle can be delayed behind
the large GSA/GSV cycle that was still being sent. To avoid false alarms, `time_tolerance` is
**`TIME_JUMP_MS` (500 ms) plus the worst-case line time of the largest fix cycle** (`l76x.nmea_burst_ms`):

| GPS baud | GPS only | GPS + BeiDou |
|---|---|---|
| 4800 | 958 ms burst, tolerance 1458 ms | 1375 ms burst, tolerance 1875 ms |
| 9600 | 479 ms, tolerance 979 ms | 687 ms, tolerance 1187 ms |
| 38400 | 119 ms, tolerance 619 ms | 171 ms, tolerance 671 ms |

(These use the size estimates in `l76x.py`; the tolerance is recomputed when `Time jump` is changed in
the menu.) Time steps smaller than the tolerance are therefore invisible.

### K2 - movement versus reported speed (medium)

*Idea.* The receiver reports a speed (SOG) that it derives largely from carrier/Doppler measurements,
independently of the position changes. A genuine track makes the distance covered over a window match the
speed; a spoofer that shifts the position without a consistent velocity does not.

*Rule.* Over a window of at least `K2_WINDOW_MS` (10 s) of trusted fixes, compare the speed implied by
the position change with the mean of the reported SOG values sampled in that window:

```
implied = distance(anchor, now) / elapsed * 1.943844            [kn]
sog     = mean of RMC speeds in the window                      [kn]
flag K2 if  implied - sog > max(K2_ABS_KN (3 kn), K2_REL (0.5) * max(implied, sog))
```

Only movement that the reported speed does **not explain** is flagged (implied above reported). The
opposite case, implied below reported, is normal: when a boat circles, turns or zig-zags the straight
line between the window's endpoints is shorter than the distance sailed, so the chord-based speed is lower
than SOG. The signature of a position spoof (position moves while the speed stays low) is
kept. The window is skipped if it spans more than 3x the window (30 s, a fix gap) and restarts after each
evaluation. The absolute (3 kn) and relative (50 %) conditions together keep GNSS noise from triggering it.
`cog_deg` is not used (direction is not compared).

### K3 - altitude step (weak)

*Idea.* A vessel stays within a few metres of sea level; GNSS altitude jumps of tens of metres between
consecutive GGA sentences mean a changed solution (or poor geometry).

*Rule.* `|alt - previous alt| > ALT_STEP_M` (30 m). Only GGA sentences with a fix contribute. It is
weak because vertical error is the least accurate GNSS dimension.

### S1 - suspiciously uniform signal strength (medium)

*Idea.* A single transmitter produces fake satellites with nearly the same power. Real satellites differ
by several dB.

*Rule.* For **each constellation separately** (talker `GP`, `BD`, ...), take the tracked satellites
(C/N0 > 0) from its last complete GSV cycle. If there are at least `UNIFORM_MIN_SATS` (6) and the
**population standard deviation** of their C/N0 is below `UNIFORM_STD_DB` (1.5 dB-Hz), flag S1.
Example: C/N0 `[40, 40, 41, 40, 40, 41, 40, 40]` has a standard deviation of 0.43 dB-Hz. Healthy
open-sky tracking typically spreads over several dB (`[48, 44, 41, 38, 35, 31, 28, 25]` has about 7.5).

### S2 - C/N0 not correlated with elevation (weak)

*Idea.* In reality low satellites are usually weaker than high ones, so C/N0 and elevation are
positively correlated. For fake satellites from one antenna the power need not follow the elevation the
receiver computes for them. (The elevations reported in GSV are computed by the receiver from its own
position and the broadcast orbits, so they describe the *spoofed* geometry.)

*Rule.* For each constellation with at least `ELEV_MIN_SATS` (6) tracked satellites that have an
elevation value, compute the Pearson correlation between elevation and C/N0; the readings of the
constellations are averaged per GSV cycle and smoothed (`ELEV_ALPHA`, 0.15). Once `ELEV_CYCLES` (12)
readings have been taken, S2 is flagged on every cycle in which the smoothed value is **<=
`ELEV_CORR_MAX` (-0.2)**. Weak because many real installations (marine multipath, antenna patterns,
few satellites) show a poor correlation at times; the smoothing keeps such spells from flagging.

### S3 - sudden power rise or abrupt satellite-set change (weak)

*Idea.* To take over a lock a spoofer typically needs more power than the genuine signals, so the mean
C/N0 jumps; and the set of satellites being tracked can change abruptly.

*Rule (two conditions, either flags S3).*
1. **Power rise.** The mean C/N0 of all tracked satellites (all constellations) exceeds a slowly
   adapting baseline by more than `CN0_RISE_DB` (8 dB). The baseline is a running mean for the first
   `BASELINE_MIN_SAMPLES` (5) cycles and an EMA (`BASELINE_ALPHA` 0.05) afterwards, learned only
   once the detector is armed. While S3 fires the flagged samples are not learned (no baseline
   chasing), but after `REBASE_AFTER` (10) consecutive flagged cycles the new level is accepted as
   normal and the baseline jumps to it (otherwise a genuine, lasting change, such as the
   end of a cold-start ramp, would leave S3 firing for the rest of the run). The comparison is only active
   after `BASELINE_MIN_SAMPLES` samples.
2. **Set change.** With at least `JACCARD_MIN_SATS` (6) tracked satellites in both the previous and the
   current evaluation, the Jaccard similarity of the two sets `{(talker, PRN)}` is below `JACCARD_MIN`
   (0.5): `|A intersect B| / |A union B|`. Satellites rise and set gradually, so consecutive
   evaluations normally overlap almost completely.

### C1 - GPS versus BeiDou offset shift (medium)

*Idea.* GPS and BeiDou use different signals and frequencies (L1 C/A at 1575.42 MHz and B1I at
1561.098 MHz). A spoofer that only forges GPS leaves BeiDou genuine, so the relationship between the
two constellations' signal levels changes. Their *absolute* levels depend on the antenna and the sky,
so the detector uses the **difference of the means** relative to a learned baseline.

*Rule.* With at least `CROSS_MIN_SATS` (3) tracked satellites from both groups (`GP`/`GN` and `BD`/`GB`),
`offset = mean(GPS C/N0) - mean(BeiDou C/N0)`. After `BASELINE_MIN_SAMPLES` samples, flag C1 when
`|offset - baseline offset| > CROSS_SHIFT_DB` (8 dB). The baseline learns exactly like the S3 baseline
(running mean, then EMA, only when armed; flagged samples are not learned; rebased after `REBASE_AFTER`
consecutive flagged cycles). Requires `GNSS_MODE = GPS+BD`; with GPS-only there is nothing to compare.

*Why only statistics and not position.* The L76B outputs one combined position solution; separate
GPS-only and BeiDou-only positions are not available over NMEA. A time-sliced approach that switches
the module between single-constellation modes was considered and rejected: it interrupts the fix, and
BeiDou-only mode is not documented for this module.

## 6. From indicators to a state

```
events = codes seen in the last WINDOW_MS (60 s)

strong = events that are K1 or T1
medium = distinct events among K2, S1, C1
weak   = distinct events among K3, S2, S3

if strong or len(medium) >= 2:                     state = HIGH    (and remember the time + reason)
elif a HIGH was raised < LATCH_MS (10 min) ago:    state = HIGH    (latched, shows the remembered reason)
elif medium or len(weak) >= 2:                     state = MEDIUM
elif weak:                                         state = LOW
else:                                              state = OK      (clears the latch)
```

* Different codes are counted once each: repeated S1 flags do not add up to "two mediums".
* The **latch** keeps a `HIGH` alert visible after the evidence has gone. Every evaluation at which the HIGH
  condition holds restarts the 10-minute timer, and the condition holds for as long as the evidence is
  younger than `WINDOW_MS` (60 s). In practice an alert therefore lasts **about 11 minutes** after the
  last strong evidence (observed in simulation: 659 s), and the "latch" countdown on the Spoofing
  page stays at 10:00 for the first minute before it starts counting down. Spoofing alarms should not
  vanish after 60 s just because the evidence expired; the operator may not have been looking at the
  screen.
* `reason` is the concatenation of active codes in severity order (for example `K1T1S1`); during a
  latched alert it shows the codes of the last alerting evaluation. The Spoofing page spells them out
  (`jump`, `time`, `speed`, `flat`, `GP/BD`, `alt`, `elev`, `power`).

## 7. Alert action: display or block

`SPOOF_ACTION` (default `'display'`; changeable in **Menu > Detection > Spoof act.**):

* **`display`** - only show the probability (the ghost icon with its level meter, the banner for `MEDIUM` and `HIGH`) and the Spoofing page. Sentences keep flowing to the radio
  and Wi-Fi.
* **`block`** - while the state is `HIGH` (including the latch, about 11 minutes in total), sentences of the types in
  `SPOOF_BLOCK_TYPES` (default `RMC`, `GGA`, constant in `main.py`) are **not** forwarded to the radio
  or Wi-Fi. The radio then has no fresh position instead of a suspicious one; other sentences (GSA,
  GSV, ZDA) continue.

Consequences to keep in mind before enabling `block`:

* Evaluation happens after parsing and *before* forwarding, so a T1 trigger blocks that very RMC. For
  K1, however, the jump is only confirmed on the **second** fix at the new position, so the **first
  spoofed fix (its RMC and GGA) has already been forwarded**.
* A false alarm cuts the radio's position for about 11 minutes. The default is therefore `display`;
  measure the false-alarm rate on your boat (section 14) before using `block`.
* Blocking a position does not make the radio safe: some radios will fall back to a manually entered
  or last known position. Know what yours does.

## 8. Parameters

Constants at the top of `spoofing.py`. Those marked *menu* are in **Menu > Advanced** and are applied
live (stored in `settings.json`).

| Constant | Default | Menu label (key, range) | Meaning |
|---|---|---|---|
| `MAX_SPEED_KN` | 60 | *Max speed* (`max_speed_kn`, 20-100) | implied speed above which a position change is a jump (K1) |
| `JUMP_MARGIN_M` | 30 | - | position-noise allowance added to the allowed distance (K1) |
| `GAP_MAX_MS` | 600000 | - | fixes further apart than this are not compared (K1) |
| `TIME_JUMP_MS` | 500 | *Time jump* (`time_jump_ms`, 200-3000) | base tolerance for T1; the baud-dependent burst time is added |
| `TIME_CHECK_MAX_MS` | 5000 | - | T1 only compares fixes at most this far apart locally |
| `K2_WINDOW_MS` | 10000 | - | K2 window |
| `K2_ABS_KN` / `K2_REL` | 3.0 / 0.5 | - | K2 mismatch must exceed both |
| `ALT_STEP_M` | 30 | *Alt step* (`alt_step_m`, 10-100) | altitude step (K3) |
| `UNIFORM_STD_DB` | 1.5 | *Flat C/N0* (`uniform_std_x10`, 5-40 = 0.5-4.0 dB) | std dev below which signals are "too uniform" (S1) |
| `UNIFORM_MIN_SATS` | 6 | - | satellites needed for S1 |
| `ELEV_CORR_MAX` | -0.2 | *S2 corr* (`elev_corr_x100`, -60 to 20 = -0.60 to 0.20) | smoothed elevation/C-N0 correlation at or below which S2 flags |
| `ELEV_CYCLES` | 12 | *S2 cycles* (`elev_cycles`, 3-60) | smoothed readings (GSV cycles) before S2 may fire |
| `ELEV_MIN_SATS` / `ELEV_ALPHA` | 6 / 0.15 | - | S2 |
| `CN0_RISE_DB` | 8 | - | power rise above baseline (S3) |
| `JACCARD_MIN` / `JACCARD_MIN_SATS` | 0.5 / 6 | - | set-change test (S3) |
| `CROSS_SHIFT_DB` / `CROSS_MIN_SATS` | 8 / 3 | - | GPS-BeiDou offset shift (C1) |
| `BASELINE_ALPHA` / `BASELINE_MIN_SAMPLES` | 0.05 / 5 | - | statistical baselines (S3, C1) |
| `REBASE_AFTER` | 10 | - | consecutive flagged cycles after which S3/C1 accept the new level as normal |
| `WARMUP_FIXES` | 30 | *Warm-up* (`warmup_fixes`, 10-120) | valid fixes before any indicator may fire |
| `WINDOW_MS` | 60000 | - | how long an indicator counts |
| `LATCH_MS` | 600000 | *Latch min* (`latch_min`, 1-60 minutes) | HIGH hold time, counted from the end of the 60 s evidence window (so the total is `WINDOW_MS` + `LATCH_MS`) |

Runtime options in `main.py`/menu: spoofing on/off (re-enabling restarts warm-up and baselines),
`SPOOF_ACTION`, `SPOOF_BLOCK_TYPES`, `GNSS_MODE` (needs `GPS+BD` for C1), `GPS_BAUDRATE` (affects the
T1 tolerance).

## 9. Detection latency

| Indicator | Earliest detection |
|---|---|
| T1 | on the first RMC after the time step (same fix) |
| K1 | on the **second** fix at the new position (about one fix interval after the jump; 1 s by default) |
| K3 | on the next GGA with the altitude step |
| K2 | at the end of the 10 s window (up to about 10-30 s after the movement starts) |
| S1, S3, C1 | on the next settled GSV cycle (about 4-5 s plus the 0.8 s settling time) |
| S2 | after 12 smoothed readings (about 1 minute from the first GSV) and while the smoothed correlation stays low |
| Alert clears | about 11 minutes after the last alerting evidence (60 s window + `LATCH_MS` of 10 min); `MEDIUM` and `LOW` clear when the evidence is older than 60 s |

## 10. Worked examples

**Persistent jump (K1).** Vessel at 5 kn, 1 s fixes. At one fix the position moves 5.5 km north. Allowed
distance is 61 m, so this is a suspected jump: it becomes the candidate and the state stays `OK`. At the
next fix the position is still at the new place (`not jump(candidate, cur)`), so **K1** is flagged: a
strong indicator, state `HIGH`, reason `K1`, held for about 11 minutes. (About 10 s later K2 usually joins it: reason `K1K2`.) With `block` active, the RMC of the
confirming fix is blocked; the RMC/GGA of the first jumped fix had already gone out.

**Glitch ignored.** A single fix is 120 m off, the next is back where it should be. The 120 m fix
becomes the candidate; the following fix is *not* a jump relative to the trusted position, so the
candidate is dropped silently. Nothing is flagged.

**Time step (T1).** GPS time advances 4.0 s between two fixes while the Pico measured 1.0 s: the
difference of 3.0 s exceeds the tolerance (1.875 s at 4800 baud with BeiDou) -> **T1**, strong ->
`HIGH` on that fix.

**Movement without speed (K2).** Over 10 s the position moves 150 m (about 29 kn implied) while RMC
speed reports about 0: `|29 - 0| > max(3, 0.5 x 29 = 14.5)` -> **K2**, a single medium indicator ->
`MEDIUM`. If **S1** (uniform signal strength) also fires within 60 s, two different medium
indicators -> `HIGH`.

**Flat signal levels (S1).** Eight tracked GPS satellites at `[40, 40, 41, 40, 40, 41, 40, 40]`
dB-Hz: standard deviation 0.43 < 1.5 -> **S1**, medium -> `MEDIUM`.

**GPS vs BeiDou shift (C1).** The learned offset GPS minus BeiDou is +2 dB. A later GSV cycle shows the
GPS satellites averaging 13 dB above BeiDou: shift of 11 dB > 8 -> **C1** (medium). Note a GPS-only
interference source can also cause this; the jamming indicator would then fire too.

**Altitude alone is not enough.** An altitude jump of 75 m flags **K3** (weak); one weak indicator never
raises the state above `LOW`, which is not an alert. A second weak indicator (for example S2) would give `MEDIUM`.

## 11. Where you see it

* **Main page**, top row right: a ghost icon with a level meter of one to three bars (nothing when `OK`); a `MEDIUM` or `HIGH` level also shows the alert banner and blinks the display.
* **Spoofing page**: the level as a badge, a tile for each of the eight indicators (lit while it counts),
  `warm n/30` or `armed`, and `latch m:ss` while a `HIGH` alert is latched.
* **Debug page**: indicator letters (`S:` field).
* **Screen-off timer**: a `MEDIUM` or `HIGH` state wakes a sleeping display and keeps it on.
* **Menu > Detection**: *Spoofing* on/off and *Spoof act.* (`display`/`block`).

## 12. False positives and what the detector cannot catch

**Things that can raise a flag without any attack**

| Situation | Likely indicator |
|---|---|
| Marina or dock with metal structures: multipath makes the position wander tens of metres | K1 (if > ~55-60 m between fixes), K2 |
| Receiver reacquiring after an outage, first fixes coarse | K1, K3 (suppressed only during the initial warm-up) |
| Receiver clock step after long signal loss | T1 (gaps over 5 s are skipped, but a step on the first normal pair can still show) |
| Overloaded GPS link (low baud, lots of sentences) delaying RMC unevenly | T1 (tolerance is widened for the nominal burst; unusual delays can exceed it) |
| Poor satellite geometry or few satellites | K3, S2, S3 |
| Bright, clean antenna with an unusually even signal | S1 |
| Satellite constellation changes (passes, satellite set updates), switching `GNSS_MODE` | S3 (Jaccard), C1 until the baseline re-learns |
| High-speed craft above `Max speed` | K1 (raise `Max speed`) |

**What it cannot catch**

* A spoofer that starts matched to the real position and signal levels and drags the solution slowly
  and smoothly within the speed and noise limits.
* Spoofing that begins during or after a signal outage longer than 10 min (no comparison across the gap).
* Any attack that keeps position, time, altitude, speed *and* signal statistics self-consistent.
* Anything the module's own receiver logic accepts as genuine and that shows in none of the NMEA
  fields we use. There is no authentication and no RAIM here.

## 13. Known limitations and implementation notes

* **Baselines learn from whatever they see** while no indicator has flagged, once the detector is armed.
  Start the device in normal conditions. After `REBASE_AFTER` consecutive flagged cycles a lasting change
  is accepted as the new normal, which also means a spoofer that stays in place long enough stops
  being flagged by S3/C1 (the strong kinematic indicators and the alert latch are not affected).
* **Only RMC fixes with status `A` advance the warm-up counter;** until the detector is armed no flag is
  raised and no statistical baseline is learned.
* **Position model.** Flat-earth distance (fine at these ranges; less so near the poles).
* **`cog_deg` is parsed but unused.** Course consistency (COG versus the direction of position change)
  would be a natural extra check.
* **Thresholds are first guesses** not validated on real recordings. Expect to tune them.
* **Timestamping.** `rx_ms` is taken in the GPS reader thread when the end of the line is received. Long
  pauses of that thread (for example heavy garbage collection) add jitter to T1; the tolerance
  has margin for the nominal case only.
* **No persistence of evidence.** A reboot clears all baselines, warm-up and any latched alert.

## 14. Tuning and validation

1. **Record logs** with **Menu > System > Log raw** and `mpremote repl | tee log.txt`: every framed sentence
   of every talker is printed as `<arrival ms> <sentence>` (several hours at the dock and under way,
   including manoeuvres, different sea states and equipment use).
2. **Replay** them: `python3 tools/replay.py log.txt --baud 4800 --gnss gps+bd` drives a real `Bridge` with
   both detectors, prints every state change (`SPOOF HIGH K1K2` etc.) and ends with the time in each
   state and the alarm episodes per hour. `--baud`/`--gnss` set the GPS-time check's tolerance as on the
   board. Without timestamps the tool synthesises the arrival time from GPS time, which makes T1 meaningless;
   keep the timestamps to test it.
3. **Adjust** thresholds in the menu (Advanced) or sweep them offline with `--set KEY=VALUE` (for example
   `--set max_speed_kn=40 --set time_jump_ms=800`) until normal operation produces no `HIGH` and few `MEDIUM`,
   with margin.
4. **Provoke events safely.** Never transmit spoofing signals: generating counterfeit GNSS signals
   outside a shielded test environment is illegal in most places and dangerous to others. Use
   recorded logs with injected changes (edit a copy of a log to insert a position jump or a time
   step) and the unit tests for the algorithmic behaviour.
5. Only then consider `SPOOF_ACTION = 'block'`.

## 15. Tests

`tests/test_spoofing.py` covers the parser fields (decimal-degree conversion with hemispheres, SOG,
UTC, rx time, altitude, `$PMTKSPF`, BeiDou GSA, per-satellite elevation) and the detector:
steady track stays `OK`; a 35 kn vessel stays `OK`; a persistent 5.5 km jump gives `HIGH` (K1) while
the first jumped fix does not; a single glitch is ignored; jumps during warm-up are ignored; a GPS
time step and time going backwards give `HIGH` (T1); movement without reported speed gives `MEDIUM`
(K2); the alert latches past the 60 s window and clears after the latch (the test evaluates sparsely; the 11-minute total with once-per-second evaluation was checked separately by simulation); an altitude step alone stays
`OK`; uniform C/N0 gives `MEDIUM` (S1); natural C/N0 spread stays `OK`; a GPS-BeiDou offset shift
flags C1; the T1 tolerance is configurable. Settings mapping is covered by `tests/test_settings.py`,
the page rendering by `tests/test_screens.py`, and the link-rate arithmetic by `tests/test_l76x.py`.

## 16. Code map

| What | Where |
|---|---|
| Detector, thresholds, decision rule, latch | `spoofing.py`: `SpoofDetector`, constants |
| Kinematics (K1, K2, T1) | `spoofing.py`: `_on_fix`, `_is_jump`, `_dist_m`, `_dt_ms` |
| Signal statistics (S1-S3, C1) | `spoofing.py`: `_on_gsv` |
| Altitude (K3) | `spoofing.py`: `_on_altitude` |
| Position/time/speed/altitude parsing | `NMEA.py`: `Parser._store_fix`, `_parse_rmc`, `_parse_gga`, `_to_units`, `_days_from_civil` |
| GSV per-satellite data | `NMEA.py`: `Parser._parse_gsv` (`sats_by_talker`) |
| Arrival timestamp | `bridge.py`: `GpsReader.step` / `SentenceFramer` (`rx_ms` when the line ends) |
| T1 tolerance from the link rate | `l76x.py`: `nmea_burst_ms`, `nmea_load`; `main.py`: `make_spoof` |
| Block action, scheduling | `bridge.py`: `Bridge._handle_sentence`, `forward_decision`, `_periodic` |
| Runtime enable/disable | `main.py`: `apply_setting` |
| Display | `screens.py`: main page label, `_draw_spoof` |
| Threshold settings | `settings.py`: `SCHEMA`, `apply_thresholds` |
| Replay tool | `tools/replay.py` |

## 17. References

* NMEA 0183 sentences used (RMC, GGA, GSV): `doc/NMEA0183.pdf`.
* Quectel L76-LB GNSS Protocol Specification V1.0 (`doc/dc671e606814c41999c20b2e3e0cabcd.pdf`): GPS and
  GPS+BeiDou only; no GST/GRS, no RAIM, no authentication; PMTK353 search modes.
* The signal-statistics indicators follow well-known qualitative properties of single-antenna spoofers
  and of real sky signals (see textbooks such as Kaplan and Hegarty, *Understanding GPS*, and the
  GNSS-interference literature); the specific thresholds are engineering choices for this device,
  not published standards.
