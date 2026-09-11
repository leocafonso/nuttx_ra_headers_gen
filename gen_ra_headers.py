#!/usr/bin/env python3
"""
Generate NuttX RA-family hardware headers (memorymap + per-peripheral register
headers, e.g. system/mstp) from Renesas FSP support files: the CMSIS .svd, the
CMSIS iodefine.h, and (for memory regions / secure-NS info) the FSP .rzone file.

Design notes / constraints are in PLAN_gen_ra_headers.md. Key point: peripheral
*names* are not consistent across the three input files for the same block
(e.g. RA8M1's SVD calls 0x4001E000 "SYSC", while the .rzone file and
iodefine.h call it "SYSTEM") -- base address is the only reliable join key.

iodefine.h, not the .rzone file, is the memorymap's primary source: a self-check
against the hand-written RA4M1 headers showed the .rzone file is scoped to one
physical package variant (missing e.g. ADC1, SCI3-8, GPT8-13 on the 3CFP part),
while iodefine.h covers the full chip and matches the hand-written memorymap
almost symbol-for-symbol (105/106). The .rzone file is still the right source
for memory regions and secure/non-secure pairing, just not peripheral bases.

This script does not modify or import svdtoheaders; it is a standalone tool.
"""

import argparse
import os.path
import re
import sys

import xmltodict

BORDER_TOP = '/' + '*' * 76
BORDER_BOTTOM = ' ' + '*' * 76 + '/'

LICENSE_LINES = [
    'Licensed to the Apache Software Foundation (ASF) under one or more',
    'contributor license agreements.  See the NOTICE file distributed with',
    'this work for additional information regarding copyright ownership.  The',
    'ASF licenses this file to you under the Apache License, Version 2.0 (the',
    '"License"); you may not use this file except in compliance with the',
    'License.  You may obtain a copy of the License at',
    '',
    '  http://www.apache.org/licenses/LICENSE-2.0',
    '',
    'Unless required by applicable law or agreed to in writing, software',
    'distributed under the License is distributed on an "AS IS" BASIS, WITHOUT',
    'WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.  See the',
    'License for the specific language governing permissions and limitations',
    'under the License.',
]

COLUMN = 34


# ---------------------------------------------------------------------------
# Small text helpers
# ---------------------------------------------------------------------------

def cleanse(text):
    """Collapse internal whitespace, as SVD descriptions often wrap lines."""
    return ' '.join((text or '').split())


def as_list(node):
    if node is None:
        return []
    return node if isinstance(node, list) else [node]


def sanitize_ident(text):
    ident = re.sub(r'[^0-9A-Za-z]+', '_', text.strip())
    ident = ident.strip('_').upper()
    if not ident:
        ident = 'X'
    if ident[0].isdigit():
        ident = 'V' + ident
    return ident


def derive_guard(repo_path):
    stem = os.path.splitext(repo_path)[0]
    ident = re.sub(r'[^0-9A-Za-z]+', '_', stem.upper()).strip('_')
    return f'__{ident}_H'


def file_header(path_comment):
    lines = [BORDER_TOP, f' * {path_comment}', ' *']
    for line in LICENSE_LINES:
        lines.append((' * ' + line).rstrip())
    lines.append(' *')
    lines.append(BORDER_BOTTOM)
    return '\n'.join(lines)


def section_banner(title):
    lines = [BORDER_TOP, f' * {title}', BORDER_BOTTOM]
    return '\n'.join(lines)


def subbanner(title):
    prefix = f'/* {title} '
    stars = '*' * max(1, 78 - len(prefix))
    return f'{prefix}{stars}/'


def clobber_ok(path, overwrite):
    if overwrite or not os.path.isfile(path):
        return True
    print(f'Unable to overwrite existing file "{path}" (use -o to allow).')
    sys.exit(1)


# ---------------------------------------------------------------------------
# .rzone parsing
# ---------------------------------------------------------------------------

