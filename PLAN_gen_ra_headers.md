# RA8M1 memorymap + system header generator — implementation plan

## Context

`RA8M1/nuttx_hw_headers/` is the generator's **output folder** (per the file
structure documented in `.claude/.claude.md`) — it's currently empty; an earlier
copy-paste of the RA4M1 headers into it was a mistake and has been removed. The
actual NuttX source tree (`arch/arm/src/ra8m1/hardware/` in the `nuttx` repo) is
still a straight copy-paste of `arch/arm/src/ra4/hardware/` (confirmed byte-identical
for `ra4m1_memorymap.h`) — that's the real target this generator's output is meant to
eventually replace, once reviewed. RA8M1 (Cortex-M85 + TrustZone) is different enough
from RA4M1 (Cortex-M4, no TZ) that hand-editing the copy doesn't scale. Goal: a
generator that produces correct `ra8m1_memorymap.h`, `ra8m1_system.h`, and
`ra8m1_mstp.h` from the FSP support files (`.rzone`, CMSIS `.svd`, `iodefine.h`),
writing into `RA8M1/nuttx_hw_headers/`.

Note: `supportFiles/ra_peripherals.md` is the user's own in-progress manual
cross-reference table (address → RA4M1 name vs RA8M1 name), currently just one row
(`0x4001E000 | SYSTEM | SYSC`) — independent confirmation of the name-divergence
finding below, not something this script needs to read or write.

