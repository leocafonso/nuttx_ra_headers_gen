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
import glob
import os.path
import re
import sys
import textwrap

import xmltodict
import yaml

BORDER_TOP = '/' + '*' * 76
BORDER_BOTTOM = ' ' + '*' * 76 + '/'

# nxstyle (tools/nxstyle.c) derives its max line width from the width of the
# file's own banner comments, so every banner in a generated file must agree
# on one physical width -- BANNER_WIDTH is that width (matches BORDER_BOTTOM
# and the hand-written RA4M1 headers). MAX_LINE is the resulting content
# budget nxstyle enforces on ordinary (non right-hand-comment) lines.
BANNER_WIDTH = 78
MAX_LINE = 77

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
    """A single bordered banner line: '/* Title ****...*/'.

    All such lines in a file must share one exact physical width (nxstyle
    checks banner comments for file-wide length consistency), so the star
    count is chosen to hit BANNER_WIDTH exactly rather than padded from a
    fixed star count. If the title itself is too long for that to work,
    fall back to a plain wrapped comment -- nxstyle's width-consistency
    check only looks at bordered banner lines, so an undecorated multi-line
    comment is exempt from it (and is still subject to the ordinary
    per-line MAX_LINE limit, which the wrapping respects).
    """
    prefix = f'/* {title} '
    stars = BANNER_WIDTH - len(prefix) - 1
    if stars >= 1:
        return [f'{prefix}{"*" * stars}/']

    content_width = MAX_LINE - 3  # "/* " / " * " prefix on every line
    wrapped = textwrap.wrap(title, width=content_width) or ['']
    lines = [f'/* {wrapped[0]}']
    lines.extend(f' * {word}' for word in wrapped[1:])
    lines.append(' */')
    return lines


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


def register_names(root_node):
    """Bare register names (array markers stripped) a root node defines."""
    return {reg['name'].replace('[%s]', '').replace('%s', '')
            for reg in as_list(root_node.get('registers', {}).get('register'))}


def resolve_one_root(spec, mode, instance_addr, svd_by_name, svd_addr_index,
                      iodefine_map, zone_flat, verbose):
    """Resolve one config `template` or `sources` entry to a root dict, or
    (None, error_string).

    mode='canonical' (from `template`): structural clone -- `spec` is
    another canonical (iodefine/.rzone) instance name whose register layout
    this instance reuses verbatim, for instances with no SVD entry of their
    own (e.g. RA4M1 SCI3-8). delta is always 0: the clone's offsets already
    read correctly relative to *this* instance's own base.

    mode='svd' (from `sources`): address splice -- `spec` is a literal SVD
    <name>, for FSP peripherals that fold more than one SVD peripheral's
    registers into a single struct at a shared base (e.g. RA8M1 ICU: SVD
    ICU_COMMON @0x40006000 + SVD ICU @0x4000C000 both live in iodefine's one
    R_ICU_Type -- see memory). delta = this source's own SVD base address
    minus the instance's canonical base, and gets added to every register
    offset pulled from it so the emitted OFFSET defines land where iodefine
    actually puts them.
    """
    if mode == 'canonical':
        lookup_addr = address_for_canonical(spec, iodefine_map, zone_flat)
        if lookup_addr is None:
            return None, f'template "{spec}" not found in iodefine.h or .rzone'
        candidates = svd_addr_index.get(lookup_addr)
        if not candidates:
            return None, f'no SVD peripheral at {hex(lookup_addr)}'
        root_name, root_node = resolve_derived_root(candidates[0], svd_by_name, verbose)
        delta = 0
    else:
        node = svd_by_name.get(spec)
        if node is None:
            return None, (f'"{spec}" not found in SVD by name (sources: '
                           f'entries must be literal SVD <name> values)')
        root_name, root_node = resolve_derived_root(node, svd_by_name, verbose)
        delta = int(node['baseAddress'], 0) - instance_addr

    return {'root_name': root_name, 'root_node': root_node, 'delta': delta,
            'key': f'{root_name}@{delta:#x}'}, None