def read_zone(path):
    with open(path) as fp:
        return xmltodict.parse(fp.read())


def zone_peripherals(zdoc):
    """Flatten <rzone><resources><peripherals> (bare + grouped) into a list
    of {name, start, size, info} -- this is the authoritative NuttX-facing
    peripheral name/address list, not the SVD's peripheral list."""
    root = zdoc['rzone']['resources']['peripherals']
    flat = []
    for p in as_list(root.get('peripheral')):
        flat.append({
            'name': p['@name'],
            'start': int(p['@start'], 0),
            'size': int(p['@size'], 0),
            'info': p.get('@info', ''),
        })
    for group in as_list(root.get('group')):
        for p in as_list(group.get('peripheral')):
            flat.append({
                'name': p['@name'],
                'start': int(p['@start'], 0),
                'size': int(p['@size'], 0),
                'info': p.get('@info', group.get('@info', '')),
            })
    return flat


# ---------------------------------------------------------------------------
# SVD parsing
# ---------------------------------------------------------------------------

def read_svd(path):
    with open(path) as fp:
        return xmltodict.parse(fp.read())


def svd_peripherals(sdoc):
    return as_list(sdoc['device']['peripherals']['peripheral'])


def svd_index_by_name(sdoc):
    return {p['name']: p for p in svd_peripherals(sdoc) if 'name' in p}


def svd_index_by_address(sdoc):
    index = {}
    for p in svd_peripherals(sdoc):
        if 'baseAddress' not in p:
            continue
        index.setdefault(int(p['baseAddress'], 0), []).append(p)
    return index


def field_bits(field):
    """Returns (lsb, width). RA SVDs use lsb/msb; fall back to
    bitOffset/bitWidth for other vendors' SVDs."""
    if 'lsb' in field and 'msb' in field:
        lsb = int(field['lsb'])
        msb = int(field['msb'])
    else:
        lsb = int(field.get('bitOffset', 0))
        width = int(field.get('bitWidth', 1))
        msb = lsb + width - 1
    return lsb, msb - lsb + 1


def enum_value(ev):
    value = ev.get('value')
    if value is None:
        return None
    value = value.strip()
    if value.startswith('#'):
        bits = value[1:]
        if not bits or any(c not in '01' for c in bits):
            return None
        return int(bits, 2)
    try:
        return int(value, 0)
    except ValueError:
        return None


def enum_label(ev):
    desc = cleanse(ev.get('description', ''))
    match = re.match(r'^/(\d+)$', desc)
    if match:
        return f'DIV_{match.group(1)}'
    if desc and len(desc) <= 24:
        return sanitize_ident(desc)
    return sanitize_ident(ev.get('name', ''))


# ---------------------------------------------------------------------------
# iodefine.h cross-check
# ---------------------------------------------------------------------------

IODEFINE_BASE_RE = re.compile(r'#define\s+R_(\w+)_BASE\s+\(?0x([0-9A-Fa-f]+)UL')


def iodefine_bases(path):
    bases = {}
    with open(path) as fp:
        for line in fp:
            match = IODEFINE_BASE_RE.search(line)
            if match:
                bases[match.group(1)] = int(match.group(2), 16)
    return bases


def cross_check_memorymap(iodefine_map, svd_addr_index, zone_flat, verbose):
    """iodefine.h is the primary source for the memorymap; verify each of its
    entries against the SVD (register detail source) and the zone file (name
    agreement), rather than the other way around."""
    zone_by_addr = {}
    for entry in zone_flat:
        zone_by_addr.setdefault(entry['start'], []).append(entry['name'])

    no_svd = 0
    name_disagreement = 0
    for name, addr in iodefine_map.items():
        if addr not in svd_addr_index:
            no_svd += 1
            if verbose:
                print(f'[svd] "{name}" ({hex(addr)}) has no SVD peripheral '
                      f'at that address')
        zone_names = zone_by_addr.get(addr, [])
        if zone_names and name not in zone_names:
            name_disagreement += 1
            if verbose:
                print(f'[zone] "{name}" ({hex(addr)}) vs zone name(s) '
                      f'{zone_names}')
    if verbose:
        print(f'[cross-check] {len(iodefine_map)} iodefine peripherals: '
              f'{no_svd} without an SVD register block, '
              f'{name_disagreement} with a differing zone name')


