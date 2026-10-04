# Tiny Tapeout boards under the registration-driven board list

Design, 2026-10-04. Not implemented. It spans four repositories; it lives here because the listing rule is the site's.

## Goal

- A Tiny Tapeout **ASIC** board appears only on tinytapeout.fpgas.online.
- A Tiny Tapeout **FPGA** board appears on tinytapeout.fpgas.online and on the site's own board list (welland.fpgas.online).
- Whether a host carries an ASIC or an FPGA board is **detected on the Pi**. Nothing is configured per Pi or per switch port.
- A board is offered only when its Pi has checked in and its boot check passed, the same gate as every other board.

## Where things stand

As of 2026-10-04 (site `64e9392`, test-designs `8f478b3`, infra `1e4d191`, fpgas-tt `cb896cb`):

- The site's own list (`/fpgas/`) is registration-driven: `pibfpgas.pis.offered()` lists a Pi that is checked in and whose `fpga-verified` result is `pass`, and names it from `fpga-board-found` (`docs/verify-events.md`, "What the site reads").
- The Tiny Tapeout site is not. `ttsite.Board` rows (keyed by slug) are loaded from the infra table `tt_boards`, which gives each a switch and port. A board counts as live when its row is enabled and has a port; no check-in, no verify. `ttsite` reads nothing from `fleet`. Its board pages, design API and serial route all take the Pi's address from the row's switch and port.
- The same table is baked into the Pi root, where `fpgas-tt` looks up its own slug and kind by hostname and falls back to `kind="asic"` when nothing matches, and into nginx, which has one exact `location = /ws/board/<slug>/serial` per row, proxying to `10.21.<switch>.<port>`.
- `fpgas-verify` claims every USB device with vendor `2e8a` as board `tt`, variant `tt-fpga`. There is no ASIC variant, so an ASIC host is loaded with iCE40 bitstreams and can never pass.
- `rpi-hwid tinytapeout` asks the demo board's SDK and reports `chip`, `shuttle`, `mcu`, `demoboard` and `sdk`; `fpgas-verify` passes them on in `fpga-board-identified` and as `board<i>_identity_*` in `fpga-verified` (test-designs `docs/identity.md`, "Tiny Tapeout fields"). On the fleet they are not read today: rpi-hwid is not installed in the Pi root, and without it the board is not failed.
- Three components disagree on what a Tiny Tapeout board is: `fpgas-verify` takes any `2e8a` product, rpi-hwid only `2e8a:0005` whose REPL answers as the SDK, the `fpgas-tt` udev rule `2e8a:0005` and `2e8a:000f`.
- On 2026-10-04 the Tiny Tapeout site listed ten boards; seven of their Pis had been unpowered for over two weeks, and none of the three live ones passes its boot check.

So the table is a hand-kept inventory, keyed by placement, feeding three consumers. That is what registration replaced for every other board.

## Design

### 1. The Pi says what it carries

**What a Tiny Tapeout board is**, one definition for verify, the daemon's udev rule and the site: a USB device that `rpi-hwid tinytapeout` reports with kind `tinytapeout`. The check looks at the USB ids a demo board's microcontroller can show: `2e8a:0005` and `2e8a:000f` (MicroPython), and `2e8a:0003` (the RP2 boot loader). One of those that rpi-hwid does not report as `tinytapeout` is an `error` with the text "a Raspberry Pi RP2 is on USB but is not running the Tiny Tapeout firmware", not "no board found". A `2e8a` device with any other product id (a debug probe, for example) is not a candidate and is ignored.

`fpgas-verify` decides the variant from that report, with `fpgas-tt.service` stopped, before any design is chosen:

