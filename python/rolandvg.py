#!/usr/bin/env python3
"""Read and decode the live status of a Roland TrueVIS VG3 over SNMP v1.

    rolandvg.py HOST            one reading, human-readable
    rolandvg.py HOST --json     one reading as JSON
    rolandvg.py HOST --watch    print a line whenever the status changes
    add --raw to include the raw STATUS string

Standard library only. Read-only: sends SNMP GET requests and nothing else.
See README.md for what each field means.
"""
import argparse
import json
import socket
import sys
import time

OID = "1.3.6.1.4.1.25816.3.2.1.1"

# Cartridge slots left to right on a VG3 in 4-colour x2 (CMYK + CMYK) mode.
CARTRIDGE_ORDER = ["M", "M", "C", "C", "Y", "Y", "K", "K"]
COLOUR_NAME = {"C": "Cyan", "M": "Magenta", "Y": "Yellow", "K": "Black"}
LOW_INK_ML = 50

# ---------------------------------------------------------------- SNMP v1 GET


def _len(n):
    if n < 0x80:
        return bytes([n])
    if n < 0x100:
        return bytes([0x81, n])
    return bytes([0x82, n >> 8, n & 0xFF])


def _tlv(tag, value):
    return bytes([tag]) + _len(len(value)) + value


def _oid(s):
    parts = [int(x) for x in s.split(".")]
    body = bytes([40 * parts[0] + parts[1]])
    for p in parts[2:]:
        chunk = [p & 0x7F]
        p >>= 7
        while p:
            chunk.insert(0, 0x80 | (p & 0x7F))
            p >>= 7
        body += bytes(chunk)
    return _tlv(0x06, body)


def _read(buf, i):
    tag, length = buf[i], buf[i + 1]
    i += 2
    if length & 0x80:
        n = length & 0x7F
        length = int.from_bytes(buf[i:i + n], "big")
        i += n
    return tag, buf[i:i + length], i + length


def snmp_get_string(host, oid=OID, community="public", timeout=2.0):
    """SNMP v1 GET of one OCTET STRING. Returns str, or None if the OID is missing."""
    request_id = int(time.time() * 1000) & 0x7FFF
    varbind = _tlv(0x30, _tlv(0x30, _oid(oid) + b"\x05\x00"))
    pdu = _tlv(0xA0, _tlv(0x02, request_id.to_bytes(2, "big")) + _tlv(0x02, b"\x00") + _tlv(0x02, b"\x00") + varbind)
    message = _tlv(0x30, _tlv(0x02, b"\x00") + _tlv(0x04, community.encode()) + pdu)

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(timeout)
        s.sendto(message, (host, 161))
        data, _ = s.recvfrom(65535)

    _, msg, _ = _read(data, 0)
    _, _, j = _read(msg, 0)  # version
    _, _, j = _read(msg, j)  # community
    _, pdu, _ = _read(msg, j)
    _, _, k = _read(pdu, 0)  # request id
    _, err, k = _read(pdu, k)
    _, _, k = _read(pdu, k)  # error index
    if int.from_bytes(err, "big"):
        return None
    _, vbl, _ = _read(pdu, k)
    _, vb, _ = _read(vbl, 0)
    _, _, m = _read(vb, 0)
    tag, value, _ = _read(vb, m)
    return value.decode("latin-1") if tag == 0x04 else None


# ---------------------------------------------------------------- decoding


def parse_device_id(raw):
    out = {}
    for part in raw.split(";"):
        key, sep, value = part.partition(":")
        if sep:
            out[key.strip()] = value.strip()
    return out


def parse_fields(status_hex):
    """STATUS = repeated [type:2 hex][length in hex digits:2 hex][value]."""
    fields, i = {}, 0
    while i + 4 <= len(status_hex):
        ftype = int(status_hex[i:i + 2], 16)
        length = int(status_hex[i + 2:i + 4], 16)
        fields[ftype] = status_hex[i + 4:i + 4 + length]
        i += 4 + length
    return fields


def _chunks(value, size):
    if not value:
        return []
    return [int(value[i:i + size], 16) for i in range(0, len(value) - size + 1, size)]


def decode_state(b0, b1, b2, heaters_on):
    if b1 & 0x08:
        return "asleep"
    if b2 & 0x40:
        return "cleaning"
    if b0 == 0x00:
        return "maintenance"
    if b0 & 0x01:
        return "cancelling"
    if b0 & 0x02:
        return "paused"
    if b0 & 0x08:
        return "cutting"
    if b0 & 0x10:
        return "printing"
    if b0 & 0x40:
        return "media-not-set"
    if b0 & 0x20:
        return "warming-up" if heaters_on and not (b2 & 0x80) else "ready"
    return "unknown"


STATE_LABEL = {
    "asleep": "Asleep",
    "cleaning": "Head cleaning",
    "maintenance": "Maintenance",
    "cancelling": "Cancelling",
    "paused": "Paused",
    "cutting": "Cutting",
    "printing": "Printing",
    "media-not-set": "Media not set",
    "warming-up": "Ready (heaters warming up)",
    "ready": "Ready",
    "unknown": "Unknown",
}