# ---------------------------------------------------------------------------
# memorymap.h generation
# ---------------------------------------------------------------------------

def emit_memorymap(iodefine_map, prefix, path_comment):
    guard = derive_guard(path_comment)
    lines = [file_header(path_comment), '', f'#ifndef {guard}', f'#define {guard}', '']
    lines.append(section_banner('Included Files'))
    lines.append('')
    lines.append('#include <nuttx/config.h>')
    lines.append('')
    lines.append(section_banner('Pre-processor Definitions'))
    lines.append('')
    lines.append('/* Registers Base Addresses */')
    lines.append('')

    defines = []
    for name, addr in iodefine_map.items():
        sym = f'{prefix}{name}_BASE'
        define = f'#define {sym:<{COLUMN}} 0x{addr:08X}UL'
        defines.append((sym, define))
    defines.sort(key=lambda item: item[0])
    lines.extend(define for _, define in defines)

    lines.append('')
    lines.append(section_banner('Public Types'))
    lines.append('')
    lines.append('#ifndef __ASSEMBLY__')
    lines.append('')
    lines.append(section_banner('Public Data'))
    lines.append('')
    lines.append('#undef EXTERN')
    lines.append('#if defined(__cplusplus)')
    lines.append('#define EXTERN extern "C"')
    lines.append('extern "C"')
    lines.append('{')
    lines.append('#else')
    lines.append('#define EXTERN extern')
    lines.append('#endif')
    lines.append('')
    lines.append(section_banner('Public Function Prototypes'))
    lines.append('')
    lines.append('#undef EXTERN')
    lines.append('#if defined(__cplusplus)')
    lines.append('}')
    lines.append('#endif')
    lines.append('')
    lines.append('#endif /* __ASSEMBLY__ */')
    lines.append('')
    lines.append(f'#endif /* {guard} */')
    lines.append('')
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# Peripheral register header generation (system.h / mstp.h / sci.h style)
#
# A "family" is one or more NuttX-facing instance names (e.g. SCI_B0..SCI_B9)
# that share a symbol prefix (e.g. "SCI") in the output. Some families have a
# single uniform register template (SYSTEM, MSTP, RA8M1's SCI_B); others have
# several template "flavors" that different instances use (RA4M1's SCI: SCI0/
# SCI1 have FIFO registers, SCI2/SCI9 don't) -- the SVD's derivedFrom chain
# says which flavor each instance uses, so that's what this code follows
# rather than assuming every instance in a family is identical.
# ---------------------------------------------------------------------------

def address_for_canonical(name, iodefine_map, zone_flat):
    """Base address for a NuttX-facing instance name, iodefine.h preferred
    (it's the memorymap's source of truth), zone file as fallback."""
    if iodefine_map and name in iodefine_map:
        return iodefine_map[name]
    for entry in zone_flat:
        if entry['name'] == name:
            return entry['start']
    return None


def resolve_derived_root(node, svd_by_name, verbose, max_depth=10):
    """Follow a peripheral's @derivedFrom chain to the node that actually
    owns <registers> -- that's the register/field template this instance
    uses. Returns (root_name, root_node)."""
    seen = set()
    current = node
    for _ in range(max_depth):
        name = current['name']
        if name in seen:
            print(f'ERROR: derivedFrom cycle detected at "{name}".',
                  file=sys.stderr)
            sys.exit(1)
        seen.add(name)
        if current.get('registers'):
            return name, current
        derived_from = current.get('@derivedFrom')
        if not derived_from:
            return name, current
        parent = svd_by_name.get(derived_from)
        if parent is None:
            if verbose:
                print(f'[join] warning: "{name}" derivedFrom="{derived_from}" '
                      f'not found in SVD; treating "{name}" as its own template')
            return name, current
        current = parent
    print(f'ERROR: derivedFrom chain too deep starting at "{node["name"]}".',
          file=sys.stderr)
    sys.exit(1)