def resolve_family_instances(family_prefix, instances, svd_by_name, svd_addr_index,
                              iodefine_map, zone_flat, verbose):
    """instances: config `instances` list, each {'name', and optionally
    'template' xor 'sources'} -- see resolve_one_root for the two modes.
    Neither given: single source, the instance's own canonical name (today's
    plain case, delta 0).

    Finds each instance's own base address (for its address block) and the
    SVD root peripheral(s) that define its register template.
    """
    resolved = []
    unresolved = []
    for inst in instances:
        name = inst['name']
        addr = address_for_canonical(name, iodefine_map, zone_flat)
        if addr is None:
            unresolved.append((name, 'not found in iodefine.h or .rzone'))
            continue

        sources = inst.get('sources')
        template = inst.get('template')
        if sources:
            root_specs = [('svd', s) for s in sources]
        elif template:
            root_specs = [('canonical', template)]
        else:
            root_specs = [('canonical', name)]

        roots = []
        failed = False
        for mode, spec in root_specs:
            root, err = resolve_one_root(
                spec, mode, addr, svd_by_name, svd_addr_index,
                iodefine_map, zone_flat, verbose)
            if err:
                hint = ('' if mode == 'svd' or len(root_specs) > 1 else
                        ' -- this instance has no SVD entry; add '
                        '"template: <other-instance-with-same-registers>"')
                unresolved.append((name, f'{spec}: {err}{hint}'))
                failed = True
                continue
            roots.append(root)
        if failed:
            continue

        if len(roots) > 1:
            owner = {}
            conflict = False
            for r in roots:
                for reg_name in register_names(r['root_node']):
                    if reg_name in owner and owner[reg_name] != r['key']:
                        print(f'ERROR: instance "{name}" merges sources '
                              f'"{owner[reg_name]}" and "{r["key"]}", which '
                              f'both define register "{reg_name}" -- merged '
                              f'sources may not redefine the same register '
                              f'name', file=sys.stderr)
                        conflict = True
                    owner.setdefault(reg_name, r['key'])
            if conflict:
                sys.exit(1)

        if verbose:
            tmpl = ' + '.join(r['key'] for r in roots)
            print(f'[family] "{name}" -> template root(s) {tmpl}')

        resolved.append({'name': name, 'addr': addr, 'roots': roots})

    if unresolved:
        print(f'ERROR: could not resolve {len(unresolved)} instance(s) for '
              f'family "{family_prefix}":', file=sys.stderr)
        for name, reason in unresolved:
            print(f'  {name}: {reason}', file=sys.stderr)
        sys.exit(1)

    return resolved


def build_enum_entries(field, field_sym):
    """Return (prefix, comment) tuples for one field's enumerated values,
    for align_comments() to render -- see its docstring for why."""
    entries = []
    values = field.get('enumeratedValues')
    if not values:
        return entries
    for ev in as_list(values.get('enumeratedValue')):
        value = enum_value(ev)
        if value is None:
            continue
        label = enum_label(ev)
        desc = cleanse(ev.get('description', ''))
        sym = f'{field_sym}_{label}'
        entries.append(
            (f'#  define {sym} ({value} << {field_sym}_SHIFT)', desc))
    return entries