def decode(raw):
    """Decode the device-ID string into a dict. Returns None if it has no STATUS."""
    dev = parse_device_id(raw)
    status = dev.get("STATUS")
    if not status:
        return None
    f = parse_fields(status)

    state_bytes = (_chunks(f.get(0x01), 2) + [0, 0, 0, 0])[:4]
    heaters_on = f.get(0x09, "11") != "00"
    pouches = list(f.get(0x04, ""))
    capacity = _chunks(f.get(0x05), 4)
    remaining = _chunks(f.get(0x06), 4)

    inks = []
    for i, colour in enumerate(CARTRIDGE_ORDER):
        pouch = {"1": "ok", "2": "removed"}.get(pouches[i] if i < len(pouches) else "", "unknown")
        ml = remaining[i] if i < len(remaining) else None
        if pouch == "removed":
            level = "removed"
        elif ml is None:
            level = "unknown"
        elif ml <= 0:
            level = "empty"
        elif ml < LOW_INK_ML:
            level = "low"
        else:
            level = "ok"
        inks.append({
            "slot": i + 1,
            "colour": colour,
            "ml": ml,
            "capacity_ml": capacity[i] if i < len(capacity) else None,
            "pouch": pouch,
            "level": level,
        })

    temps = _chunks(f.get(0x08), 2)
    width = f.get(0x07)
    state = decode_state(state_bytes[0], state_bytes[1], state_bytes[2], heaters_on)
    return {
        "model": dev.get("MDL") or dev.get("DES"),
        "firmware": dev.get("VER"),
        "state": state,
        "state_label": STATE_LABEL[state],
        "state_code": f.get(0x01, ""),
        "busy": bool(state_bytes[1] & 0x80),
        "heaters_on": heaters_on,
        "print_heater_c": temps[0] if len(temps) > 0 else None,
        "dryer_c": temps[1] if len(temps) > 1 else None,
        "media_width_mm": (int(width, 16) or None) if width else None,
        "inks": inks,
        "last_event": int(dev["LASTEVENT"], 16) if dev.get("LASTEVENT") else None,
        "raw_status": status,
    }


# ---------------------------------------------------------------- output


def format_text(st, raw=False):
    lines = [f"{st['model'] or 'Roland'}  {st['state_label']}" + ("  (busy)" if st["busy"] else "")]
    if st["state"] == "unknown":
        lines.append(f"  state code   {st['state_code']}  <- unknown, please report it")
    if st["heaters_on"]:
        lines.append(f"  heaters on   print {st['print_heater_c']} °C  dryer {st['dryer_c']} °C")
    else:
        lines.append("  heaters off")
    lines.append(f"  media        {st['media_width_mm']} mm" if st["media_width_mm"] else "  media        not set")
    ink = []
    for i in st["inks"]:
        label = f"{i['colour']}{i['slot']} "
        if i["level"] == "removed":
            label += "removed"
        else:
            label += f"{i['ml']} ml" + ("" if i["level"] == "ok" else f" ({i['level']})")
        ink.append(label)
    lines.append("  ink   " + "  ".join(ink))
    if st["last_event"] is not None:
        lines.append(f"  last event   {st['last_event']:#x}")
    if raw:
        lines.append(f"  raw STATUS   {st['raw_status']}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("host", help="printer IP address")
    ap.add_argument("--json", action="store_true", help="print JSON")
    ap.add_argument("--watch", action="store_true", help="poll every 2 s and print each change")
    ap.add_argument("--raw", action="store_true", help="include the raw STATUS string")
    ap.add_argument("--community", default="public")
    args = ap.parse_args()

    def read():
        raw = snmp_get_string(args.host, community=args.community)
        return decode(raw) if raw else None

    if not args.watch:
        try:
            st = read()
        except socket.timeout:
            sys.exit(f"{args.host}: no SNMP reply (switched off, or not a Roland VG?)")
        if st is None:
            sys.exit(f"{args.host}: no Roland status string at {OID}")
        print(json.dumps(st, indent=2) if args.json else format_text(st, args.raw))
        return

    last = None
    while True:
        try:
            st = read()
            key = (st["raw_status"], st["last_event"]) if st else ("none",)
        except (socket.timeout, OSError) as e:
            st, key = None, ("error", str(e))
        if key != last:
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            if st is None:
                print(f"{stamp}  no reply", flush=True)
            elif args.json:
                print(json.dumps({"time": stamp, **st}), flush=True)
            else:
                ink = " ".join(f"{i['colour']}{i['ml'] if i['level'] != 'removed' else '-'}" for i in st["inks"])
                line = (
                    f"{stamp}  {st['state_label']:<27} code {st['state_code']}  "
                    f"heat {st['print_heater_c']}/{st['dryer_c']}  media {st['media_width_mm'] or '-'}  "
                    f"ink {ink}  event {st['last_event']:#x}"
                )
                if args.raw:
                    line += f"\n    {st['raw_status']}"
                print(line, flush=True)
            last = key
        time.sleep(2)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