def parse_instance_token(token):
    """'NAME' or 'NAME=TEMPLATE' -> (name, template_name_or_None)."""
    name, sep, template = token.partition('=')
    return name.strip(), (template.strip() if sep else None)


def resolve_family_instances(family_prefix, tokens, svd_by_name, svd_addr_index,
                              iodefine_map, zone_flat, verbose):
    """For each instance token, find its own base address (for its address
    block) and the SVD root peripheral that defines its register template
    (directly by address, or via an explicit NAME=TEMPLATE override for
    instances -- e.g. RA4M1 SCI3-8 -- that have no SVD entry of their own)."""
    resolved = []
    unresolved = []
    for token in tokens:
        name, template = parse_instance_token(token)
        addr = address_for_canonical(name, iodefine_map, zone_flat)
        if addr is None:
            unresolved.append((name, 'not found in iodefine.h or .rzone'))
            continue

        lookup_name = template or name
        lookup_addr = address_for_canonical(lookup_name, iodefine_map, zone_flat)
        if lookup_addr is None:
            unresolved.append((name, f'template "{lookup_name}" not found'))
            continue

        candidates = svd_addr_index.get(lookup_addr)
        if not candidates:
            hint = '' if template else (
                ' -- this instance has no SVD entry; add an override '
                f'"{name}=<other-instance-with-same-registers>"')
            unresolved.append((name, f'no SVD peripheral at {hex(lookup_addr)}{hint}'))
            continue

        root_name, root_node = resolve_derived_root(
            candidates[0], svd_by_name, verbose)
        if verbose:
            note = f' (via override ={template})' if template else ''
            print(f'[family] "{name}"{note} -> template root "{root_name}"')
        resolved.append({
            'name': name, 'addr': addr,
            'root_name': root_name, 'root_node': root_node,
        })

    if unresolved:
        print(f'ERROR: could not resolve {len(unresolved)} instance(s) for '
              f'family "{family_prefix}":', file=sys.stderr)
        for name, reason in unresolved:
            print(f'  {name}: {reason}', file=sys.stderr)
        sys.exit(1)

    return resolved


def build_enum_constants(field, field_sym):
    lines = []
    values = field.get('enumeratedValues')
    if not values:
        return lines
    for ev in as_list(values.get('enumeratedValue')):
        value = enum_value(ev)
        if value is None:
            continue
        label = enum_label(ev)
        desc = cleanse(ev.get('description', ''))
        sym = f'{field_sym}_{label}'
        lhs = f'#  define {sym}'
        lines.append(f'{lhs:<{COLUMN + 3}} ({value} << {field_sym}_SHIFT) '
                      f'/* {desc} */'.rstrip())
    return lines