def align_comments(entries, gap=2):
    """entries: list of (prefix_text, comment_text_or_None).

    nxstyle requires every right-hand comment within an unbroken run of
    lines (no intervening blank line) to start at the same column,
    regardless of how much the code before it varies in width. Compute
    that one shared column from the widest commented prefix in `entries`
    and render every line to it; entries with no comment are emitted at
    their own natural width (they impose no alignment requirement and
    padding them would just leave dangling trailing whitespace).
    """
    commented_widths = [len(prefix) for prefix, comment in entries
                         if comment is not None]
    col = max(commented_widths) + gap if commented_widths else 0
    lines = []
    for prefix, comment in entries:
        if comment is not None:
            lines.append(f'{prefix:<{col}}/* {comment} */')
        else:
            lines.append(prefix)
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
        for r in inst['roots']:
            key = r['key']
            if key not in seen_roots:
                root_order.append(key)
                seen_roots[key] = (r['root_node'], r['delta'],
                                    as_list(r['root_node'].get('registers', {})
                                            .get('register')))

    root_reg_names = {}
    name_owner = {}
    merged = []
    for key in root_order:
        root_node, delta, registers = seen_roots[key]
        names = set()
        for reg in registers:
            reg_name = reg['name'].replace('[%s]', '').replace('%s', '')
            names.add(reg_name)
            offset = int(reg['addressOffset'], 0) + delta
            if reg_name not in name_owner:
                name_owner[reg_name] = (key, offset)
                merged.append((reg_name, reg, offset))
            elif verbose:
                prev_key, prev_off = name_owner[reg_name]
                if prev_off != offset:
                    print(f'[merge] "{family_prefix}": WARNING "{reg_name}" '
                          f'is at {hex(prev_off)} via "{prev_key}" but '
                          f'{hex(offset)} via "{key}" -- using the first '
                          f'one seen')
        root_reg_names[key] = names
    merged.sort(key=lambda item: item[2])

    offset_entries = []
    register_blocks = []
    offset_meta = {}
    for reg_name, reg, offset in merged:
        is_array = '%s' in reg['name']
        descr = cleanse(reg.get('description', reg_name))
        size = reg.get('size', '32')
        dim = int(reg.get('dim', '0'), 0) if is_array else 0
        increment = int(reg.get('dimIncrement', '0'), 0) if is_array else 0
        offset_meta[reg_name] = (offset, descr, size, is_array, dim, increment)

        sym = f'{prefix}{family_prefix}_{reg_name}'
        offset_sym = f'{sym}_OFFSET'
        offset_entries.append(
            (offset_sym, f'0x{offset:04x}', f'{descr} ({size}-bits)'))

        fields = [f for f in as_list(reg.get('fields', {}).get('field'))
                  if f.get('name') != 'Reserved']
        if not fields:
            continue

        block_entries = []

        if is_array and dim:
            size_sym = f'{sym}_SIZE'
            block_entries.append((f'#define {size_sym} {dim}', None))

        for field in fields:
            fname = field['name']
            fdescr = cleanse(field.get('description', 'N/A'))
            lsb, width = field_bits(field)
            fsym = f'{sym}_{fname}'
            if width <= 1:
                block_entries.append(
                    (f'#define {fsym} (1 << {lsb:>2})',
                     f'{(1 << lsb):02x}: {fdescr}'))
            else:
                mask = (1 << width) - 1
                shift_sym = f'{fsym}_SHIFT'
                mask_sym = f'{fsym}_MASK'
                block_entries.append((f'#define {shift_sym} ({lsb})', None))
                block_entries.append(
                    (f'#define {mask_sym} (0x{mask:x})', None))
                block_entries.extend(build_enum_entries(field, fsym))

        register_blocks.append((f'{descr} ({size}-bits)', block_entries))

    # Offset defines for every register in the family are emitted back to
    # back with no blank line between them, so nxstyle treats the whole
    # block as one run -- align every comment in it to one shared column.
    # The symbol column is padded wide enough for the longest offset
    # symbol so hex values (fixed-width "0xHHHH") still line up, matching
    # the fixed COLUMN this replaces for the common case; the value field
    # is fixed-width already, so aligning symbols is enough to align
    # comments too.
    sym_col = max([COLUMN] + [len(sym) for sym, _, _ in offset_entries]) + 1
    offset_lines = align_comments(
        [(f'#define {sym:<{sym_col}} {value}', comment)
         for sym, value, comment in offset_entries])

    bitfield_lines = []
    for title, entries in register_blocks:
        bitfield_lines.append('')
        bitfield_lines.extend(subbanner(title))
        bitfield_lines.append('')
        bitfield_lines.extend(align_comments(entries))

    merged_names = [name for name, _, _ in merged]
    return offset_lines, bitfield_lines, merged_names, offset_meta, root_reg_names


def build_instance_address_lines(inst, family_prefix, prefix, merged_names,
                                  offset_meta, root_reg_names):
    names = set()
    for r in inst['roots']:
        names |= root_reg_names[r['key']]
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
        roots = {r['root_name'] for inst in resolved for r in inst['roots']}
        print(f'[gen] "{family_prefix}": {len(resolved)} instance(s), '
              f'{len(roots)} distinct template(s) ({sorted(roots)}), '
              f'{total} registers merged')

    return offset_lines, address_lines, bitfield_lines


def emit_peripheral(family_prefix, resolved, prefix, path_comment,
                     memorymap_include, verbose):
    guard = derive_guard(path_comment)
    offset_lines, address_lines, bitfield_lines = build_family_sections(
        family_prefix, resolved, prefix, verbose)

    lines = [file_header(path_comment), '', f'#ifndef {guard}', f'#define {guard}', '']
    lines.append(section_banner('Included Files'))
    lines.append('')
    lines.append('#include <nuttx/config.h>')
    lines.append('')
    lines.append('#include "chip.h"')
    lines.append(f'#include "{memorymap_include}"')
    lines.append('')
    lines.append(section_banner('Pre-processor Definitions'))
    lines.append('')
    lines.extend(subbanner('Register Offsets'))
    lines.append('')
    lines.extend(offset_lines)
    lines.append('')
    lines.extend(subbanner('Register Addresses'))
    # address_lines already opens with a blank line (see
    # build_family_sections), so no extra '' is added here.
    lines.extend(address_lines)
    lines.append('')
    lines.extend(subbanner('Register Bitfield Definitions'))
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
# Kconfig
# ---------------------------------------------------------------------------

