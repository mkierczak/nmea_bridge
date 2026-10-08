# Jamming / signal-degradation detection

Implementation: [`jamming.py`](../jamming.py) (class `JamDetector`). Inputs come from
[`NMEA.py`](../NMEA.py) (`Parser`), the radio-path wiring is in [`bridge.py`](../bridge.py) (set up by [`main.py`](../main.py)), the user interface is in
[`screens.py`](../screens.py) and the thresholds can be changed in the menu
(see [Parameters](#6-parameters)). See also [spoofing detection](spoofing-detection.md).

## Contents
1. [Purpose and what the result means](#1-purpose-and-what-the-result-means)
2. [Background](#2-background)
3. [Inputs](#3-inputs)
4. [Algorithm at a glance](#4-algorithm-at-a-glance)
5. [The algorithm step by step](#5-the-algorithm-step-by-step)
6. [Parameters](#6-parameters)
7. [Worked examples](#7-worked-examples)
8. [Where you see it](#8-where-you-see-it)
9. [The module's own detector and interference cancellation](#9-the-modules-own-detector-and-interference-cancellation)
10. [False positives and false negatives](#10-false-positives-and-false-negatives)
11. [Known limitations and implementation notes](#11-known-limitations-and-implementation-notes)
12. [Tuning and validation](#12-tuning-and-validation)
13. [Tests](#13-tests)
14. [Code map](#14-code-map)
15. [References](#15-references)

---

## 1. Purpose and what the result means

The detector watches the **quality of the GNSS signal** the L76B module reports and raises a flag when
that quality collapses in the way it does under radio-frequency interference (jamming). The result is a
probability of jamming in four levels; `MEDIUM` and `HIGH` are alerts (banner, blink, the display stays on):

| Label | Level | Meaning |
|---|---|---|
| *(blank)* | `INIT` | still learning what "normal" looks like (no verdict yet) |
| *(blank)* | `OK` | signal quality is consistent with the learned baseline |
| `JAM.` | `LOW` | signal quality is clearly degraded: one kind of evidence, or the module warns. Not an alert. |
| `JAM?` | `MEDIUM` | **alert**: two kinds of evidence agree (or the module reports a critical state) |
| `JAM!` | `HIGH` | **alert**: three or more agree, for example loss of tracking and of the fix together with a critical module status |

**Important.** The L76B only gives us NMEA sentences. It does not expose raw RF measurements such as
AGC level or the noise floor. Everything that reduces signal strength therefore looks the same to this
detector: an intentional jammer, an accidental interferer (a poorly shielded device near the antenna,
the boat's own electronics, a Wi-Fi radio too close), a damaged or disconnected antenna cable, heavy
rain on the antenna, or simply driving under a bridge. The labels are deliberately worded "signal low"
and "jamming possible", never "jamming confirmed". The detector cannot tell *why* the signal is gone, only
*that* it is.

It does **not** detect spoofing (counterfeit signals that look healthy); see
[spoofing detection](spoofing-detection.md).

## 2. Background

**GNSS signals are extremely weak.** A GPS L1 signal arrives at roughly -130 dBm, below the thermal
noise floor of the receiver. The receiver recovers it by correlating with the known spreading code.
Any interferer that raises the noise floor (jamming) shrinks the correlation margin, and the receiver's
signal-quality measure falls.

**C/N0** (carrier-to-noise-density ratio, unit **dB-Hz**) is that signal-quality measure, reported per
satellite. As a rough guide for an unobstructed antenna, healthy tracking shows about 35-50 dB-Hz;
below roughly 25-30 dB-Hz a receiver struggles to hold a satellite. Interference lowers C/N0 for
*all* satellites at once, because the noise floor rises for the whole band. This is the main signature
used here: a simultaneous, broad drop. Ordinary obstruction (a mast, a building, the horizon) normally
affects only some satellites, which is why the detector looks at the *average* and the *number of
satellites tracked*, not at single satellites.

**Where C/N0 appears in NMEA.** The GSV sentence ("satellites in view") carries up to four satellites
per sentence, each as four fields: `PRN, elevation, azimuth, C/N0`. An empty or `0` C/N0 means the
satellite is predicted to be in view but is not being tracked. A complete GSV *cycle* is a group of
1..n GSV sentences (field 1 = total messages, field 2 = message number). On the L76B, GPS satellites
use talker `GP` and BeiDou `BD`, so a GPS+BeiDou configuration produces two GSV cycles per period.

The module is configured (see `l76x.SET_NMEA_OUTPUT`) to send GSV every 5th fix, i.e. roughly every
5 s at the default 1 s fix interval (4 s at 800 ms). That sets the detector's sampling rate.

## 3. Inputs

| Quantity | Source | Parser attribute | Used for |
|---|---|---|---|
| Per-satellite C/N0 of the last complete GSV cycle of each talker | GSV (`GPGSV`, `BDGSV`) | `cn0_by_talker`, `cn0_stats()` -> `(tracked, mean, max)` over satellites with C/N0 > 0 | indicators C and N, the baseline |
| "Cycle completed" counter | GSV | `cn0_version` (incremented whenever any talker's last GSV message arrives) | sampling: one evaluation per new data |
| Satellites in view (total reported by the receiver, summed over talkers) | GSV field 3 | `birds_in_view` | indicator F |
| Fix available | main loop | `fix_ok = not no_fix` (see below) | indicator F, baseline gating |
| Module's own jamming status | `$PMTKSPF,n` | `module_jam_status` (0 unknown, 1 healthy, 2 warning, 3 critical) | indicator M |

`fix_ok` is computed in `bridge.py` (`Bridge.no_fix`): there is **no** fix if the last GGA reported fix type 0 (`NO`), or no
valid GGA/RMC has been received yet, or the last valid position is older than `FIX_STALE_TIMEOUT_MS`
(10 s).

## 4. Algorithm at a glance

```
every JAM_EVAL_PERIOD_MS (2 s) Bridge.step() calls evaluate(now, fix_ok)

 new settled GSV cycle, or new module status?  --no-->  data stale for >= STALE_MS (20 s)
        | yes                                    and baseline valid?   | no -> return unchanged state
        v                                                              | yes -> treat as "0 satellites tracked"
 tracked, mean = cn0_stats()   <---------------------------------------+
        |
        v
 indicators:  F = no fix  AND  satellites in view >= HIGH_VIEW_MIN                     (always)
              M = module reports warning/critical                                      (always)
              C = mean    < baseline_mean - CN0_DROP_DB          (only with a valid baseline)
              N = tracked < TRACKED_DROP_FRACTION * baseline_tracked   (only with a valid baseline)
        |
        v
 raw level (0 OK / 1 LOW / 2 MEDIUM / 3 HIGH) from the indicators   (section 5.4)
        |
        v
 debounce (hysteresis): worse for ENTER_CYCLES in a row -> state worsens
                        better for EXIT_CYCLES in a row -> state improves   (section 5.5)
        |
        v
 healthy sample -> learn it into the baseline (running mean, then slow EMA)   (section 5.2)
```

## 5. The algorithm step by step

### 5.1 Sampling

`bridge.py` calls `JamDetector.evaluate(now_ms, fix_ok)` every `JAM_EVAL_PERIOD_MS` (2 s). The call does
real work only when the parser has new GSV data **that has settled** (a cycle completed and no
constellation finished another one for `GSV_SETTLE_MS`, 800 ms, so GPS and BeiDou are evaluated
together, once, instead of twice on half-updated data), when the module reported a new
`$PMTKSPF` status, or when data has gone stale (5.6). With the default settings one sample therefore
arrives roughly every 4-5 s (plus up to 2 s of polling delay), and every "cycle" count below
(`ENTER_CYCLES`, `EXIT_CYCLES`, `BASELINE_MIN_SAMPLES`) is measured in such samples.

The parser also protects the input: a GSV cycle with a lost or out-of-order message is discarded as a
whole (it never produces a short, misleading "complete" cycle), and the data of a constellation that
has not completed a cycle for 20 s (`TALKER_TTL_MS`) is dropped, so a silent constellation's last
values do not linger.

### 5.2 The baseline: learning what "normal" is

The detector compares the present signal with a **per-installation baseline** of two numbers:

* `base_mean` - the usual mean C/N0 of tracked satellites (dB-Hz),
* `base_tracked` - the usual number of tracked satellites.

A baseline is needed because "normal" depends enormously on the antenna, its mounting, cable loss and
the sky view; a fixed threshold such as "below 30 dB-Hz" would be wrong on one boat and too lenient on
another.

**Warm-up (until `samples >= BASELINE_MIN_SAMPLES`, default 5).** Every sample with a fix and at least
one tracked satellite is learned as a plain running average:

```
samples == 0 :  base_mean = mean;  base_tracked = tracked
otherwise    :  a = 1 / (samples + 1)
                base_mean    += a * (mean    - base_mean)
                base_tracked += a * (tracked - base_tracked)
samples += 1
```

While the baseline is not valid the state is `INIT`, and the **C and N indicators cannot be raised**
(there is nothing to compare with). The F and M indicators *are* evaluated during this time, so a unit
that boots under interference (no fix with many satellites overhead, or the module reporting a warning or
critical state) shows `LOW`, `MEDIUM` or `HIGH` instead of staying blank. A sample on which F or M is active is
never learned into the baseline. Learning the baseline takes about 25 s of good data after boot
(5 samples x 4-5 s).

**Steady state.** Once valid, the baseline keeps adapting slowly with an exponential moving average
(`BASELINE_ALPHA = 0.05`, i.e. about 20 samples of memory, roughly 100 s):

```
base_mean    += 0.05 * (mean    - base_mean)
base_tracked += 0.05 * (tracked - base_tracked)
```

but **only** when everything looks healthy: the raw level of this sample is 0, the committed state is
`OK`, a fix exists and at least one satellite is tracked. A degraded or suspicious sample is *never*
learned, otherwise the baseline would follow the interference down and the alarm would silently
disappear ("baseline poisoning"). The slow adaptation lets the baseline follow genuine, gradual
changes (season, antenna ageing) without chasing sudden drops.

### 5.3 The four indicators

All are evaluated on each valid sample once the baseline is trusted.

| Letter | Name | Condition | Rationale |
|---|---|---|---|
| **C** | C/N0 drop | `mean < base_mean - CN0_DROP_DB` (default 6 dB) | Interference raises the noise floor for all satellites, so the mean falls. 6 dB is about a factor of four in signal-to-noise power: far outside normal variation of an average over several satellites. |
| **N** | fewer satellites | `tracked < TRACKED_DROP_FRACTION * base_tracked` (default 0.6) | Weak satellites are dropped first, so the tracked count falls. Looking at the count catches interference strong enough to lose satellites even if the survivors' average stays high. |
| **F** | fix lost with sky in view | `not fix_ok` **and** `birds_in_view >= HIGH_VIEW_MIN` (default 6) | The receiver knows many satellites are overhead (from almanac/ephemeris predictions) but cannot produce a position. Plain "no fix with few satellites in view" is just a cold start or an obstructed view, so it is not flagged. |
| **M** | module warning | `module_jam_status >= 2` | The module's own jamming detector (section 9). Status 2 = warning, 3 = critical. |

`mean` and `tracked` come from `Parser.cn0_stats()`: over all satellites of all talkers with
C/N0 > 0, in the last complete GSV cycle of each talker.

### 5.4 From indicators to a raw level

```
level = C + N + F                      # each indicator that is true counts once
if module status == 2:                 level += 1  # the module's warning counts once ...
if module status == 3:                 level += 2  # ... its critical status twice
level = min(level, 3)                  # 0 OK / 1 LOW / 2 MEDIUM / 3 HIGH
```

The probability grows with the number of agreeing *different* kinds of evidence, so single odd
measurements (one satellite blocked, a brief dip) stay at the mild `LOW`. The module's critical status
alone gives `MEDIUM` (it is derived from receiver-internal RF measurements this detector cannot see);
together with one more indicator it gives `HIGH`.

The `reason` string lists the letters that were true on the last evaluation (for example `CN` or `CNM`);
the Signal page spells them out as words (`cn0`, `sat`, `fix`, `mod`).

### 5.5 Debounce (hysteresis)

The committed state follows the raw level only after it has persisted:

```
raw level  > current : _up   += 1, _down = 0;  when _up   >= ENTER_CYCLES -> state = raw level
raw level  < current : _down += 1, _up   = 0;  when _down >= EXIT_CYCLES  -> state = raw level
equal                : _up = _down = 0
```

Defaults: `ENTER_CYCLES = 2`, `EXIT_CYCLES = 3`. Entering is quicker than leaving on purpose: an alarm
should appear promptly but not flap on and off. With about 5 s per sample the state worsens after
roughly 10 s of continuous bad data and recovers after roughly 15 s of continuous good data. When
the state changes, it jumps straight to the new raw level (OK -> MEDIUM in one step is possible).

### 5.6 Stale data

A jammed receiver may stop producing useful GSV cycles. If **no** new GSV cycle has arrived for
`STALE_MS` (20 s) and the baseline is valid, the detector treats the period as a sample with
`tracked = 0, mean = 0`. That satisfies C and N (and F as well when the fix is lost), so the raw level is
2 (`MEDIUM`) or 3 (`HIGH`). A stale period is counted once per `STALE_MS`, so `ENTER_CYCLES = 2` stale
periods (about 42 s) are needed to reach it. When GSV returns, normal hysteresis applies (`EXIT_CYCLES` good samples).
Stale handling is inactive while the baseline is not yet valid. The elapsed time is computed with
`ticks_diff`, so it keeps working when MicroPython's millisecond counter wraps (about every 12.4 days;
covered by a regression test).

### 5.7 Outputs

`evaluate()` returns `(state, reason)`; `state` property gives `INIT/OK/LOW/MEDIUM/HIGH`; `label()` returns
`''` for `INIT` and `OK`, otherwise `JAM.`, `JAM?` or `JAM!`; `signature()` returns a tuple used by the display code to decide
whether a redraw is needed.

## 6. Parameters

Constants are at the top of `jamming.py`. The ones marked *menu* can be changed on the device
(**Menu > Advanced**); they are stored in `settings.json` and applied live by assigning the module
constants (the detector reads them on every call).

| Constant | Default | Menu label (key) | Meaning | Tuning advice |
|---|---|---|---|---|
| `CN0_DROP_DB` | 6 | *CN0 drop* (`cn0_drop_db`, 3-15) | mean C/N0 drop below baseline that sets C | Lower = more sensitive. Raise it if you get `LOW` from ordinary weather or manoeuvres. |
| `TRACKED_DROP_FRACTION` | 0.6 | *Sats drop%* (`tracked_drop_pct`, 30-90) | fraction of baseline tracked count below which N is set | Lower = fewer alarms from partial obstruction. |
| `ENTER_CYCLES` | 2 | *Jam enter* (`jam_enter_cycles`, 1-6) | consecutive worse samples before the state worsens | Raise to suppress short dips. |
| `EXIT_CYCLES` | 3 | *Jam exit* (`jam_exit_cycles`, 1-10) | consecutive better samples before it improves | Raise to keep alarms up longer. |
| `HIGH_VIEW_MIN` | 6 | - | satellites in view needed for F | Lower for poor horizons. |
| `BASELINE_MIN_SAMPLES` | 5 | - | samples before the baseline is trusted | Raise for a steadier baseline at the cost of a longer `INIT`. |
| `BASELINE_ALPHA` | 0.05 | - | weight of a new healthy sample | Smaller = slower adaptation, more resistant to slow poisoning. |
| `STALE_MS` | 20000 | - | no GSV for this long counts as a lost-signal sample | |

## 7. Worked examples

The C/N0 and satellite numbers are illustrative. The timings (2 bad samples to `LOW`, 3 good samples
to recover, 2 stale periods of about 21 s to `MEDIUM`) were confirmed by running the detector on
simulated GSV data.

**Interference appears.** Baseline: `base_mean = 39.5 dB-Hz`, `base_tracked = 8`. A jammer comes up and
all eight satellites drop to about 19.5 dB-Hz but stay tracked.
* Sample 1: C is true (19.5 < 39.5 - 6), N false (8 >= 4.8), F false -> raw level 1. `_up = 1`, state stays `OK`.
* Sample 2: raw level 1 again -> `_up = 2 >= ENTER_CYCLES` -> state `LOW`, reason `C`. (about 10 s)
* The jammer gets stronger and satellites are lost (`tracked = 3`, receiver loses the fix with 9 in
  view): C, N and F are all true -> raw level 3 -> after two samples `HIGH`, reason `CNF`.

**Recovery.** The interference stops. Three consecutive healthy samples (about 15 s) are needed
(`EXIT_CYCLES = 3`), then the state returns to `OK`. During those samples the baseline is *not* updated
(the state is not yet `OK`).

**Receiver goes silent.** No GSV for 20 s: stale period 1 (`tracked = 0`): raw level 2, `_up = 1`.
After another 20 s: stale period 2, `_up = 2` -> `MEDIUM` with reason `CN` (about 42 s in total).

**Cold start in a poor location.** Steady 10 dB-Hz with two satellites from the beginning: the
baseline learns *that* as normal, so no alarm is raised (`INIT` then `OK`). This is by design (it cannot
know it should be better) and is a limitation (section 11).

## 8. Where you see it

* **Main page**, top row, to the right of the time: `JAM.`, `JAM?`, `JAM!` for `LOW`, `MEDIUM`, `HIGH` (blank while `OK` or `INIT`); `MEDIUM` and `HIGH` also show the alert banner and blink the display.
* **Signal page** (short-press through the pages): the level as a badge, reasons in words, `CN0 mean/baseline dB`,
  `sats tracked/baseline`, mean C/N0 per constellation (GP, BD), module jamming status
  (`ok/warn/CRIT/?`) and the AIC result (`AIC+`, `AIC-`, `AIC?`).
* **Stats page**: a line `CN mean/baseline nTracked/baseline`.
* **Screen-off timer**: an active `MEDIUM`/`HIGH` wakes a sleeping display and keeps it on.
* **Enable/disable:** *Menu > Detection > Jamming* (live; re-enabling restarts the baseline learning).

## 9. The module's own detector and interference cancellation

From the Quectel L76-LB protocol specification (`doc/dc671e606814c41999c20b2e3e0cabcd.pdf`, PMTK838,
section 3.43, PDF pages 47-48; found in our research of the vendor documents):

* `$PMTK838,1` enables the module's **jamming detection**. (The command is titled "anti-spoofing" in the
  document, but the text describes jamming detection only; it is not signal authentication.)
* The module then reports `$PMTKSPF,<n>`: `1` healthy, `2` warning, `3` critical.
* Per the document: without a position, the status goes to 3 if there is still no fix after 200 s;
  with a position, it moves 1 -> 2 -> 3 on continuous jamming.

`main.py` sends `$PMTK838,1` at boot. The parser stores the last reported value in
`module_jam_status`; the detector uses it as indicator M (status 2 counts once, which alone gives `LOW`,
status 3 counts twice, which alone gives `MEDIUM`). This is valuable because the module sees RF-level information we do not.
Caveats: we have not verified on hardware that the L76B really emits `$PMTKSPF`; and because the
parser keeps the last value, a status of 2 or 3 persists until the module reports 1 again.

**Active Interference Cancellation (AIC).** `$PMTK286,1` asks the module to enable AIC, which suppresses
narrow-band interference. The Quectel specification lists it as enabled by default on this module;
the Fastrax manual describes it for related receivers as "effective narrow-band interference and
jamming elimination". We still send the command at boot and show the module's acknowledgement
(`$PMTK001,286,<flag>`, 3 = success) as `AIC+`/`AIC-`/`AIC?` on the Signal and Debug pages. AIC is a
mitigation, not a detector, and it does not help against wide-band jamming.

## 10. False positives and false negatives

| Situation | Effect | Why |
|---|---|---|
| Passing under a bridge, entering a covered berth, heavy rain, snow on the antenna | `LOW` to `HIGH` while it lasts | Physically the same as jamming: signal drops for all satellites. |
| Antenna cable fault, loose connector | `LOW`-`HIGH` | Same. Actually a useful fault detector. |
| Own electronics (Wi-Fi radio, switching supplies, VHF/AIS transmitters near the antenna) | `LOW`-`HIGH` or a lower baseline | Local interference is real interference. Compare C/N0 with the equipment on and off. |
| Slow, steady degradation over hours | Not detected | The baseline follows it (EMA). |
| Jamming that is already present when the system starts | C and N cannot fire (no earlier "normal"), and the baseline learns the degraded state if the receiver still gets a fix | The F and M indicators work from the first sample: no fix with many satellites in view, or the module's own warning, raises `LOW`-`HIGH` immediately. |
| Narrow-band jamming absorbed by AIC | Little or no effect on C/N0 -> no alarm | Nothing to see; that is the point of AIC. |
| Very short bursts (< `ENTER_CYCLES` samples, ~10 s) | Not shown | Debounce. Lower `Jam enter` to catch them (more false alarms). |
| Jamming plus spoofing (jam to break lock, then spoof) | Jamming indicator fires first; spoofing detector may follow | See [spoofing detection](spoofing-detection.md); the two run independently. |

## 11. Known limitations and implementation notes

* **No RF measurements.** Only NMEA-level signal quality is available (section 1).
* **Baseline quality matters.** The first 5 samples define "normal"; start the device with the antenna
  in its usual, clear position. A baseline taken during interference makes the detector blind to it.
* **Thresholds are first guesses** that have not been validated against real recordings. Record logs
  and use `tools/replay.py` (section 12) before trusting the alarms operationally.
* **Sampling is coarse** (about one sample per 4-5 s, plus up to 2 s polling delay). The state therefore
  changes in steps of that size and short events can be missed.
* **Averages hide details.** `mean` is over all tracked satellites of all talkers. A constellation-specific
  effect (only BeiDou affected) is attenuated; the Signal page shows the per-constellation means for
  manual inspection.
* **Sticky module status.** `module_jam_status` keeps its last value until another `$PMTKSPF` arrives.
* **`birds_in_view` can be stale** for a constellation that stops sending GSV (each talker's last total
  is kept). It only influences indicator F.

## 12. Tuning and validation

**Record a log.** Switch on **Menu > System > Log raw** and capture the console on the computer with
`mpremote repl | tee log.txt`. Every framed sentence of every talker is printed as
`<arrival ms> <sentence>`, which is what the replay tool reads (BeiDou, `$PMTK...` and bad-checksum lines
included, so all indicators can be tested). Logs without timestamps also work: the replay tool then
derives the time from the GPS time of the RMC sentences.

**Replay offline.**

```
python3 tools/replay.py my_log.nmea [--baud 4800] [--gnss gps+bd] [--set KEY=VALUE ...] [-q]
```

drives a real `Bridge` with the same detectors, the same evaluation cadence and GSV settling as the board
(it steps the bridge between sentences like the board's 10 ms loop), prints every change of the jamming
and spoofing states with the time, reasons and C/N0 figures, and ends with a summary, for example:

```
     40.0s JAM   LOW   why=C    cn0=19.5 base=39.5 n=4
     60.0s JAM   OK    why=     cn0=39.5 base=39.5 n=4
     41.0s SPOOF HIGH   K1K2
--- summary ---
log duration   3600 s (1h00m), 7200 sentences: 7190 valid, 10 invalid, 0 with unreadable fields
jamming        INIT 0.7%  OK 98.9%  LOW 0.4%  MEDIUM 0.0%  HIGH 0.0%
               alarm episodes: 2 (2.00 per hour)
```

`--set` overrides the advanced thresholds of the menu (`cn0_drop_db`, `tracked_drop_pct`,
`jam_enter_cycles`, `jam_exit_cycles`, and the spoofing ones) so you can sweep them on the same data;
the alarm episodes per hour (periods at `MEDIUM` or `HIGH`, the levels that raise an alert on the board) on a log recorded in normal conditions are the false-alarm rate.
`--baud` and `--gnss` only matter for the spoofing detector's GPS-time tolerance.

**Provoke a controlled signal drop.** The only legitimate way to test the detector on hardware is to
reduce the signal: cover the antenna with a metal box or foil, or unplug it briefly. **Never transmit
a jamming signal**: jamming GNSS is illegal in most jurisdictions and dangerous to others nearby.
Check that the state goes `LOW`/`MEDIUM` after the expected delay and recovers after uncovering.

**Suggested procedure:** log a few hours at the dock and a few under way, replay, and set `CN0 drop`,
`Sats drop%` and the cycle counts so that normal operation produces no alarms with margin.

## 13. Tests

`tests/test_jamming.py` covers: C/N0 parsing across multi-message GSV and talkers; no alert during
warm-up; healthy data stays `OK`; a C/N0 drop produces `LOW` after the debounce and recovers after the
exit cycles; loss of tracking plus fix produces `HIGH`; the probability follows the number of agreeing indicators; stale GSV counts as lost signal; the module's
`$PMTKSPF` status feeds the detector. Parser behaviour is covered in `tests/test_nmea.py`; the
settings that map to the thresholds in `tests/test_settings.py`.

## 14. Code map

| What | Where |
|---|---|
| Detector, thresholds, state machine | `jamming.py`: `JamDetector.evaluate`, `_learn`, constants |
| GSV parsing, C/N0 statistics | `NMEA.py`: `Parser._parse_gsv`, `cn0_stats`, `cn0_version` |
| `$PMTKSPF` / `$PMTK001` parsing | `NMEA.py`: `Parser._parse_pmtk` |
| Module commands (`PMTK838`, `PMTK286`, GSV rate) | `l76x.py`, `main.py` (`gps_init`) |
| Scheduling, `fix_ok` | `bridge.py` (`Bridge._periodic`, `no_fix`) |
| Create/destroy at runtime | `main.py` (`apply_setting`) |
| Display | `screens.py`: main page label, `_draw_signal`, stats page |
| Threshold settings | `settings.py`: `SCHEMA`, `apply_thresholds` |

## 15. References

* Quectel L76-LB GNSS Protocol Specification V1.0, section on `PMTK838` (jamming detection) and `PMTK286`
  (AIC): `doc/dc671e606814c41999c20b2e3e0cabcd.pdf`.
* NMEA 0183 sentence formats (GSV, GGA, RMC): `doc/NMEA0183.pdf`.
* General background on GNSS interference and C/N0 is textbook material (for example the GNSS
  chapters of *Understanding GPS: Principles and Applications*, Kaplan and Hegarty); the numeric
  "typical" values quoted above are rules of thumb, not specifications of this module.