def merge_family_templates(resolved, family_prefix, prefix, verbose):
    """Build one shared offset/bitfield table across every distinct template
    root used by this family's instances, deduplicated by *register name*
    (first-seen wins), not by offset.

    Register name, not offset, is the right dedup key: several SVD registers
    legitimately share one offset as alternate views (RA4M1 SCI's TDRHL and
    FTDRHL are both real 16-bit registers at the same address -- one for
    plain mode, one for FIFO mode -- and the hand-written ra_sci.h keeps both
    as separate symbols). Deduplicating by offset would arbitrarily keep only
    one and silently drop a register a driver might need. The only thing
    actually duplicated across roots is the *same-named* register appearing
    in more than one template (e.g. "SMR" in both SCI0 and SCI2) -- that's a
    true duplicate and collapses to one entry.

    Returns (offset_lines, bitfield_lines, merged_names, offset_meta,
    root_reg_names): merged_names is the register name list in offset order;
    offset_meta maps reg_name -> (offset, descr, size, is_array, dim,
    increment); root_reg_names maps id(root_node) -> set of register names
    that root defines (used to filter each instance's address block).
    """
    seen_roots = {}
    root_order = []
    for inst in resolved:
        rid = id(inst['root_node'])
        if rid not in seen_roots:
            root_order.append(rid)
            seen_roots[rid] = (inst['root_node'],
                                as_list(inst['root_node'].get('registers', {})
                                        .get('register')))

    root_reg_names = {}
    name_owner = {}
    merged = []
    for rid in root_order:
        root_node, registers = seen_roots[rid]
        names = set()
        for reg in registers:
            reg_name = reg['name'].replace('[%s]', '').replace('%s', '')
            names.add(reg_name)
            if reg_name not in name_owner:
                name_owner[reg_name] = reg
                merged.append((reg_name, reg))
            elif verbose:
                prev_off = int(name_owner[reg_name]['addressOffset'], 0)
                new_off = int(reg['addressOffset'], 0)
                if prev_off != new_off:
                    print(f'[merge] "{family_prefix}": WARNING "{reg_name}" '
                          f'is at {hex(prev_off)} in one template but '
                          f'{hex(new_off)} in "{root_node["name"]}" -- '
                          f'using the first one seen')
        root_reg_names[rid] = names
    merged.sort(key=lambda item: int(item[1]['addressOffset'], 0))

    offset_lines = []
    bitfield_lines = []
    offset_meta = {}
    for reg_name, reg in merged:
        is_array = '%s' in reg['name']
        offset = int(reg['addressOffset'], 0)
        descr = cleanse(reg.get('description', reg_name))
        size = reg.get('size', '32')
        dim = int(reg.get('dim', '0'), 0) if is_array else 0
        increment = int(reg.get('dimIncrement', '0'), 0) if is_array else 0
        offset_meta[reg_name] = (offset, descr, size, is_array, dim, increment)

        sym = f'{prefix}{family_prefix}_{reg_name}'
        offset_sym = f'{sym}_OFFSET'
        offset_lines.append(
            f'#define {offset_sym:<{COLUMN}} 0x{offset:04x}  '
            f'/* {descr} ({size}-bits) */')

        fields = [f for f in as_list(reg.get('fields', {}).get('field'))
                  if f.get('name') != 'Reserved']
        if not fields:
            continue

        bitfield_lines.append('')
        bitfield_lines.append(subbanner(f'{descr} ({size}-bits)'))
        bitfield_lines.append('')

        if is_array and dim:
            size_sym = f'{sym}_SIZE'
            bitfield_lines.append(f'#define {size_sym:<{COLUMN}} {dim}')

        for field in fields:
            fname = field['name']
            fdescr = cleanse(field.get('description', 'N/A'))
            lsb, width = field_bits(field)
            fsym = f'{sym}_{fname}'
            if width <= 1:
                bitfield_lines.append(
                    f'#define {fsym:<{COLUMN}} (1 << {lsb:>2}) '
                    f'/* {(1 << lsb):02x}: {fdescr} */')
            else:
                mask = (1 << width) - 1
                shift_sym = f'{fsym}_SHIFT'
                mask_sym = f'{fsym}_MASK'
                bitfield_lines.append(f'#define {shift_sym:<{COLUMN}} ({lsb})')
                bitfield_lines.append(f'#define {mask_sym:<{COLUMN}} (0x{mask:x})')
                bitfield_lines.extend(build_enum_constants(field, fsym))

    merged_names = [name for name, _ in merged]
    return offset_lines, bitfield_lines, merged_names, offset_meta, root_reg_names