def derive_chip_label(rzone_path):
    """Kconfig chip label from an .rzone filename: the literal filename
    stem, uncollapsed.

    RA4M1's checked-in Kconfig uses collapsed labels (R7FA4M1AB3CFP.rzone ->
    ARCH_CHIP_R7FA4M1ABxxFP), which looked like a generic "collapse the 2
    chars before the package suffix" rule -- but checking RA8M1's actual
    User's Manual (Table 1.13, "Product part number") disproved that as a
    general heuristic: Renesas's own concrete, orderable part numbers are
    fully uncollapsed (R7FA8M1AHECBD, R7FA8M1AHECFC, ...); an "AxECxx"
    abstraction only shows up in a separate Function Comparison table,
    collapsing the H/F die letter -- a different position than what RA4M1's
    label appears to collapse, and one that would be actively wrong here
    (H vs F differ in SRAM, not just a flash-size/grade bin). Whether an
    axis is safe to collapse is a per-family fact this generator has no
    source for, so the safe default is: don't guess, use the literal name.
    If a family's Kconfig should abbreviate part numbers the way RA4M1's
    does, encode that by hand once it's confirmed against that family's own
    docs, not by inferring it from another family's filenames.
    """
    return os.path.splitext(os.path.basename(rzone_path))[0]


def emit_kconfig(chip_depends, family_constant, arch_select, menu_title,
                  variants, instance_names, default_label=None):
    """Emit only the mechanically-derivable slice of a family Kconfig: the
    `choice` block linking each part number to the RA_HAVE_* flags for the
    peripheral instances that specific .rzone variant actually has, and the
    bare `config RA_HAVE_*` bool declarations those select lines reference.

    Deliberately stops there -- the human-facing enable options (e.g.
    `config RA_SCI0_UART` with its prompt and `select SCI0_SERIALDRIVER`)
    encode driver-subsystem knowledge this generator has no source for, and
    are meant to be written by hand afterward. See README.

    variants: [(chip_label, set_of_present_instance_names), ...], already
    in the desired output order (see load_config: sorted by filename).
    instance_names: ordered, de-duplicated instance names taken directly
    from the peripherals.yaml `peripherals[].instances[].name` list (main()
    skips any peripheral entry marked `variant_tracked: false` -- core
    singletons like SYSTEM/MSTP/ICU are present on every variant, so a
    "does this part have it" flag is meaningless for them; RA4M1's real
    Kconfig confirms this by only having RA_HAVE_* for SCI/GPT, not for its
    own always-present peripherals), so the RA_HAVE_* set matches what a
    board's `.rzone` variant actually varies in, not everything the header
    generator happens to track.
    default_label: chip label for the choice's `default` line (from
    `kconfig.default_chip` in the manifest). Falls back to the
    alphabetically-first variant when unset -- that's an arbitrary pick,
    not a recommendation, since this generator has no basis for knowing
    which part is the "primary" one; it can land on an undocumented or
    otherwise wrong part purely by filename sort order, so set this
    explicitly once a real target is known rather than trusting the
    fallback.
    """
    lines = [
        '#',
        '# For a description of the syntax of this configuration file,',
        '# see the file kconfig-language.txt in the NuttX tools repository.',
        '#',
        '',
        'comment "RA Configuration Options"',
        '',
        'choice',
        '\tprompt "RA Chip Selection"',
    ]
    if default_label:
        lines.append(f'\tdefault ARCH_CHIP_{default_label}')
    elif variants:
        lines.append(f'\tdefault ARCH_CHIP_{variants[0][0]}')
    lines.append(f'\tdepends on {chip_depends}')
    lines.append('')

    for label, present in variants:
        lines.append(f'config ARCH_CHIP_{label}')
        lines.append(f'\tbool "{label}"')
        for inst in instance_names:
            if inst in present:
                lines.append(f'\tselect RA_HAVE_{inst}')
        lines.append(f'\tselect {family_constant}')
        lines.append('')

    lines.append('endchoice # RA Chip Selection')
    lines.append('')
    lines.append(f'config {family_constant}')
    lines.append('\tbool')
    lines.append('\tdefault n')
    lines.append(f'\tselect {arch_select}')
    lines.append('')
    lines.append(f'menu "{menu_title}"')
    lines.append('')

    for inst in instance_names:
        lines.append(f'config RA_HAVE_{inst}')
        lines.append('\tbool')
        lines.append('\tdefault n')
        lines.append('')

    lines.append('# TODO: user-facing enable options go here, one per')
    lines.append('# RA_HAVE_* above (prompt, depends on RA_HAVE_*, select')
    lines.append('# the driver it wires up -- see README "Kconfig draft").')
    lines.append('')
    lines.append(f'endmenu # {menu_title}')
    lines.append('')
    return '\n'.join(lines)