| rpi-hwid reports | Variant | Boot check |
|---|---|---|
| `chip` = `fpga` | `tt-fpga` | pin-id and UART through the iCE40 (the SPI flash test goes: the breakout has no flash, fpgas.online-test-designs#114) |
| `chip` = `asic` and a `shuttle` | `tt-asic` | SDK release and shuttle are a supported pair; the Pmod wiring and function test. Needs no bitstream package. |
| rpi-hwid not installed, or it failed, or `chip` null, or `asic` with no shuttle | none | result **`error`**, with the reason. Never guessed, nothing loaded. The report still names the board, how it was found and why it was refused. |

Consequences, in fpgas.online-test-designs:

- It stays one board module (`name = "tt"`). The variant is not known when `fpga-board-found` is sent (it carries `-` there), so **the site takes result, variant and identity from the one `fpga-verified` event** (`board<i>` is `"<board> <variant> <result>"`, plus `board<i>_identity_*`). That also closes a hole: progress events stop after a broker timeout while `fpga-verified` is still sent.
- One Tiny Tapeout board per Pi: a second device that rpi-hwid reports as `tinytapeout` is an `error`. The check has one port and the daemon one device.
- rpi-hwid becomes required for this board, from the fleet's package repository. Nothing in infra installs it today.
- The ASIC "Pmod wiring and function test" is fpgas.online-test-designs#15 (the Tiny Tapeout Pmod wiring test), a named prerequisite; its place in the boot check is specified with the verify change, not here.

Declared, not detected (open decision 3): a **TT04**'s ROM reads `unknown`, so the SDK reports a shuttle only if `force_shuttle` is set in the demo board's own `config.ini`; a **tt03p5** runs SDK 1.2.2 and takes its shuttle from a `rom_fallback.txt` on the board. Both travel with the board, not with the Pi or the port. Whether rpi-hwid can read a 1.2.2 board at all is unverified. Until decision 3 is taken, a board whose chip cannot say what it is, and which carries no such declaration, is an `error`.

### 2. One listing function, two sites

`fleet.services` gets one function that returns, for each offered Pi, its boards from `fpga-verified` with `board`, `variant`, and for a `tt` board `shuttle`, `usb_serial` and `sdk`. Both sites use it. The filters are allow-lists and work on boards, not Pis:

| Site | Shows a `tt` board when its variant is |
|---|---|
| The site's own list (`pibfpgas`) | exactly `tt-fpga` |
| tinytapeout.fpgas.online (`ttsite`) | exactly `tt-fpga` or `tt-asic` |

A `tt` board with any other variant is shown on neither and logged. Boards of other kinds are unaffected. "Offered" keeps its meaning: newest machine on the hostname, checked in within 3 minutes, boot check passed this boot.

- On the site's own host, a Pi with no board left after the filter is not in the index, and its `/fpgas/<hostname>.html` is a 404.
- Ping and upload (`pistat`, `pibup`) stay gated on "the Pi is offered", whatever its boards and whichever host name the request used. The host name is chosen by the client, so it can decide presentation, never access.
- `/fleet/` is the debugging view of machines, not a board list; it keeps showing every machine (open decision 5).
- `BOARD_TITLES` names a `tt` board by variant ("TT FPGA", "TT ASIC").
- `/fpgas/tt.html` is left alone: it belongs to the flat-scheme site and three tests use it.

### 3. What is left of the table

A board's place (switch, port), `enabled`, and the Commander build leave the table. What stays is text no Pi can detect: title, blurb, description, links, `pcb`, `pmods`, `sort_order`.

- A row says which boards it describes: `kind` and an explicit list of shuttles (the `ttgf` row lists its five; the four `fpga-N` rows become one row for the FPGA board, without per-port text).
- A live board takes the row that names its kind and shuttle. **A live, offered board with no row is still shown**, with a page built from its registration (title from the shuttle). Rows add text; they never decide whether a board exists.
- Rows with no hardware ("coming soon" shuttles, the KianV entries) are marked as such in the row.
- The Commander build follows the SDK the board reported (1.x: `legacy`), so it cannot go stale on a reflash.

The rows keep coming from infra through `ttsite_loadboards`; the loader and the `Board` schema lose the placement fields. Site and infra are deployed separately, so the loader accepts both row shapes during the change-over.

### 4. Slugs

A board's URL must not change when its cable moves, or when another board arrives.

- ASIC: the shuttle (`tt06`), while exactly one offered board reports that shuttle.
- **Collision**: when two offered boards report one shuttle, neither is served under the bare shuttle slug; each gets shuttle plus a suffix from its USB serial, and the site logs it. Open decision 1 covers whether that suffix form should be the only form, and the FPGA boards.

### 5. The daemon and the serial route

- `fpgas-tt` takes its kind from `/run/fpgas-online/verify.json`: the entry whose `usb_serial` is that of the device behind `/dev/ttboard`, **if that board's own result is `pass`**. Otherwise it serves no board and says why in `/health`; the fall-back to "asic" goes. It reads the report when it starts and again whenever `/dev/ttboard` reappears.
- For that to be sound: `fpgas-verify` writes the report atomically; the runner, not the board check, starts the stopped services again, after the report is written, and rewrites the report with an `error` if a start fails; `fpgas-tt.service` gets its own `After=fpgas-verify.service`.
- Every per-board address in `ttsite` (the daemon client, the design API, the status view, the serial route) comes from the registered hostname through one function, as upload and ping already do. Nothing takes an address from message content.
- nginx stops carrying a slug-to-address list. `/ws/board/<slug>/serial` becomes one location with an `auth_request` to an internal location that hands Django the original URI; Django answers 2xx with the Pi's address in a header, or 403. The address is one Django built from a hostname that matched the fleet pattern, so nginx needs no pattern check of its own. Authorisation happens at the WebSocket handshake only: a socket already open stays open if the board stops being offered. That is accepted.
- Order within the change: the site's view first, then the nginx location.

### 6. Trust

Variant, shuttle and USB serial are **asserted by the Pi**, and visitors have root on every Pi. Today the Tiny Tapeout site's map of slug to address is static and immune to that. After this change a Pi can claim a shuttle and so get that shuttle's page and serial route pointed at itself. The collision rule in section 4 keeps a forger from silently replacing a board that is also offered, but not from claiming a shuttle whose real board is off; and a forged second claim changes the real board's URL to the suffixed form, which is a way to disrupt it.

fpgas.online-infra#196 (the broker accepts anonymous messages, so a Pi can forge another Pi's registration) does not close this: once fixed, a Pi can still lie about its own board. Whether steps 4 and 5 wait for #196, and whether this exposure is acceptable at all, is open decision 6.

## Order of work

Each step leaves both sites as they were or better.

1. **site**: the shared listing function reading `fpga-verified`; the allow-list filters; titles by variant. Works with the verify deployed today. It must be deployed before step 2 reaches the fleet, so an ASIC board never shows on the site's own list.
2. **test-designs** and **infra**: the definition of a Tiny Tapeout board, detection before loading, `tt-asic` and its tests (prerequisite: test-designs#15), one board per Pi, atomic report and restart order; rpi-hwid in the Pi root. From here a Tiny Tapeout Pi whose board cannot be identified is an `error`.
3. **fpgas-tt**: kind from `verify.json`. Gated: not deployed until every board whose daemon is serving on that day has a recorded boot-check pass on the hardware, because from this step a board without one loses its serial bridge and gallery while the Tiny Tapeout site still lists it.
4. **site**, then **infra**: `ttsite` lists from registration joined with the rows; every per-board address from the hostname; the serial route through Django; slugs per open decision 1. Same hardware gate as step 3, and subject to open decision 6.
5. **infra**, subject to open decision 6: placement leaves `tt_boards`; the Pi root stops carrying the table, while the tasks that enable `fpgas-tt` stay. `tt_boards` stays defined, so the web play and `ALLOWED_HOSTS` conditions do not change; the verify plays' assertions (the table in the root, one WebSocket location per live board) do.

Tests each step owes: the listing matrix for both hosts (a `tt-asic` Pi; a `tt-fpga` Pi; a Pi with a Tiny Tapeout board and a board of another kind; a `tt` board with no or an unknown variant; lost progress events; two boards claiming one shuttle); the serial-auth view; the loader with both row shapes; the `auth_request` route in infra's virtual-fleet test; and a run on fleet hardware recorded in the PR.

## Open decisions

1. **Slugs.** `fpga-1` to `fpga-4` are port positions under another name. (a) Slug from the board's USB serial (`fpga-9a7a`), no hand-kept list, old URLs stop working. (b) Keep `fpga-1..4` by listing four serials in the table: a hand-kept inventory again. And for ASIC boards: (a) bare shuttle while unique, as section 4; (b) always shuttle plus serial suffix, with the bare shuttle as an index of its boards.
2. **A shuttle whose board is not offered, on the Tiny Tapeout site.** (a) No board page, the index shows the shuttle like "coming soon". (b) An "offline" page, which needs the site to remember the last board seen per shuttle. "A board appears only after verify passes", read strictly, says (a).
3. **Declared identity**: accept `force_shuttle` (TT04) and `rom_fallback.txt` (tt03p5) on the demo board as that board's identity, or treat a board whose chip cannot say what it is as unverifiable.
4. **An FPGA board on both sites has two front ends**: the Tiny Tapeout page drives it through `fpgas-tt` and the Commander, the site's own page through ssh and upload. (a) Both always available; visitors may collide. (b) The site's own page shows the board and links to the Tiny Tapeout page for driving it. (c) Wait for the exclusive-use API.
5. **`/fleet/`** on the site's own host lists every machine, ASIC hosts included. (a) Exempt as a debugging view. (b) Filtered like the board list.
6. **Trust** (section 6): (a) go ahead; a forged shuttle claim is the same class of risk the site's own list already carries. (b) Steps 4 and 5 wait for infra#196 and a decision on Pi-asserted identity. (c) Keep a static map from shuttle to board identity for the Tiny Tapeout site, which is a hand-kept inventory keyed by board, not by port.

## Not in this design

The board-page features (site #16, #17, #18), and merging the two URL configurations into one themed site (site #19, infra #44). This design removes the second board registry, which #19 also asks for; the rest of #19 is separate. The Tiny Tapeout page's power button requires `switch == 1` today and so never shows for these boards; that is site #17.