def build_instance_address_lines(inst, family_prefix, prefix, merged_names,
                                  offset_meta, root_reg_names):
    rid = id(inst['root_node'])
    names = root_reg_names[rid]
    base_symbol = f'{prefix}{inst["name"]}_BASE'
    lines = []
    for reg_name in merged_names:
        if reg_name not in names:
            continue
        _offset, _descr, _size, is_array, _dim, increment = offset_meta[reg_name]
        sym = f'{prefix}{family_prefix}_{reg_name}'
        offset_sym = f'{sym}_OFFSET'
        inst_sym = f'{prefix}{inst["name"]}_{reg_name}'
        if is_array:
            lhs = f'{inst_sym}(p)'
            lines.append(
                f'#define {lhs:<{COLUMN}} ({base_symbol} + {offset_sym} + '
                f'(p)*0x{increment:04x})')
        else:
            lines.append(
                f'#define {inst_sym:<{COLUMN}} ({base_symbol} + {offset_sym})')
    return lines, len(names)


def build_family_sections(family_prefix, resolved, prefix, verbose):
    offset_lines, bitfield_lines, merged_names, offset_meta, root_reg_names = \
        merge_family_templates(resolved, family_prefix, prefix, verbose)

    total = len(merged_names)
    address_lines = []
    for inst in resolved:
        lines, count = build_instance_address_lines(
            inst, family_prefix, prefix, merged_names, offset_meta,
            root_reg_names)
        note = '' if count == total else f' (subset: {count} of {total} registers)'
        address_lines.append('')
        address_lines.append(f'/* {inst["name"]} Registers{note} */')
        address_lines.append('')
        address_lines.extend(lines)

    if verbose:
        roots = {inst['root_name'] for inst in resolved}
        print(f'[gen] "{family_prefix}": {len(resolved)} instance(s), '
              f'{len(roots)} distinct template(s) ({sorted(roots)}), '
              f'{total} registers merged')

    return offset_lines, address_lines, bitfield_lines


def emit_peripheral(family_prefix, resolved, prefix, path_comment, verbose):
    guard = derive_guard(path_comment)
    offset_lines, address_lines, bitfield_lines = build_family_sections(
        family_prefix, resolved, prefix, verbose)

    lines = [file_header(path_comment), '', f'#ifndef {guard}', f'#define {guard}', '']
    lines.append(section_banner('Included Files'))
    lines.append('')
    lines.append('#include <nuttx/config.h>')
    lines.append('')
    lines.append('#include "chip.h"')
    lines.append('#include "hardware/ra_memorymap.h"')
    lines.append('')
    lines.append(section_banner('Pre-processor Definitions'))
    lines.append('')
    lines.append(subbanner('Register Offsets'))
    lines.append('')
    lines.extend(offset_lines)
    lines.append('')
    lines.append(subbanner('Register Addresses'))
    lines.append('')
    lines.extend(address_lines)
    lines.append('')
    lines.append(subbanner('Register Bitfield Definitions'))
    lines.extend(bitfield_lines)
    lines.append('')
    lines.append(section_banner('Public Types'))
    lines.append('')
    lines.append(section_banner('Public Data'))
    lines.append('')
    lines.append(section_banner('Public Functions Prototypes'))
    lines.append('')
    lines.append(f'#endif /* {guard} */')
    lines.append('')
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_peripheral_spec(spec):
    """OUTFILE:PATH:FAMILYPREFIX:INSTANCES, where INSTANCES is a comma list of
    NAME or NAME=TEMPLATE tokens (TEMPLATE for instances with no SVD entry of
    their own -- see resolve_family_instances)."""
    parts = spec.split(':', 3)
    if len(parts) != 4:
        print(f'ERROR: --peripheral expects '
              f'OUTFILE:PATH:FAMILYPREFIX:INSTANCES, got "{spec}"',
              file=sys.stderr)
        sys.exit(1)
    outfile, path_comment, family_prefix, instances = parts
    tokens = [t.strip() for t in instances.split(',') if t.strip()]
    if not tokens:
        print(f'ERROR: --peripheral "{spec}" has no instances.', file=sys.stderr)
        sys.exit(1)
    return outfile, path_comment, family_prefix, tokens