def resolve_variants(variant_zones, verbose):
    """[(chip_label, set_of_present_peripheral_names), ...] for every
    .rzone path in `variant_zones` -- the presence set is that variant's
    own flat peripheral name list (zone_peripherals()), so callers (the
    Kconfig and chip.h emitters) can each decide which of those names they
    care about without re-reading the zone files themselves.
    """
    variants = []
    for vzone_path in variant_zones:
        if verbose:
            print(f'Reading variant zone file {vzone_path}')
        present = {e['name'] for e in zone_peripherals(read_zone(vzone_path))}
        variants.append((derive_chip_label(vzone_path), present))
    return variants


def group_family_counts(peripherals, present_names):
    """(prefix, count, present_instance_names) for every peripherals.yaml
    entry not opted out via `variant_tracked: false` -- present_instance_
    names is the subset of that entry's declared instances `present_names`
    (one .rzone variant's flat peripheral list) actually contains, in
    declared order; count is just its length. Shared by the Kconfig and
    chip.h emitters' "does this part have it" logic; see
    emit_chip_header().
    """
    groups = []
    for periph in peripherals:
        if periph.get('variant_tracked') is False:
            continue
        present = [inst['name'] for inst in periph['instances']
                   if inst['name'] in present_names]
        groups.append((periph['prefix'], len(present), present))
    return groups


def describe_instance_range(prefix, names):
    """Human-readable range description for a peripheral group's present
    instance names, e.g. ['SCI_B0', ..., 'SCI_B4', 'SCI_B9'] ->
    'SCI_B0-4, SCI_B9 (not contiguous: no SCI_B5-8)'.

    A bare count macro (RA_N<PREFIX>) says nothing about *which* numbers
    exist -- RA8M1's SCI_B is exactly the case that bites: SCI_B0-4 and
    SCI_B9 are real, SCI_B5-8 are not (see memory), so
    `for (i = 0; i < RA_NSCI_B; i++)` would touch a nonexistent SCI_B5 and
    never reach SCI_B9. Calling the gap out in the comment, the way
    STM32's own chip.h hedges STM32_NUSART ("Actually only 3: USART1, 2
    and 6"), is the cheap fix for a draft with no consumer yet to instead
    build a real per-index lookup for.

    Falls back to a plain comma-joined name list for anything that isn't
    exactly `prefix` + digits (defensive default; every current entry
    matches).
    """
    nums = []
    for name in names:
        m = re.fullmatch(re.escape(prefix) + r'(\d+)', name)
        if not m:
            return ', '.join(names)
        nums.append(int(m.group(1)))
    nums.sort()

    runs = []
    start = prev = nums[0]
    for n in nums[1:]:
        if n == prev + 1:
            prev = n
        else:
            runs.append((start, prev))
            start = prev = n
    runs.append((start, prev))

    def span(a, b):
        return f'{prefix}{a}' if a == b else f'{prefix}{a}-{b}'

    desc = ', '.join(span(a, b) for a, b in runs)
    if len(runs) > 1:
        gaps = [span(prev_b + 1, next_a - 1)
                for (_, prev_b), (next_a, _) in zip(runs, runs[1:])]
        desc += f' (not contiguous: no {", ".join(gaps)})'
    return desc


