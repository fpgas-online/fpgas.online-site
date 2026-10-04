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
- The Tiny Tapeout site is not. `ttsite.Board` rows (keyed by slug) are loaded from the infra table `tt_boards`, which gives each a switch and port. A board counts as live when its row is enabled and has a port; no check-in, no verify. `ttsite` reads nothing from `fleet`.
- The same table is baked into the Pi root, where `fpgas-tt` looks up its own slug and kind by hostname and falls back to `kind="asic"` when nothing matches, and into nginx, which has one exact `location = /ws/board/<slug>/serial` per row, proxying to `10.21.<switch>.<port>`.
- `fpgas-verify` claims every USB device with vendor `2e8a` as board `tt`, variant `tt-fpga`. There is no ASIC variant, so an ASIC host is loaded with iCE40 bitstreams and can never pass.
- `rpi-hwid tinytapeout` asks the demo board's SDK and reports `chip`, `shuttle`, `mcu`, `demoboard` and `sdk`; `fpgas-verify` passes them on in `fpga-board-identified` and as `board<i>_identity_*` in `fpga-verified` (test-designs `docs/identity.md`, "Tiny Tapeout fields"). On the fleet they are not read today: rpi-hwid is not installed in the Pi root.
- On 2026-10-04 the Tiny Tapeout site listed ten boards; seven of their Pis had been unpowered for over two weeks, and none of the three live ones passes its boot check.

So the table is a hand-kept inventory, keyed by placement, feeding three consumers. That is what registration replaced for every other board.

## Design

### 1. The Pi says what it carries

`fpgas-verify` decides the variant from what the demo board's SDK reports (through rpi-hwid), before it loads anything:

| The SDK says | Variant | Boot check |
|---|---|---|
| shuttle `FPGA` (`chip` = `fpga`) | `tt-fpga` | pin-id and UART through the iCE40 |
| a real shuttle name (`chip` = `asic`, `shuttle` read) | `tt-asic` | SDK release and shuttle are read; the Pmod wiring and function test |
| anything else: no SDK, `chip` null, or `asic` with no shuttle | `tt`, no variant | **fail**, with the reason. Never guessed, and no bitstream is loaded. |

Consequences to build, in fpgas.online-test-designs:

- The variant cannot be the one `spot()` hard-codes today, and it is not known when `fpga-board-found` is sent. The read has to happen at the start of the board's check, with `fpgas-tt.service` stopped, before any design is chosen. **The site therefore takes result, variant and identity from the one `fpga-verified` event** (`board<i>`, `board<i>_identity_*`), not from `fpga-board-found`. That also closes a hole: progress events stop after a broker timeout while `fpga-verified` is still sent, so a passing Pi can have no found-board events.
- One Tiny Tapeout board per Pi. A second `2e8a` device is a verify error, not a second board: the check has one port and the daemon one device.
- What "the Pmod wiring and function test" is for an ASIC board (which project per shuttle, who drives `ui_in`, which pins are observed) is its own specification, with fpgas.online-test-designs PR #15 (the TT Pmod wiring test) as its starting point. This design only requires that it exists and gives pass or fail.
- rpi-hwid has to be in the Pi root, from the fleet's package repository. Nothing in infra installs it today.

Detection limits, to be settled on hardware before anything relies on them:

- **TT04**: its ROM reads as `unknown`, so the SDK reports no shuttle unless `force_shuttle` is set in the demo board's own `config.ini`. That is identity carried on the board's flash. It moves with the board and is not a per-Pi or per-port setting, but it is declared, not detected. Open decision 3.
- **tt03p5** runs SDK 1.2.2 with a `rom_fallback.txt`. Whether rpi-hwid can read it at all is unverified.

### 2. One listing function, two sites

`fleet.services` gets one function that returns, for each offered Pi, its boards from `fpga-verified` with `board` kind, `variant`, and for Tiny Tapeout `shuttle` and `usb_serial`. Both sites use it and **filter boards, not Pis**:

| Site | Shows |
|---|---|
| The site's own list (`pibfpgas`) | every offered board except `tt-asic`. A Pi whose only board is `tt-asic` is not listed. |
| tinytapeout.fpgas.online (`ttsite`) | every offered `tt` board, either variant |

"Offered" keeps its meaning: newest machine on the hostname, checked in within 3 minutes, boot check passed this boot.

- Ping and upload (`pistat`, `pibup`) stay gated on "the Pi is offered", whatever its boards and whichever host name the request used. The host name is chosen by the client, so it can decide presentation, never access.
- `BOARD_TITLES` names a `tt` board by variant ("TT FPGA", "TT ASIC").
- `/fpgas/tt.html` (one page for "the" Tiny Tapeout Pi on port 21) is removed. Each Pi already has `<hostname>.html`.

### 3. What is left of the table

