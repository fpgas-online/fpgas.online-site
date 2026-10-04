# Tiny Tapeout boards under the registration-driven board list

Design, 2026-10-04. Not implemented. It spans four repositories; it lives here because the listing rule is the site's.

## Goal

- A Tiny Tapeout **ASIC** board appears only on tinytapeout.fpgas.online.
- A Tiny Tapeout **FPGA** board appears on tinytapeout.fpgas.online and on the site's own board list (welland.fpgas.online).
- Whether a host carries an ASIC or an FPGA board is **detected on the Pi**. Nothing is configured per Pi or per switch port.
- A board is listed only when its Pi has checked in and its boot check passed, the same gate as every other board.

## Where things stand

As of 2026-10-04 (site `64e9392`, test-designs `7ee3a5e`, infra `1e4d191`):

- The site's own list (`/fpgas/`) is registration-driven: `pibfpgas.pis.offered()` lists a Pi that is checked in and whose `fpga-verified` result is `pass`, and names it from `fpga-board-found` (`docs/verify-events.md`, "What the site reads").
- The Tiny Tapeout site is not. `ttsite.Board` rows are loaded from the infra table `tt_boards`, keyed by switch and port. A board counts as live when its row is enabled and has a port; no check-in, no verify. `ttsite` reads nothing from `fleet`.
- The same table is baked into the Pi root, where `fpgas-tt` looks up its own slug and kind by hostname, and into nginx, which proxies `/ws/board/<slug>/serial` to `10.21.<switch>.<port>`.
- `fpgas-verify` claims every USB device with vendor `2e8a` as board `tt`, variant `tt-fpga`. There is no ASIC variant, so an ASIC host is loaded with iCE40 bitstreams and can never pass.
- The discriminator already exists: `rpi-hwid tinytapeout` asks the demo board's SDK and reports `chip` (`asic` or `fpga`), `shuttle`, `mcu`, `demoboard` and `sdk`. `fpgas-verify` passes these on in `fpga-board-identified` (test-designs `docs/identity.md`, "Tiny Tapeout fields"). On the fleet today they are not read, because rpi-hwid is not installed in the Pi root.

So the table is a hand-kept inventory, keyed by placement, feeding three consumers. That is what registration replaced for every other board.

## Design

### 1. The Pi says what it carries

`fpgas-verify` decides the variant from the board, not from a table:

| The SDK says | Variant | Boot check |
|---|---|---|
| `chip` = `fpga` | `tt-fpga` | as today: pin-id, UART, SPI flash through the iCE40 |
| `chip` = `asic` | `tt-asic` | SDK version and shuttle are read and are a supported pair; the Pmod wiring test (the chip's factory test project driven from the SDK, observed on the HAT pins) |
| no answer (no SDK, read failed) | `tt` with no variant | fail, with the reason. Never guessed. |

The variant goes out in `fpga-board-found` and the shuttle and SDK in `fpga-board-identified`, as now. This needs rpi-hwid in the Pi root.

### 2. One listing function, two sites

`fleet.services` gets one function that returns, for each offered Pi, its found boards with `board`, `variant`, and (for `tt`) `shuttle` and `usb_serial` from `fpga-board-identified`. Both sites use it:

| Site | Shows a Pi when |
|---|---|
| The site's own list (`pibfpgas`) | it is offered and at least one found board is **not** `tt-asic` |
| tinytapeout.fpgas.online (`ttsite`) | it is offered and a found board is `tt`, either variant |

"Offered" keeps its meaning: newest machine on the hostname, checked in within 3 minutes, boot check passed this boot.

The upload and ping views (`pibup`, `pistat`) are shared by both hosts and gate on the same function, with the site's rule applied for the host the request came in on. Otherwise an ASIC board hidden from the site's own list would also lose them on the Tiny Tapeout host.

`BOARD_TITLES` names a `tt` board by variant ("TT FPGA", "TT ASIC") instead of calling every `tt` board "TT FPGA". The `/fpgas/tt.html` view stops assuming port 21.

### 3. What is left of the table

A board's place (switch, port, enabled) leaves the table. What stays is text that no Pi can detect, keyed by **what the board is**, not where it is:

- per shuttle: title, blurb, description, links, and which Commander build drives it (`legacy` for tt03p5);
- rows with no hardware: "coming soon" shuttles and the KianV entries.

A live board's page is made from its registration plus the row for its shuttle (ASIC) or the one FPGA row.

### 4. Slugs

A board's URL must not change when its cable moves.

- ASIC: the shuttle, as today (`tt06`). Two boards of one shuttle get the shuttle plus a suffix from the USB serial.
- FPGA: today `fpga-1` to `fpga-4` are port positions under another name. **Open decision** (below).

### 5. The daemon and the serial route

- `fpgas-tt` takes its kind from the same detection, not from the table: it reads the identity the boot check wrote to `/run/fpgas-online/verify.json`. Without one it serves no board and says why in `/health`; it does not fall back to "asic".
- nginx stops carrying a slug-to-address list. `/ws/board/<slug>/serial` becomes one location that asks Django (an `auth_request` sub-request) which Pi serves the slug, and proxies to the address Django returns. Django derives the address from the registered hostname, as it does for upload and ping, never from message content.

## Order of work

Each step leaves both sites working.

1. test-designs: the `tt-asic` variant and the detection in `fpgas-verify`; infra: rpi-hwid in the Pi root. Until this lands no ASIC host can pass, so nothing below can list one.
2. site: the shared listing function; the site's own list hides `tt-asic` and titles by variant. This alone meets "FPGA boards on both sites" for the FPGA half, once the FPGA boards pass their boot check.
3. site: `ttsite` lists from registration joined with the per-shuttle rows; a migration turns the table's rows into that form.
4. site and infra: the serial route through Django; infra drops switch and port from `tt_boards`.
5. fpgas-tt: kind from `verify.json`; infra stops baking the table into the Pi root.

## Open decisions

1. **FPGA board slugs.** Recommended: `fpga-` plus the last four hex digits of the RP2350's USB serial, with redirects from `fpga-1` to `fpga-4` for the four boards known today. Alternative: keep `fpga-1..4` by listing the four serials in the table, which is a hand-kept inventory again.
2. **A board that fails its boot check on the Tiny Tapeout site.** Recommended: shown as "offline", with the reason, rather than vanishing, because that site's index is a catalogue of shuttles. The site's own list keeps hiding it.
3. **Trust.** The listing believes anonymous messages on the site's broker (fpgas.online-infra#196). Nothing here makes that worse; the addresses still come from the hostname.

## Not in this design

The board-page features (site #16, #17, #18), and merging the two URL configurations into one themed site (site #19, infra #44). This design removes the second board registry, which #19 also asks for; the rest of #19 is separate.