def emit_chip_header(path_comment, family_label, variants, peripherals):
    """Emit a chip.h mirroring the STM32 family's convention
    (arch/arm/include/stm32f4/chip.h): one #if/#elif per part number
    (CONFIG_ARCH_CHIP_<label>, the same labels emit_kconfig() uses),
    defining RA_N<PREFIX> for each tracked peripheral group -- the count of
    that group's peripherals.yaml instances this part's .rzone actually
    has, mirroring STM32_N<PERIPH>. Peripheral entries marked
    `variant_tracked: false` (core singletons like SYSTEM/MSTP/ICU -- see
    group_family_counts()) are excluded, same as the Kconfig draft: STM32
    doesn't count its own always-exactly-one architectural blocks either
    (no STM32_NSYSCFG, no STM32_NPWR).

    Deliberately mechanical-only, same scope boundary as the Kconfig draft:
    no flash/SRAM size defines (out of scope for this whole generator, see
    memory), no peripheral-IP-version defines (STM32_HAVE_IP_SPI_V2-style
    -- driver-subsystem knowledge this generator has no source for). The
    NVIC priority block is emitted verbatim (fixed Cortex-M convention,
    identical across every existing RA/STM32 chip.h checked so far, not
    derived from anything family-specific).

    variants: [(chip_label, set_of_present_peripheral_names), ...] from
    resolve_variants().
    """
    guard = derive_guard(path_comment)
    lines = [file_header(path_comment), '', f'#ifndef {guard}', f'#define {guard}', '']
    lines.append(section_banner('Included Files'))
    lines.append('')
    lines.append('#include <nuttx/config.h>')
    lines.append('')
    lines.append(section_banner('Pre-processor Definitions'))
    lines.append('')
    lines.append('/* Get customizations for each supported chip */')
    lines.append('')

    keyword = 'if'
    for label, present in variants:
        lines.append(f'#{keyword} defined(CONFIG_ARCH_CHIP_{label})')
        keyword = 'elif'
        groups = group_family_counts(peripherals, present)
        sym_texts = [f'#  define RA_N{prefix}' for prefix, _, _ in groups]
        sym_col = max((len(s) for s in sym_texts), default=0) + 1
        entries = [(f'{sym:<{sym_col}} {count}',
                    describe_instance_range(prefix, names))
                   for sym, (prefix, count, names) in zip(sym_texts, groups)]
        lines.extend(align_comments(entries))
        lines.append('')

    lines.append('#else')
    lines.append(f'#  error "Unsupported {family_label} chip"')
    lines.append('#endif')
    lines.append('')
    lines.extend(subbanner('NVIC Priority Levels'))
    lines.append('')
    lines.append('/* Each priority field holds a priority value, 0-15. The lower')
    lines.append(' * the value, the greater the priority of the corresponding')
    lines.append(' * interrupt. The processor implements only bits[7:4] of each')
    lines.append(' * field, bits[3:0] read as zero and ignore writes.')
    lines.append(' */')
    lines.append('')
    nvic_entries = [
        ('#define NVIC_SYSH_PRIORITY_MIN',
         '0xf0', 'All bits[7:4] set is minimum priority'),
        ('#define NVIC_SYSH_PRIORITY_DEFAULT',
         '0x80', 'Midpoint is the default'),
        ('#define NVIC_SYSH_PRIORITY_MAX',
         '0x00', 'Zero is maximum priority'),
        ('#define NVIC_SYSH_PRIORITY_STEP',
         '0x10', 'Four bits of interrupt priority used'),
    ]
    nvic_col = max(len(sym) for sym, _, _ in nvic_entries) + 1
    lines.extend(align_comments(
        [(f'{sym:<{nvic_col}} {value}', comment)
         for sym, value, comment in nvic_entries]))
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