A board's place (switch, port) and `enabled` leave the table. What stays is text no Pi can detect, keyed by slug as now, matched to a live board by **kind and shuttle**:

- per ASIC shuttle and for the FPGA board: title, blurb, description, links, which Commander build drives it (`legacy` for tt03p5);
- rows with no hardware: "coming soon" shuttles and the KianV entries, marked as such in the row.

The rows keep coming from infra through `ttsite_loadboards`, as now; the loader and the `Board` schema lose the placement fields. Site and infra are deployed separately, so the loader accepts rows with and without placement during the change-over.

### 4. Slugs

A board's URL must not change when its cable moves, or when another board arrives.

- ASIC: the shuttle (`tt06`) is the board page while one board of that shuttle exists, as today. Open decision 1 covers a second board of one shuttle and the FPGA boards.

### 5. The daemon and the serial route

- `fpgas-tt` takes its kind from the same detection: it reads the identity in `/run/fpgas-online/verify.json` for the device behind `/dev/ttboard` (matched by USB serial). Without a passed identity it serves no board and says why in `/health`; the fall-back to "asic" goes. For that to be sound, `fpgas-verify` must write the report atomically and restart the services it stopped only after the report is written; today a manual `fpgas-verify --update` restarts `fpgas-tt` first.
- nginx stops carrying a slug-to-address list. `/ws/board/<slug>/serial` becomes one location with an `auth_request` to an internal location that passes the original URI to Django; Django answers 2xx with the Pi's address in a header, or 403. nginx proxies to that address only if it matches the fleet's address pattern. The address is derived from the registered hostname, as for upload and ping, never from other message content.

### 6. Trust

Today the Tiny Tapeout site's slug-to-address map is static and immune to messages on the site's broker. After this change it is not: the broker accepts anonymous messages (fpgas.online-infra#196), so anyone on the fleet network could register a Pi that claims a shuttle and so take over that shuttle's page and serial route. The site's own list already has this exposure. **Steps 4 and 5 below wait for #196.**

## Order of work

Each step leaves both sites as they were or better.

1. **site**: the shared listing function reading `fpga-verified`; the site's own list filters out `tt-asic` and titles by variant; `/fpgas/tt.html` removed. Harmless while no `tt-asic` exists, and it must be deployed before step 2 so an ASIC board never shows on the site's own list.
2. **test-designs** and **infra**: detection before loading, the `tt-asic` variant and its test, one board per Pi, atomic report and restart order; rpi-hwid in the Pi root. After this, FPGA boards that pass are on the site's own list: half of the goal.
3. **fpgas-tt**: kind from `verify.json`. Before the table loses placement, or the FPGA design gallery breaks.
4. **site** and **infra**, together, after #196: `ttsite` lists from registration joined with the rows; the serial route through Django; slugs as decided. Gated on a recorded boot-check pass on real hardware for every board the Tiny Tapeout site offers today, tt03p5 and tt04 included, so that no working board disappears.
5. **infra**: placement leaves `tt_boards`; the Pi root stops carrying the table. The `tt_boards is defined` conditions (web play, `ALLOWED_HOSTS`, the Pi-side tasks that also enable `fpgas-tt`) and the verify plays' assertions (the table in the root, one WebSocket location per live board) change with it.

Tests each step owes: the two-site listing matrix (ASIC only, FPGA only, both on one Pi, no variant, lost progress events); the serial-auth view; the loader with both row shapes; the `auth_request` route in infra's virtual-fleet test; and a run on fleet hardware recorded in the PR.

## Open decisions

1. **Slugs for FPGA boards and for a second board of one shuttle.** `fpga-1` to `fpga-4` are port positions under another name. Options: (a) slug from the board's USB serial (`fpga-9a7a`), no hand-kept list, old URLs stop working; (b) keep `fpga-1..4` by listing four serials in the table, which is a hand-kept inventory again.
2. **A board that is not offered, on the Tiny Tapeout site.** That site's index is a catalogue of shuttles. Either a shuttle with a row but no offered board is shown as "offline" (which needs the site to remember the last board seen per shuttle), or it is shown like "coming soon", or not at all. The rule "a board appears only after verify passes" read strictly says: not offered means no board page.
3. **TT04's declared shuttle** (above): accept `force_shuttle` on the demo board as the board's identity, or treat a board whose chip cannot identify itself as unverifiable.
4. **An FPGA board on both sites has two front ends**: the Tiny Tapeout page drives it through `fpgas-tt` and the Commander, the site's own page through ssh and upload. Whether both may be active on one board at once is not decided here.

## Not in this design

The board-page features (site #16, #17, #18), and merging the two URL configurations into one themed site (site #19, infra #44). This design removes the second board registry, which #19 also asks for; the rest of #19 is separate. The Tiny Tapeout page's power button requires `switch == 1` today and so never shows for these boards; that is site #17.
