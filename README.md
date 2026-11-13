# Roland TrueVIS VG3: network status protocol

The Roland TrueVIS VG3 print-and-cut printer reports its live status over the
local network: what it's doing, ink levels, heater temperatures and media
width. Roland doesn't document this. Its own tools (VersaWorks, Roland DG
Connect) read it, but third-party software can't without reverse engineering.

This repository documents what that status means. It also includes two small
parsers so you can show your printer's status on a dashboard or in a job
system, or log it for usage reports, without decoding it again yourself.

**Everything here was decoded by watching one printer go through each state
and comparing the readings with its front panel.** It isn't official, it may
be incomplete, and other models or firmware versions may differ.
Corrections and readings from other machines are very welcome (see
[Contributing](#contributing)).

| | |
|---|---|
| Tested on | Roland TrueVIS **VG3-640**, firmware 3.40 (panel 3.0.1), 4-colour ×2 ink setup (CMYK + CMYK) |
| Transport | **SNMP v1**, community `public`, UDP 161 |
| Read-only | Yes. Nothing is ever sent to the printer except SNMP GET requests |

## Quick start

```bash
# Python 3, no dependencies
python3 python/rolandvg.py 192.168.1.50          # one reading, human-readable
python3 python/rolandvg.py 192.168.1.50 --json   # JSON
python3 python/rolandvg.py 192.168.1.50 --watch  # print every change (2 s polling)
```

```
VG3-640  Ready
  heaters on   print 45 °C  dryer 55 °C
  media        1480 mm
  ink   M1 500 ml  M2 256 ml  C3 219 ml  C4 409 ml  Y5 0 ml (empty)  Y6 231 ml  K7 497 ml  K8 142 ml
  last event   0x8242
```

You can do the same with the net-snmp tools:

```bash
snmpget -v1 -c public 192.168.1.50 1.3.6.1.4.1.25816.3.2.1.1
```

For TypeScript or JavaScript, [`typescript/roland-vg.ts`](typescript/roland-vg.ts) is a
dependency-free parser for the string that this OID returns.

## What the printer exposes

The printer answers only SNMP v1 with the community `public`. It doesn't
implement the standard MIB-II, Host Resources or Printer MIB tables. A full
walk returns just one value, the same string under two OIDs:

| OID | Notes |
|---|---|
| `1.3.6.1.4.1.25816.3.2.1.1` | Roland DG enterprise branch (25816). **Use this one.** |
| `1.3.6.1.4.1.1229.2.2.2.1.24.1` | The same string |

The only open TCP ports were **9100**, the raw print port, and **9500**,
which accepts a connection but sends nothing. **Don't send data to 9100**:
the printer will try to print it.

### The device-ID string

The value is an IEEE 1284-style device ID: `KEY:value;` pairs.

```
MFG:Roland;CMD:RTL,RDGL3,PJL,DW_PCL;MDL:VG3-640;DES:Roland VG3-640;CLASS:PRINTER;
VER:3.40;STATUS:0002010108200000…;SID:XXXXXXX;MODE:APPLICATION;FMI:4.00;
LASTEVENT:00008216;CAPABILITY:00000033;PANEL:3.0.1;SECURITY:0.00;
```

| Key | Meaning |
|---|---|
| `MDL` / `DES` | Model |
| `VER`, `PANEL`, `FMI` | Firmware versions |
| `SID` | The printer's serial number |
| `MODE` | Always `APPLICATION` in normal use |
| `STATUS` | **Live status** (below) |
| `LASTEVENT` | Hex counter that goes up by 1 or more with each machine event (see [Events](#events)) |

## The STATUS field

`STATUS` is a hex string made up of **fields**. Each field is:

```
[type: 2 hex digits] [length: 2 hex digits] [value: <length> hex digits]
```

The length counts **hex digits (nibbles), not bytes**, so fields don't always
line up on byte boundaries. Read it as a string, not as bytes.

Example, split into fields:

```
00 02 01
01 08 20008000                              ← state
02 01 0
03 20 13021303130113001300130113031302      ← ink colour per head channel
04 08 11111111                              ← pouch status
05 20 01F401F401F401F401F401F401F401F4      ← pouch capacity (ml)
06 20 01F4010200DD0199000000E701F10090      ← ink remaining (ml)
07 03 5C8                                   ← media width (mm)
08 04 2D37                                  ← heater temperatures
09 02 11                                    ← heaters on/off
0A 05 00000
```

| Type | Length | Meaning |
|---|---|---|
| `00` | 2 | Always `01`. Probably a format version |
| `01` | 8 | **State**: four bytes b0–b3, see [State](#state-field-01) |
| `02` | 1 | Always `0` so far |
| `03` | 32 | Ink colour per **head channel**: 8 × 4 hex, `13xx`, where xx is a colour code (`00` = yellow; see notes) |
| `04` | 8 | **Pouch status** per cartridge slot: 8 × 1 hex. `1` = OK, `2` = pouch removed |
| `05` | 32 | **Pouch capacity** per slot: 8 × 4 hex, ml (`01F4` = 500) |
| `06` | 32 | **Ink remaining** per slot: 8 × 4 hex, ml |
| `07` | 3 | **Media width**, mm. `000` = no media set |
| `08` | 4 | **Temperatures**: 2 hex digits print heater °C, then 2 hex digits dryer °C |
| `09` | 2 | **Heaters switched on**: `11` = on, `00` = off (asleep) |
| `0A` | 5 | Always `00000` so far |

### Ink (fields 04, 05, 06)

Fields 04–06 list the eight **cartridge slots in their physical order, left
to right**. On the tested printer in CMYK ×2 mode the slots are
**M M C C Y Y K K**:

| Slot | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 |
|---|---|---|---|---|---|---|---|---|
| Colour | M | M | C | C | Y | Y | K | K |

- The remaining amount (field 06) matched the panel's ink display for every
  pouch, and went down by about 1 ml per pouch during a small test print and
  by about 2 ml during a head clean.
- A pouch close to empty reads **0 ml** while the panel still shows only a
  low-ink warning, so treat 0 as "nearly empty" rather than "empty".
- When a pouch is pulled out, its status (field 04) changes to `2` and both its
  capacity and remaining read `0`. When it goes back in, they return to their
  previous values.
- Each colour has two pouches, and the printer stays **Ready** with one of a
  colour's pouches removed. It only stops when both are out.
- The panel's low-ink warning is **not** reflected in field 04, which stayed
  `1`. Set your own low-ink threshold on the ml value.

Field 03 lists colours per **head channel** in a mirrored order (`02 03 01 00 00
01 03 02` on the tested machine), **not** cartridge order. The `00` channels
belong to yellow. The other codes haven't been confirmed, so use the cartridge
order above when mapping colours.

### State (field 01)

Field 01 is four bytes, `b0 b1 b2 b3`.

**b0** is a set of flags for the current job:

| Bit | Meaning |
|---|---|
| `0x40` | Media not set (lever up / not loaded) |
| `0x20` | Ready |
| `0x10` | Printing (also briefly while feeding after a cut) |
| `0x08` | Cutting |
| `0x02` | Paused (added to the job bit: `12` = printing, paused) |
| `0x01` | Cancelling (added to the job bit: `11` = cancelling a print) |
| `0x00` | No flags: maintenance (head cleaning or other panel maintenance) |

**b1**

| Bit | Meaning |
|---|---|
| `0x80` | Busy: carriage moving or media being measured (setup, moving to clean) |
| `0x08` | **Asleep** |

**b2**

| Bit | Meaning |
|---|---|
| `0x80` | Heaters at temperature (also set while the heaters are switched off) |
| `0x40` | Head cleaning |

**b3** has always been `00`.

#### Codes seen

| Field 01 | What the printer was doing |
|---|---|
| `20008000` | Ready, heaters at temperature |
| `20000000` | Ready, heaters still warming up (after media setup or waking) |
| `20808000` | Ready, carriage moving (just before or after cleaning) |
| `10008000` | Printing |
| `08008000` | Cutting |
| `12008000` | Print paused |
| `11008000` | Print being cancelled (about 16 s), then back to ready |
| `40008000` | Media not set |
| `40808000` | Media setup in progress (measuring) |
| `0080C000` | Head cleaning |
| `00808000` | Other panel maintenance |
| `20080000` | Asleep |

#### Suggested decoding order

```
if b1 & 0x08            → asleep
if b2 & 0x40            → cleaning
if b0 == 0x00           → maintenance
if b0 & 0x01            → cancelling
if b0 & 0x02            → paused
if b0 & 0x08            → cutting
if b0 & 0x10            → printing
if b0 & 0x40            → media not set
if b0 & 0x20            → ready  (warming up if b2 & 0x80 is clear and field 09 ≠ 00)
otherwise               → unknown (please report it!)
```

The panel already says "Ready" while the heaters are warming up, so a
"Ready, heating" label avoids appearing to contradict the panel.

### Heaters (fields 08, 09)

- Field 08 holds the actual print heater and dryer temperatures in °C. They
  matched the panel exactly.
- With media released or the printer asleep, the heaters switch off and the
  temperatures drift down to room temperature.
- Field 09 goes from `11` to `00` about 2 seconds before the printer enters
  sleep, and back to `11` on waking.
- The tested printer went to sleep after **30 minutes** idle and kept answering
  SNMP while asleep.

### Media width (field 07)

The loaded media width in mm, measured during media setup. For example `5CB`
is 1483 mm and `5C8` is 1480 mm. It reads `000` while the media lever is up or
before setup has finished.

### Events

`LASTEVENT` goes up when something happens on the machine: media setup steps,
starting and finishing a job, a pouch going in or out, and panel notices. It
does **not** say what the event was.

Panel reminders and warnings such as "shake the ink", "wiper cleaning due" and
low ink **only** advance `LASTEVENT`. None of them appear in `STATUS`.

Not visible at all: opening the front cover while idle (no change).

## Not yet seen

These states weren't captured on the test machine. Please open an issue if you
capture any of them:

- media end / media out during a print
- both pouches of one colour empty
- media jam, carriage errors, other error screens
- other VG3 models (VG3-540), other ink configurations (8-colour), the VG2 or
  SG series

## Polling tips

- One SNMP GET takes a few milliseconds. Polling every few seconds is fine,
  but cache the result if many screens show it.
- If the printer doesn't answer, it's switched off or off the network. Sleep
  still answers.
- Ink use can be estimated from changes in field 06, and print/cut time from
  state changes.

## Repository contents

| Path | |
|---|---|
| [`python/rolandvg.py`](python/rolandvg.py) | Query and decode (Python 3 standard library only): one-shot, `--json`, `--watch` |
| [`typescript/roland-vg.ts`](typescript/roland-vg.ts) | Dependency-free parser for the device-ID string |
| [`samples/samples.tsv`](samples/samples.tsv) | Labelled readings for each state, with the serial number removed |
| [`python/test_samples.py`](python/test_samples.py) | Checks the decoder against every sample (`python3 python/test_samples.py`) |

## Contributing

If you have a Roland that reports through this OID, readings are the most
useful thing you can contribute. Ideally:

1. Run `python3 python/rolandvg.py <ip> --watch --raw` (or `snmpget`).
2. Put the machine into a known state and write down what the panel shows.
3. Open an issue with the model, firmware (`VER`), ink setup, the panel state
   and the raw `STATUS` string. **Remove the `SID` value** first, because it
   is your serial number.

## Disclaimer

Not affiliated with or endorsed by Roland DG. "Roland", "TrueVIS" and
"VersaWorks" are trademarks of Roland DG Corporation. This information comes
from observing a printer through its normal network interface and is provided
as-is.

## Licence

[MIT](LICENSE)