def load_config(path):
    """Load the YAML manifest that drives generation for one part/family --
    svd/zone/iodefine sources, the memorymap output, the #include path
    peripheral headers use to pull in the memorymap (`memorymap_include`,
    default 'hardware/ra_memorymap.h' -- override this per family if it has
    no such generic dispatcher, e.g. RA8M1), every peripheral header's
    instance list (including template/sources overrides -- see
    resolve_family_instances), an optional `kconfig` section -- see
    emit_kconfig() -- driving a draft Kconfig linking each part number
    (one per `kconfig.variant_zones` glob match) to the RA_HAVE_* flags for
    whichever `peripherals[].instances` it actually has, and an optional
    `chip_header` section -- see emit_chip_header() -- driving a draft
    chip.h with the same per-part-number linkage, STM32-style
    (RA_N<PREFIX> counts instead of RA_HAVE_* bools). Both `kconfig` and
    `chip_header` respect `variant_tracked: false` on a peripheral entry
    (core singletons like SYSTEM/MSTP/ICU -- see group_family_counts()).
    Checked into git so a generation run is
    always reproducible from history alone; this replaced a CLI-flag-driven
    design (--peripheral) specifically because one such invocation (RA8M1
    SCI's exact NAME=TEMPLATE overrides) was lost and couldn't be
    reconstructed -- see memory ra8m1-header-generation.

    Relative svd/zone/iodefine/outfile paths are resolved against the
    config file's own directory, not the CWD, so the manifest is portable.
    """
    with open(path) as fp:
        cfg = yaml.safe_load(fp)
    base_dir = os.path.dirname(os.path.abspath(path))

    def resolve(p):
        return p if os.path.isabs(p) else os.path.join(base_dir, p)

    for key in ('svd', 'zone', 'iodefine'):
        if cfg.get(key):
            cfg[key] = resolve(cfg[key])
    if cfg.get('memorymap'):
        cfg['memorymap']['outfile'] = resolve(cfg['memorymap']['outfile'])
    def resolve_zone_glob(section_name, section):
        pattern = section.get('variant_zones')
        if pattern:
            section['variant_zones'] = sorted(glob.glob(resolve(pattern)))
            if not section['variant_zones']:
                print(f'ERROR: {section_name}.variant_zones pattern '
                      f'"{pattern}" matched no files', file=sys.stderr)
                sys.exit(1)

    if cfg.get('kconfig'):
        kc = cfg['kconfig']
        kc['outfile'] = resolve(kc['outfile'])
        resolve_zone_glob('kconfig', kc)
    if cfg.get('chip_header'):
        ch = cfg['chip_header']
        ch['outfile'] = resolve(ch['outfile'])
        resolve_zone_glob('chip_header', ch)
    for periph in cfg.get('peripherals', []):
        periph['outfile'] = resolve(periph['outfile'])
        for inst in periph.get('instances', []):
            if inst.get('sources') and inst.get('template'):
                print(f'ERROR: instance "{inst["name"]}" in "{periph["outfile"]}"'
                      f' has both "sources" and "template" -- pick one',
                      file=sys.stderr)
                sys.exit(1)
    return cfg


