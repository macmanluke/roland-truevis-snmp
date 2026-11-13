/**
 * Decoder for the Roland TrueVIS VG3 device-ID string (SNMP v1, community
 * "public", OID 1.3.6.1.4.1.25816.3.2.1.1). Dependency-free: fetch the string
 * with any SNMP client and pass it to parseRolandDeviceId().
 *
 * See README.md for the meaning of each field and how it was worked out.
 *
 * Example with the `net-snmp` npm package:
 *
 *   import snmp from "net-snmp";
 *   const session = snmp.createSession("192.168.1.50", "public", { version: snmp.Version1 });
 *   session.get([ROLAND_DEVICE_ID_OID], (err, varbinds) => {
 *     if (!err) console.log(parseRolandDeviceId(varbinds[0].value.toString("latin1")));
 *     session.close();
 *   });
 */

export const ROLAND_DEVICE_ID_OID = "1.3.6.1.4.1.25816.3.2.1.1";

export type InkColour = "C" | "M" | "Y" | "K";

/** Cartridge slots left to right on a VG3 in 4-colour ×2 (CMYK + CMYK) mode. */
export const VG3_CARTRIDGE_ORDER: InkColour[] = ["M", "M", "C", "C", "Y", "Y", "K", "K"];

export const LOW_INK_ML = 50;

export type RolandState =
  | "off"
  | "asleep"
  | "cleaning"
  | "maintenance"
  | "cancelling"
  | "paused"
  | "cutting"
  | "printing"
  | "media-not-set"
  | "warming-up"
  | "ready"
  | "unknown";

export type RolandInk = {
  /** 1-based cartridge slot, left to right. */
  slot: number;
  colour: InkColour;
  ml: number | null;
  capacityMl: number | null;
  pouch: "ok" | "removed" | "unknown";
  level: "ok" | "low" | "empty" | "removed" | "unknown";
};

export type RolandStatus = {
  model: string | null;
  firmware: string | null;
  state: RolandState;
  /** Raw field 01 (b0–b3), e.g. "20008000". Report unknown codes! */
  stateCode: string;
  busy: boolean;
  heatersOn: boolean;
  printHeaterC: number | null;
  dryerC: number | null;
  mediaWidthMm: number | null;
  inks: RolandInk[];
  lastEvent: number | null;
  rawStatus: string;
};

function parseDeviceId(raw: string): Map<string, string> {
  const out = new Map<string, string>();
  for (const part of raw.split(";")) {
    const i = part.indexOf(":");
    if (i > 0) out.set(part.slice(0, i).trim(), part.slice(i + 1).trim());
  }
  return out;
}

/** STATUS = repeated [type: 2 hex][length in hex digits: 2 hex][value]. */
export function parseStatusFields(hex: string): Map<number, string> {
  const out = new Map<number, string>();
  let i = 0;
  while (i + 4 <= hex.length) {
    const type = Number.parseInt(hex.slice(i, i + 2), 16);
    const len = Number.parseInt(hex.slice(i + 2, i + 4), 16);
    if (!Number.isFinite(type) || !Number.isFinite(len)) break;
    out.set(type, hex.slice(i + 4, i + 4 + len));
    i += 4 + len;
  }
  return out;
}

function hexChunks(value: string | undefined, size: number): number[] {
  if (!value) return [];
  const out: number[] = [];
  for (let i = 0; i + size <= value.length; i += size) {
    out.push(Number.parseInt(value.slice(i, i + size), 16));
  }
  return out;
}

export function decodeState(b0: number, b1: number, b2: number, heatersOn: boolean): RolandState {
  if (b0 & 0x80) return "off"; // soft powered off
  if (b1 & 0x08) return "asleep";
  if (b2 & 0x40) return "cleaning";
  if (b0 === 0x00) return "maintenance";
  if (b0 & 0x01) return "cancelling";
  if (b0 & 0x02) return "paused";
  if (b0 & 0x08) return "cutting";
  if (b0 & 0x10) return "printing";
  if (b0 & 0x40) return "media-not-set";
  if (b0 & 0x20) return heatersOn && !(b2 & 0x80) ? "warming-up" : "ready";
  return "unknown";
}

/** Decode the device-ID string. Returns null if it has no STATUS field. */
export function parseRolandDeviceId(
  raw: string,
  cartridgeOrder: InkColour[] = VG3_CARTRIDGE_ORDER,
): RolandStatus | null {
  const id = parseDeviceId(raw);
  const hex = id.get("STATUS");
  if (!hex) return null;
  const f = parseStatusFields(hex);

  const [b0 = 0, b1 = 0, b2 = 0] = hexChunks(f.get(0x01), 2);
  const heatersOn = f.has(0x09) ? f.get(0x09) !== "00" : true;

  const pouches = (f.get(0x04) ?? "").split("");
  const capacity = hexChunks(f.get(0x05), 4);
  const remaining = hexChunks(f.get(0x06), 4);

  const inks: RolandInk[] = cartridgeOrder.map((colour, i) => {
    const pouch: RolandInk["pouch"] =
      pouches[i] === "1" ? "ok" : pouches[i] === "2" ? "removed" : "unknown";
    const ml = remaining[i] ?? null;
    const level: RolandInk["level"] =
      pouch === "removed"
        ? "removed"
        : ml == null
          ? "unknown"
          : ml <= 0
            ? "empty"
            : ml < LOW_INK_ML
              ? "low"
              : "ok";
    return { slot: i + 1, colour, ml, capacityMl: capacity[i] ?? null, pouch, level };
  });

  const width = f.get(0x07);
  const temps = hexChunks(f.get(0x08), 2);
  const lastEvent = id.get("LASTEVENT");

  return {
    model: id.get("MDL") ?? id.get("DES") ?? null,
    firmware: id.get("VER") ?? null,
    state: decodeState(b0, b1, b2, heatersOn),
    stateCode: f.get(0x01) ?? "",
    busy: (b1 & 0x80) !== 0,
    heatersOn,
    printHeaterC: temps[0] ?? null,
    dryerC: temps[1] ?? null,
    mediaWidthMm: width ? Number.parseInt(width, 16) || null : null,
    inks,
    lastEvent: lastEvent ? Number.parseInt(lastEvent, 16) : null,
    rawStatus: hex,
  };
}