def main():
    parser = argparse.ArgumentParser(
        description='Generate NuttX RA-family memorymap/register headers '
                     'from CMSIS iodefine.h + .svd (+ .rzone for name '
                     'cross-checking and, later, memory regions).')
    parser.add_argument('--svd', required=True, help='CMSIS .svd file')
    parser.add_argument('--zone', required=True, help='FSP .rzone file')
    parser.add_argument('--iodefine',
                         help='CMSIS iodefine.h -- required for --memorymap, '
                              'since it is the full-chip peripheral list '
                              '(the .rzone file is scoped to one package '
                              'variant and is missing peripherals the '
                              'memorymap needs)')
    parser.add_argument('--prefix', default='R_', help='C symbol prefix')
    parser.add_argument('--memorymap', help='Output path for memorymap.h')
    parser.add_argument('--memorymap-path',
                         default='memorymap.h',
                         help='Repo path written into the memorymap.h header '
                              'comment and used to derive its include guard')
    parser.add_argument(
        '--peripheral', action='append', default=[],
        metavar='OUTFILE:PATH:FAMILYPREFIX:INSTANCES',
        help='Render one or more instances into OUTFILE, sharing symbol '
             'prefix FAMILYPREFIX. PATH is the repo path written into the '
             'header comment and used to derive the include guard. '
             'INSTANCES is a comma-separated list of NAME (a canonical/'
             'iodefine instance name) or NAME=TEMPLATE (for instances with '
             'no SVD entry of their own, e.g. RA4M1 SCI3-8: use another '
             'instance\'s register template). Repeatable.')
    parser.add_argument('-v', '--verbose', action='store_true')
    parser.add_argument('-o', '--overwrite', action='store_true')
    args = parser.parse_args()

    if not args.memorymap and not args.peripheral:
        print('Nothing to do: specify --memorymap and/or --peripheral.')
        sys.exit(1)

    if args.memorymap and not args.iodefine:
        print('ERROR: --memorymap requires --iodefine (it is the primary '
              'source; the .rzone file alone under-reports peripherals).',
              file=sys.stderr)
        sys.exit(1)

    if args.verbose:
        print(f'Reading zone file {args.zone}')
    zone_doc = read_zone(args.zone)
    zone_flat = zone_peripherals(zone_doc)
    if args.verbose:
        print(f'zone: {len(zone_flat)} peripherals (bare + grouped)')

    if args.verbose:
        print(f'Reading SVD file {args.svd}')
    svd_doc = read_svd(args.svd)
    svd_by_name = svd_index_by_name(svd_doc)
    svd_by_addr = svd_index_by_address(svd_doc)

    iodefine_map = None
    if args.iodefine:
        if args.verbose:
            print(f'Reading iodefine file {args.iodefine}')
        iodefine_map = iodefine_bases(args.iodefine)
        if args.verbose:
            print(f'iodefine: {len(iodefine_map)} peripheral base defines')

    if args.memorymap and clobber_ok(args.memorymap, args.overwrite):
        cross_check_memorymap(iodefine_map, svd_by_addr, zone_flat, args.verbose)
        content = emit_memorymap(iodefine_map, args.prefix, args.memorymap_path)
        with open(args.memorymap, 'w') as fp:
            fp.write(content)
        if args.verbose:
            print(f'Wrote {args.memorymap} '
                  f'({len(iodefine_map)} peripheral base defines)')

    for spec in args.peripheral:
        outfile, path_comment, family_prefix, tokens = parse_peripheral_spec(spec)
        if not clobber_ok(outfile, args.overwrite):
            continue
        resolved = resolve_family_instances(
            family_prefix, tokens, svd_by_name, svd_by_addr,
            iodefine_map, zone_flat, args.verbose)
        content = emit_peripheral(
            family_prefix, resolved, args.prefix, path_comment, args.verbose)
        with open(outfile, 'w') as fp:
            fp.write(content)
        if args.verbose:
            print(f'Wrote {outfile}')


if __name__ == '__main__':
    main()