def main():
    parser = argparse.ArgumentParser(
        description='Generate NuttX RA-family memorymap/register headers '
                     'from CMSIS iodefine.h + .svd (+ .rzone for name '
                     'cross-checking and, later, memory regions).')
    parser.add_argument('--config',
                         help='YAML manifest for one part/family: svd/zone/'
                              'iodefine paths, the memorymap output, and '
                              'every peripheral header\'s instance list '
                              '(replaces individually specifying peripheral '
                              'headers on the command line, so a generation '
                              'run is always reproducible from git history '
                              'alone). See load_config() docstring for the '
                              'schema. --svd/--zone/--iodefine/--memorymap/'
                              '--memorymap-path/--memorymap-include/--prefix '
                              'below override the corresponding config '
                              'value when both are given.')
    parser.add_argument('--svd', help='CMSIS .svd file')
    parser.add_argument('--zone', help='FSP .rzone file')
    parser.add_argument('--iodefine',
                         help='CMSIS iodefine.h -- required for --memorymap, '
                              'since it is the full-chip peripheral list '
                              '(the .rzone file is scoped to one package '
                              'variant and is missing peripherals the '
                              'memorymap needs)')
    parser.add_argument('--prefix', help='C symbol prefix (default "R_")')
    parser.add_argument('--memorymap', help='Output path for memorymap.h')
    parser.add_argument('--memorymap-path',
                         help='Repo path written into the memorymap.h header '
                              'comment and used to derive its include guard '
                              '(default "memorymap.h")')
    parser.add_argument('--memorymap-include',
                         help='#include path every peripheral header uses to '
                              'pull in the memorymap (default '
                              '"hardware/ra_memorymap.h", the family-generic '
                              'dispatcher some RA ports use -- a family with '
                              'no such dispatcher, e.g. RA8M1 mid-port, '
                              'should point this straight at its own '
                              '"hardware/<chip>_memorymap.h")')
    parser.add_argument('-v', '--verbose', action='store_true')
    parser.add_argument('-o', '--overwrite', action='store_true')
    args = parser.parse_args()

    cfg = load_config(args.config) if args.config else {}

    svd_path = args.svd or cfg.get('svd')
    zone_path = args.zone or cfg.get('zone')
    iodefine_path = args.iodefine or cfg.get('iodefine')
    prefix = args.prefix or cfg.get('prefix') or 'R_'
    cfg_memorymap = cfg.get('memorymap') or {}
    memorymap_outfile = args.memorymap or cfg_memorymap.get('outfile')
    memorymap_path = (args.memorymap_path or cfg_memorymap.get('path')
                       or 'memorymap.h')
    memorymap_include = (args.memorymap_include or cfg.get('memorymap_include')
                          or 'hardware/ra_memorymap.h')
    peripherals = cfg.get('peripherals', [])
    kconfig_cfg = cfg.get('kconfig')
    chip_header_cfg = cfg.get('chip_header')

    if not svd_path or not zone_path:
        print('ERROR: --svd and --zone are required (directly or via '
              '--config).', file=sys.stderr)
        sys.exit(1)

    if (not memorymap_outfile and not peripherals and not kconfig_cfg
            and not chip_header_cfg):
        print('Nothing to do: specify --memorymap and/or --config '
              'peripherals/kconfig/chip_header.')
        sys.exit(1)

    if memorymap_outfile and not iodefine_path:
        print('ERROR: --memorymap requires --iodefine (it is the primary '
              'source; the .rzone file alone under-reports peripherals).',
              file=sys.stderr)
        sys.exit(1)

    if args.verbose:
        print(f'Reading zone file {zone_path}')
    zone_doc = read_zone(zone_path)
    zone_flat = zone_peripherals(zone_doc)
    if args.verbose:
        print(f'zone: {len(zone_flat)} peripherals (bare + grouped)')

    if args.verbose:
        print(f'Reading SVD file {svd_path}')
    svd_doc = read_svd(svd_path)
    svd_by_name = svd_index_by_name(svd_doc)
    svd_by_addr = svd_index_by_address(svd_doc)

    iodefine_map = None
    if iodefine_path:
        if args.verbose:
            print(f'Reading iodefine file {iodefine_path}')
        iodefine_map = iodefine_bases(iodefine_path)
        if args.verbose:
            print(f'iodefine: {len(iodefine_map)} peripheral base defines')

    if memorymap_outfile and clobber_ok(memorymap_outfile, args.overwrite):
        cross_check_memorymap(iodefine_map, svd_by_addr, zone_flat, args.verbose)
        content = emit_memorymap(iodefine_map, prefix, memorymap_path)
        with open(memorymap_outfile, 'w') as fp:
            fp.write(content)
        if args.verbose:
            print(f'Wrote {memorymap_outfile} '
                  f'({len(iodefine_map)} peripheral base defines)')

    for periph in peripherals:
        outfile = periph['outfile']
        if not clobber_ok(outfile, args.overwrite):
            continue
        resolved = resolve_family_instances(
            periph['prefix'], periph['instances'], svd_by_name, svd_by_addr,
            iodefine_map, zone_flat, args.verbose)
        content = emit_peripheral(
            periph['prefix'], resolved, prefix, periph['path'],
            memorymap_include, args.verbose)
        with open(outfile, 'w') as fp:
            fp.write(content)
        if args.verbose:
            print(f'Wrote {outfile}')

    if kconfig_cfg and clobber_ok(kconfig_cfg['outfile'], args.overwrite):
        instance_names = []
        seen = set()
        for periph in peripherals:
            if periph.get('variant_tracked') is False:
                continue
            for inst in periph['instances']:
                name = inst['name']
                if name not in seen:
                    seen.add(name)
                    instance_names.append(name)

        variants = resolve_variants(
            kconfig_cfg.get('variant_zones', []), args.verbose)
        if args.verbose:
            for label, present in variants:
                have = [n for n in instance_names if n in present]
                print(f'[kconfig] "{label}": {len(have)}/'
                      f'{len(instance_names)} tracked instances present '
                      f'({have})')

        content = emit_kconfig(
            kconfig_cfg['chip_depends'], kconfig_cfg['family_constant'],
            kconfig_cfg['arch_select'], kconfig_cfg['menu_title'],
            variants, instance_names,
            default_label=kconfig_cfg.get('default_chip'))
        with open(kconfig_cfg['outfile'], 'w') as fp:
            fp.write(content)
        if args.verbose:
            print(f'Wrote {kconfig_cfg["outfile"]} ({len(variants)} chip '
                  f'variant(s), {len(instance_names)} tracked instance(s))')

    if chip_header_cfg and clobber_ok(chip_header_cfg['outfile'], args.overwrite):
        variants = resolve_variants(
            chip_header_cfg.get('variant_zones', []), args.verbose)
        if args.verbose:
            for label, present in variants:
                groups = group_family_counts(peripherals, present)
                counts = {prefix: count for prefix, count, _ in groups}
                print(f'[chip_header] "{label}": {counts}')

        content = emit_chip_header(
            chip_header_cfg['path'], chip_header_cfg['family_label'],
            variants, peripherals)
        with open(chip_header_cfg['outfile'], 'w') as fp:
            fp.write(content)
        if args.verbose:
            print(f'Wrote {chip_header_cfg["outfile"]} '
                  f'({len(variants)} chip variant(s))')


if __name__ == '__main__':
    main()
