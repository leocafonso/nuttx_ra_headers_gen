# gen_ra_headers.py

Generates NuttX RA8M1 hardware headers (`ra8m1_memorymap.h` and
per-peripheral register headers like `ra8m1_system.h`/`ra8m1_mstp.h`) from
Renesas FSP support files: the CMSIS `.svd`, CMSIS `iodefine.h`, and the FSP
`.rzone` file. (The script is family-generic — the same flags work for
RA4M1's `RA4M1/` tree — but this guide targets RA8M1.)

See `PLAN_gen_ra_headers.md` for the design rationale (why address, not name,
is the join key across the three input files; why `iodefine.h` rather than
`.rzone` drives the memorymap; the register/bitfield emission rules).

## Requirements

```
pip install xmltodict
```

## Quick start

Regenerate the RA8M1 memorymap + system + mstp headers from the checked-in
FSP files:

```
python3 gen_ra_headers.py \
  --svd RA8M1/svd/R7FA8M1AH.svd \
  --zone RA8M1/zone/R7FA8M1AHECFP.rzone \
  --iodefine RA8M1/iodefine/R7FA8M1AH.h \
  --memorymap RA8M1/nuttx_hw_headers/ra8m1_memorymap.h \
  --memorymap-path arch/arm/src/ra8m1/hardware/ra8m1_memorymap.h \
  --peripheral "RA8M1/nuttx_hw_headers/ra8m1_system.h:arch/arm/src/ra8m1/hardware/ra8m1_system.h:SYSTEM:SYSTEM" \
  --peripheral "RA8M1/nuttx_hw_headers/ra8m1_mstp.h:arch/arm/src/ra8m1/hardware/ra8m1_mstp.h:MSTP:MSTP" \
  -v -o
```

Add `-o` to overwrite existing output files, `-v` for progress/cross-check
logging. `--peripheral` instance names (`SYSTEM`, `MSTP`) are the canonical
iodefine/zone names, not the SVD names — RA8M1's SVD calls `SYSTEM`'s block
`SYSC` internally, and the tool resolves that automatically by base address
(look for `[family] "SYSTEM" -> template root "SYSC"` in the `-v` output).

## Key options

| Flag | Meaning |
| --- | --- |
| `--svd` | CMSIS `.svd` file — source of registers/fields |
| `--zone` | FSP `.rzone` file — used for canonical peripheral naming and cross-checks |
| `--iodefine` | CMSIS `iodefine.h` — full-chip peripheral list; required with `--memorymap` |
| `--memorymap FILE` | Write a memorymap header (all peripheral `_BASE` addresses) |
| `--memorymap-path PATH` | Repo path recorded in the header comment / include guard |
| `--peripheral OUTFILE:PATH:FAMILYPREFIX:INSTANCES` | Render one or more peripheral instances into `OUTFILE`. Repeatable. `INSTANCES` is a comma list of `NAME` or `NAME=TEMPLATE` (for instances with no SVD entry of their own) |
| `--prefix` | C symbol prefix (default `R_`) |
| `-v` / `--verbose` | Print progress and cross-check diffs |
| `-o` / `--overwrite` | Allow overwriting existing output files |

Run `python3 gen_ra_headers.py --help` for the full list.

## Notes

- `--memorymap` requires `--iodefine`: it's the only one of the three inputs
  that lists every peripheral on the full chip (the `.rzone` file only covers
  one package variant).
- Peripheral names differ across files for the same address (e.g. RA8M1's SVD
  calls `0x4001E000` `SYSC`, while `.rzone`/`iodefine.h` call it `SYSTEM`) —
  the generator resolves this by address, not name.
- The script never modifies `svdtoheaders/` — it's a separate, standalone tool.
