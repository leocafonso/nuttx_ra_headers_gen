# gen_ra_headers.py

Generates NuttX RA8M1 hardware headers (`ra8m1_memorymap.h` and
per-peripheral register headers like `ra8m1_system.h`/`ra8m1_mstp.h`) from
Renesas FSP support files: the CMSIS `.svd`, CMSIS `iodefine.h`, and the FSP
`.rzone` file. It can also draft a `Kconfig` (see "Kconfig draft" below).
(The script is family-generic — the same flags work for RA4M1's `RA4M1/`
tree — but this guide targets RA8M1.)

See `PLAN_gen_ra_headers.md` for the design rationale (why address, not name,
is the join key across the three input files; why `iodefine.h` rather than
`.rzone` drives the memorymap; the register/bitfield emission rules).

## Requirements

```
pip install xmltodict pyyaml
```

A `.venv` with both installed is already checked out at `supportFiles/.venv`
— run the script with `./.venv/bin/python`, not a bare `python3`, unless
you've installed both into your system/user interpreter yourself.

## Quick start

Regeneration is driven by a checked-in YAML manifest per part/family —
`RA8M1/peripherals.yaml` — rather than by `--peripheral` CLI flags, so a
generation run is always reproducible from git history alone (a prior
CLI-only invocation's exact `NAME=TEMPLATE` overrides was lost and couldn't
be reconstructed — see `PLAN_gen_ra_headers.md`):

```
./.venv/bin/python gen_ra_headers.py --config RA8M1/peripherals.yaml -v -o
```

Add `-o` to overwrite existing output files, `-v` for progress/cross-check
logging. Instance names in the manifest (`SYSTEM`, `MSTP`, `ICU`) are the
canonical iodefine/zone names, not the SVD names — RA8M1's SVD calls
`SYSTEM`'s block `SYSC` internally, and the tool resolves that automatically
by base address (look for `[family] "SYSTEM" -> template root(s) SYSC@0x0`
in the `-v` output). See `RA8M1/peripherals.yaml` and
`gen_ra_headers.py`'s `load_config()`/`resolve_family_instances()`
docstrings for the manifest schema, including `template` (structural clone,
for an instance with no SVD entry of its own, e.g. RA4M1 SCI3-8) and
`sources` (address splice, for an FSP struct that folds more than one SVD
peripheral into itself, e.g. RA8M1 `ICU`: SVD `ICU_COMMON` + SVD `ICU`).

## Key options

| Flag | Meaning |
| --- | --- |
| `--config FILE` | YAML manifest: svd/zone/iodefine paths, memorymap output, and every peripheral header's instance list. See `RA8M1/peripherals.yaml`. |
| `--svd` | CMSIS `.svd` file — source of registers/fields. Overrides the config value if both given. |
| `--zone` | FSP `.rzone` file — used for canonical peripheral naming and cross-checks. Overrides the config value if both given. |
| `--iodefine` | CMSIS `iodefine.h` — full-chip peripheral list; required with `--memorymap`. Overrides the config value if both given. |
| `--memorymap FILE` | Write a memorymap header (all peripheral `_BASE` addresses). Overrides the config value if both given. |
| `--memorymap-path PATH` | Repo path recorded in the header comment / include guard. Overrides the config value if both given. |
| `--memorymap-include PATH` | `#include` path every peripheral header uses for the memorymap (default `hardware/ra_memorymap.h`, RA4M1's family-generic dispatcher). Overrides the config value if both given. RA8M1 has no such dispatcher yet, so `RA8M1/peripherals.yaml` sets this to `hardware/ra8m1_memorymap.h` directly. |
| `--prefix` | C symbol prefix (default `R_`). Overrides the config value if both given. |
| `-v` / `--verbose` | Print progress and cross-check diffs |
| `-o` / `--overwrite` | Allow overwriting existing output files |

Run `./.venv/bin/python gen_ra_headers.py --help` for the full list.

## Kconfig draft

An optional `kconfig:` section in the manifest drafts a `Kconfig` from the
family's `.rzone` files: a `choice` of `ARCH_CHIP_*` part numbers, each
`select`ing `RA_HAVE_<instance>` for whichever `peripherals[].instances` its
own `.rzone` variant actually has, plus the bare `RA_HAVE_*` declarations.

This is deliberately partial — it stops before the human-facing enable
options (`config RA_SCI0_UART` with its prompt and `select
SCI0_SERIALDRIVER`, PWM/mode sub-options, etc.), which encode
driver-subsystem knowledge this generator has no source for. Write those by
hand afterward the way `RA4M1/nuttx_hw_headers/Kconfig` (a copy of the real
`arch/arm/src/ra4/Kconfig`) has them, using the generated `RA_HAVE_*` flags
as the `depends on`. See `emit_kconfig()`'s docstring for the schema and
`derive_chip_label()`'s docstring for how an `.rzone` filename becomes an
`ARCH_CHIP_*` label (the literal filename stem — confirmed against the
RA8M1 User's Manual's own part-numbering table; do not reintroduce
RA4M1-style label collapsing without separately confirming it against that
family's own docs, see the docstring for why).

A peripheral entry can set `variant_tracked: false` to exclude it from both
this and the chip.h draft below — for core singletons (SYSTEM/MSTP/ICU on
RA8M1) that are present on every part variant, where a "does this part have
it" flag/count is meaningless. See `group_family_counts()`.

## chip.h draft

An optional `chip_header:` section drafts a `chip.h` the same way, mirroring
STM32's convention (`arch/arm/include/stm32f4/chip.h`): the same
`ARCH_CHIP_*` part-number ladder as the Kconfig draft, but defining
`RA_N<PREFIX>` — a *count*, not a bool — for each tracked peripheral entry
(`RA_NSCI_B`, mirroring `STM32_NUSART`/`STM32_NSPI`). Same scope boundary:
no flash/SRAM size defines, no peripheral-IP-version defines. The NVIC
priority block is emitted verbatim (a fixed Cortex-M convention, not
derived from anything family-specific). See `emit_chip_header()`'s
docstring.

`arch/arm/include/ra8m1/chip.h` currently in the nuttx tree is a stale,
unmodified copy of `ra4/chip.h` (wrong file-header path, wrong include
guard, RA4M1-specific content, no `RA_N*` counts at all) — this draft
replaces it with something generated fresh from `.rzone` data; don't use
the old one as a reference for anything.

## Notes

- `--memorymap` requires `--iodefine`: it's the only one of the three inputs
  that lists every peripheral on the full chip (the `.rzone` file only covers
  one package variant).
- Peripheral names differ across files for the same address (e.g. RA8M1's SVD
  calls `0x4001E000` `SYSC`, while `.rzone`/`iodefine.h` call it `SYSTEM`) —
  the generator resolves this by address, not name.
- The script never modifies `svdtoheaders/` — it's a separate, standalone tool.