**Constraint: do not modify `supportFiles/svdtoheaders/svdtoheaders`.** It's a
separate git-tracked tool. Reuse is by *calling* it (subprocess) if useful, not by
importing or editing it — it isn't structured as an importable module anyway (no
`if __name__ == "__main__"` guard; importing it would execute `main()` immediately).
New script: **`supportFiles/gen_ra_headers.py`** (top-level, standalone, own SVD/zone
parsing — small helpers like `cleanse()`/dim-index parsing are duplicated rather than
shared, since they're a few lines each).

## Findings that drive the design (verified against the actual files)

- **Address is the only reliable cross-file join key.** The RA8M1 SVD renames the
  `SYSTEM` peripheral (zone name, `iodefine.h` name, base `0x4001e000`) to `SYSC`
  internally — same base address, different `<name>`. A `DAC120`-named SVD block at
  `0x40332000` is the zone's `ADC0` (mode-alias). Peripheral **names cannot be
  trusted to match** across `.rzone` / `.svd` / `iodefine.h`; addresses can.
- **`.rzone`** (`<resources><peripherals>`, both bare `<peripheral>` and
  `<peripheral>` nested in `<group>`) is the authoritative name+address list for
  memorymap generation — it's what NuttX code actually expects the symbols to be
  named. Verified via `xmltodict`: RA8M1's zone has 36 bare peripherals + 26 groups
  (nested peripherals, e.g. `ACMPHS` group → `ACMPHS0`/`ACMPHS1`). No `_NS`-suffixed
  entries appear in `<peripherals>` — only `<memories>` has secure/non-secure pairs
  (`RAM`/`RAM_NS` etc.), so the peripheral list needs no TZ filtering.
- **`.svd`** is the register/field source. RA8M1 fields consistently use `lsb`/`msb`
  (not `bitOffset`/`bitWidth`). Register `size` attribute (8/16/32) gives the
  `(N-bits)` comment NuttX headers include. `SYSC` has 121 registers, `MSTP` has 5
  (`MSTPCRA`–`E`, all in one peripheral now — RA4M1 split `MSTPCRA` into `SYSTEM` and
  put `B/C/D` in a separate `MSTP` block). Array registers use `dim`/`dimIncrement`/
  `dimIndex` with `%s` in the name (e.g. `PVD%sCR1`, dim=2) — same pattern the
  existing `svdtoheaders` script's `parse_dim_index()` already handles; reimplement
  the same behavior here.
- **`iodefine.h`** is a cross-check oracle, not a primary source. Every peripheral
  line matches `#define R_<NAME>_BASE (0x<hex>UL + BASE_NS_OFFSET)`; the hex literal
  is the **secure** address and matches the SVD `<baseAddress>` / zone `start`
  exactly (spot-checked `ADC0`, `ACMPHS0`, `BUS`, `MSTP`). `BASE_NS_OFFSET` is 0 in a
  secure build.
- **User decisions already confirmed** (from the earlier plan-mode round):
  memorymap emits **secure addresses only** (no `_NS` defines); `ra8m1_system.h`
  generated from `SYSC`, `ra8m1_mstp.h` generated from `MSTP` — kept as separate
  files, mirroring RA4M1's split.
- **Output format precedent** (read in full from `RA4M1/nuttx_hw_headers/
  ra4m1_mstp.h` and `ra_system.h`, 94 and 581 lines): these are near-complete
  per-register dumps, not a hand-curated subset as originally assumed. Format rules,
  reverse-engineered line by line:
  - Single-bit field → `#define R_<P>_<REG>_<FIELD>   (1 << N) /* {N:02x}: {description} */`
    — the hex comment uses Python's `{value:02x}` formatting (2-digit minimum, no
    forced 8-digit padding: `01`, `10`, `4000`, `80000000` all appear as-is).
  - Multi-bit field → `_SHIFT` + `_MASK` pair. **The hand-written file is
    internally inconsistent** about whether `_MASK` is pre-shifted
    (`SCKDIVCR_FCK_MASK = (0x07 << ..._SHIFT)`) or raw
    (`BKRACR_BKRACS_MASK = (0x07)`, `VBTCR2_VBTLVDLVL_MASK = (3)`). Not worth
    replicating an inconsistency — **generator always emits the raw (unshifted)
    mask**, one consistent rule.
  - Fields named `Reserved` are skipped entirely (matches existing `svdtoheaders`
    behavior).
  - Multi-bit fields with `enumeratedValues` (e.g. `SCKDIVCR_FCK` → `_DIV_1`.._DIV_64
    from `/1`../64` descriptions) get named per-value constants in the hand-written
    file. Reproducing hand-picked names exactly isn't reliably automatable, so:
    generate a name from the enumeratedValue's `description` when it matches a clean
    pattern (`^/(\d+)$` → `DIV_<n>`, else a short sanitized description), falling
    back to the enumeratedValue's own `<name>` if the description isn't usable.
    Skip entries with no `<value>` (catch-all `isDefault`/"others" placeholders).
    This only affects the *identifier name* — the numeric value is always correct
    either way, so a less-pretty auto name is a cosmetic issue, not a correctness
    one. Flag with a `-v` log line so it's easy to spot and rename by hand later.
  - Array/dim registers: one `_OFFSET` (base offset, not multiplied), an address
    macro `NAME(p) = (BASE + OFFSET + p*increment)`, and a `_SIZE` define near the
    bitfield section — mirrors the existing `registers()` dim handling in
    `svdtoheaders`, reimplemented (not imported) per the no-edit constraint.
  - `ra8m1_system.h`/`ra8m1_mstp.h` do **not** redefine `R_<NAME>_BASE` themselves
    (unlike `svdtoheaders`'s current `registers()`, which does) — they `#include
    "hardware/ra_memorymap.h"` and reference the base symbol from there. Generator
    follows the target convention, not `svdtoheaders`'s.
  - License header block, include guard, and section banners
    (`/* Register Offsets */` etc.) copied verbatim in style from
    `ra4m1_memorymap.h`. The two existing hand-written files disagree with each
    other on include-guard naming (`__ARCH_ARM_SRC_RA_HARDWARE_RA4M1_MEMORYMAP_H` vs
    `__ARCH_ARM_SRC_RA4M1_HARDWARE_RA4_MSTP_H`, which doesn't even match its own
    filename — looks like a copy/paste artifact). Generator uses **one consistent
    rule**: guard and path comment both derived mechanically from the output file's
    intended repo path, not replicated per-file inconsistency.

## Design

### `zone_peripherals(zone_doc)`
Flatten `<rzone><resources><peripherals>` (bare `<peripheral>` + `<group>/<peripheral>`)
into `[{name, start:int, size:int, info}]`. This list is authoritative for memorymap
generation — iterate zone, not SVD.

### `svd_index_by_address(svd_doc)`
`{base_address:int -> [peripheral_node, ...]}` (list because of mode-aliases like
`ADC120`/`ADC0` sharing an address). Used to resolve a `--peripheral SYSC` CLI
argument (SVD name, since that's what carries registers) to its canonical zone name
(`SYSTEM`) by address lookup — this *is* the address-keyed join the earlier
comparison called for, applied concretely: look up SYSC's base address in the SVD,
find the zone peripheral at that same address, use the zone's name for all emitted
`R_<name>_...` symbols. Falls back to the SVD name with a `-v` warning if no zone
peripheral shares that address (shouldn't happen for SYSC/MSTP, but keeps the tool
honest for future peripherals).

### `iodefine_bases(path)`
Regex-scan `#define R_(\w+)_BASE \(0x([0-9A-Fa-f]+)UL` → `{name: int}`. After
generating the zone-derived memorymap, diff every zone `(name, address)` against this
map and print mismatches (missing name, or address disagreement) — validation only,
does not gate output.

### `emit_memorymap(zone_list, family, path_comment)`
One `#define R_<NAME>_BASE  0x<addr>UL` per zone peripheral entry, sorted, wrapped in
the license header + include guard + `Registers Base Addresses` section, matching
`ra4m1_memorymap.h` structure exactly (including the trailing `__ASSEMBLY__`/
`extern "C"` boilerplate block, kept for structural parity even though it's
currently empty in the precedent file).

### `emit_peripheral(svd_peripheral_node, canonical_name, family, path_comment)`
Register Offsets / Register Addresses / Register Bitfield Definitions sections per
the format rules above, wrapped in license header + include guard +
`#include "chip.h"` / `#include "hardware/ra_memorymap.h"`.

### CLI
```
python3 gen_ra_headers.py \
  --svd RA8M1/svd/R7FA8M1AH.svd \
  --zone RA8M1/zone/R7FA8M1AHECFP.rzone \
  --iodefine RA8M1/iodefine/R7FA8M1AH.h \
  --prefix R_ \
  --memorymap RA8M1/nuttx_hw_headers/ra8m1_memorymap.h \
  --memorymap-path arch/arm/src/ra8m1/hardware/ra8m1_memorymap.h \
  --peripheral "SYSC:RA8M1/nuttx_hw_headers/ra8m1_system.h:arch/arm/src/ra8m1/hardware/ra8m1_system.h" \
  --peripheral "MSTP:RA8M1/nuttx_hw_headers/ra8m1_mstp.h:arch/arm/src/ra8m1/hardware/ra8m1_mstp.h" \
  -v -o
```
`--peripheral` is repeatable, `NAME:OUTFILE:PATHCOMMENT` (colon-separated — paths in
this repo don't contain colons). `NAME` is the SVD peripheral name; the emitted
symbol prefix is resolved to the zone's canonical name via the address join above.
Include guard is derived mechanically from `PATHCOMMENT` (uppercase, non-alnum → `_`,
wrapped in `__..._H`) rather than hand-specified, so there's no guard/filename
mismatch like the existing hand-written files have.

## Correction found during self-check (2026-09-11)

The plan originally treated `.rzone` as the memorymap's authoritative peripheral
list and `iodefine.h` as a cross-check only. **Running the generator against
RA4M1 and diffing against the hand-written files disproved that**: `.rzone` is
scoped to one physical *package variant* and is missing peripherals the hand file
has (e.g. the `3CFP` zone file has no `ADC1`, `SCI3`–`8`, `GPT8`–`13`,
`DMAC4`–`7` — none of RA4M1's 7 zone variants have `ADC1` at all). `iodefine.h`
covers the full chip and matched the hand-written `ra4m1_memorymap.h` at 105/106
symbols with identical addresses for every shared name.

**Fix applied**: `--memorymap` now requires `--iodefine` and iterates its
`R_*_BASE` defines as the primary list; the cross-check direction inverted (verify
each iodefine entry against the SVD for register detail and against the zone file
for name agreement, not the reverse). `.rzone` parsing wasn't wasted — it's still
what `--peripheral` resolution falls back to for canonical naming, and it's still
the right source for memory regions / secure-NS pairing (unchanged, still out of
scope this pass). Full result of the corrected self-check below.

## Verification (completed against RA4M1, 2026-09-11)

Ran `gen_ra_headers.py` against `RA4M1/svd/R7FA4M1AB.svd` +
`RA4M1/zone/R7FA4M1AB3CFP.rzone` + `RA4M1/iodefine/R7FA4M1AB.h`
(`--peripheral SYSTEM:...`, `--peripheral MSTP:...`) and diffed against the
existing hand-written `ra4m1_memorymap.h` / `ra4m1_mstp.h` / `ra_system.h`:

- **memorymap addresses**: 100% match on every shared symbol (106 iodefine
  entries). Only diffs: `OFS` (hand-only — a flash memory region the human added
  by hand, not a register peripheral, so absent from iodefine's list) and
  `SPI2`/`WDT1` (generator-only — iodefine lists them, but no RA4M1 zone variant
  physically has them; left in per the decision below rather than filtered).
- **MSTP register offsets/addresses**: 100% match.
- **MSTP single-bit field bit-positions**: 100% match. Only diff is the
  *identifier name*: SVD field names are generic (`MSTPB2`, `MSTPD19`), while the
  hand file renamed them to abbreviations (`CAN`, `AGT1`) sourced from the field
  description ("Controller Area Network Module Stop" → `CAN`). This needs a
  domain abbreviation dictionary to automate and isn't attempted — values are
  correct, renaming the identifier is a fast manual pass.
- **SYSTEM register offsets and single-bit fields**: 100% match.
- **SYSTEM multi-bit `_SHIFT` values**: 100% match, plus the generator correctly
  emits 2 fields (`TRCKCR_TRCK`, `VBTBKR_VBTBKR`) that the hand file left as
  unconverted raw output from the *old* `svdtoheaders` tool (literal
  `(width << shift)` instead of `(1 << shift)`/`_SHIFT`+`_MASK`) — i.e. the
  generator is more internally consistent than the file it's mimicking here.
- **Enumerated-value constant derivation** (the `/1`→`DIV_1` heuristic): verified
  exact match against `R_SYSTEM_SCKDIVCR_FCK_DIV_1`..`_DIV_64` in the hand file,
  same values, same names.

No further generator changes made as a result — this confirms the design and the
mask/SHIFT conventions documented above. Proceeding to generate RA8M1 output.

## Remaining checks for RA8M1

1. `--iodefine`-driven cross-check output for RA8M1 — read it, don't just check
   it's zero mismatches (RA8M1's iodefine has TrustZone `+ BASE_NS_OFFSET` on every
   line, already handled by `iodefine_bases()`'s regex, but worth eyeballing).
2. **Peripheral count**: generated `ra8m1_memorymap.h` should have one `_BASE`
   per RA8M1 iodefine peripheral entry (expect noticeably more than the 132-entry
   zone-file count, per the RA4M1 lesson above).
3. **Spot values**: `R_SYSTEM_BASE == 0x4001e000UL`, `R_MSTP_BASE == 0x40203000UL`,
   `R_SYSTEM_MSTPCRA` should **not** appear in `ra8m1_system.h` (unlike RA4M1 — it's
   fully unified into `MSTP` for RA8M1, confirmed no `MSTPCR*` under RA8M1's `SYSC`).

## Out of scope for this pass (noted, not forgotten)

- `_NS` addressing / `BASE_NS_OFFSET` macro emission.
- Memory region (`RAM`/`FLASH`/etc.) linker-script generation from `<memories>`.
- The ~40 new RA8M1 peripherals beyond `SYSC`/`MSTP` (`CANFD`, `SDHI`, `USB_HS`,
  `SCI_B`, etc.) and the fact that `SCI` → `SCI_B` is a register-layout change that
  will break the copied `ra_serial.c` driver — a driver problem, not a header-gen
  problem, tracked separately.
- Manually adding the `#elif defined(CONFIG_RA8M1_FAMILY)` branch to
  `arch/arm/src/ra4/hardware/ra_memorymap.h` / `ra_mstp.h` (and giving
  `ra_system.h` the same generic-wrapper treatment it currently lacks — it's
  presently flat RA4M1 content instead of a wrapper + family file like its
  siblings). Small manual edit once the RA8M1 Kconfig symbol exists; not this
  script's job.
- **Index-parameterized address macros for evenly-strided families.** The
  family generator currently always emits one named address symbol per
  instance (`R_PORT0_PCNTR1`, `R_PORT5_PCNTR1`, ...). For a family where every
  instance is at a fixed stride from the first (confirmed for RA4M1's PORT:
  0x20 apart) and a driver wants to index by a runtime port/channel number,
  the existing hand-written `ra_gpio.h` instead defines a single macro taking
  a runtime index, e.g. `R_PORT_PCNTR1(port) = (R_PORT0_BASE + (port)*
  R_PORT_OFFSET + R_PORT_PCNTR1_OFFSET)`. A future option (e.g.
  `--peripheral ...:indexed` or a separate flag) could detect a constant
  stride across a family's instance addresses and additionally emit this
  macro form alongside (or instead of) the per-instance symbols. Not
  implemented -- the current per-instance output is correct and usable via
  its `_OFFSET` symbols even without this, just not the idiom `ra_gpio.h`
  happens to use for PORT specifically. Worth doing if/when a driver
  (`ra_gpio.c`-equivalent for RA8M1) actually needs the indexed form.
