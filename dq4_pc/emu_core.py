#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test de faisabilite (voie A) du portage PC de Dragon Quest IV.

Charge libdq4core.so (code ARM 64 bits du moteur du jeu) dans un emulateur (Unicorn) sur PC,
sans Unity, puis appelle ses fonctions d'entree : IsLoadNativeCode, DevInit, GameInit, GameUpdate...
Les fichiers du jeu sont lus directement dans TES APK (rien n'est copie ni distribue).
Le resultat complet est ecrit dans rapport_test.txt (a la fin, a envoyer a Claude).

Usage normal : lancer_test.bat
Options : --apks DOSSIER  --lib FICHIER.so  --lang N  --frames N  --dry  --variant n
"""
import sys, os, struct, time, re, zipfile, bisect, argparse, traceback

MASK64 = (1 << 64) - 1
PAGE = 0x1000


def up(x, a=PAGE):
    return (x + a - 1) & ~(a - 1)


# ----------------------------------------------------------------------------------------------
# Journal : ecran + fichier
# ----------------------------------------------------------------------------------------------
class Log:
    def __init__(self, path=None):
        self.f = open(path, 'w', encoding='utf-8', errors='replace') if path else None

    def __call__(self, *a):
        s = ' '.join(str(x) for x in a)
        try:
            print(s)
        except Exception:
            print(s.encode('ascii', 'replace').decode())
        if self.f:
            self.f.write(s + '\n')
            self.f.flush()


# ----------------------------------------------------------------------------------------------
# ELF 64 bits AArch64
# ----------------------------------------------------------------------------------------------
DT_PLTRELSZ, DT_HASH, DT_STRTAB, DT_SYMTAB, DT_RELA, DT_RELASZ = 2, 4, 5, 6, 7, 8
DT_INIT, DT_JMPREL, DT_INIT_ARRAY, DT_INIT_ARRAYSZ, DT_GNU_HASH = 12, 23, 25, 27, 0x6ffffef5
DT_RELRSZ, DT_RELR = 35, 36
R_ABS64, R_GLOB_DAT, R_JUMP_SLOT, R_RELATIVE = 257, 1025, 1026, 1027


class Elf:
    def __init__(self, data):
        if data[:4] != b'\x7fELF' or data[4] != 2:
            raise ValueError("ce fichier n'est pas un ELF 64 bits")
        e_machine = struct.unpack_from('<H', data, 18)[0]
        if e_machine != 183:
            raise ValueError("ce n'est pas du code ARM 64 bits")
        e_phoff = struct.unpack_from('<Q', data, 0x20)[0]
        e_phentsize, e_phnum = struct.unpack_from('<HH', data, 0x36)
        segs = []
        for i in range(e_phnum):
            segs.append(struct.unpack_from('<IIQQQQQQ', data, e_phoff + i * e_phentsize))
        loads = [s for s in segs if s[0] == 1]
        self.size = up(max(s[3] + s[6] for s in loads))
        self.img = bytearray(self.size)
        for (t, fl, off, va, pa, fsz, msz, al) in loads:
            self.img[va:va + fsz] = data[off:off + fsz]
        dyn = [s for s in segs if s[0] == 2]
        if not dyn:
            raise ValueError('pas de section dynamique')
        self.dyn = {}
        p = dyn[0][3]
        while True:
            tag, val = struct.unpack_from('<qQ', self.img, p)
            p += 16
            if tag == 0:
                break
            self.dyn.setdefault(tag, []).append(val)
        d = self.dyn
        self.symtab, self.strtab = d[DT_SYMTAB][0], d[DT_STRTAB][0]
        self.nsyms = self._count_syms()
        self.syms = []
        for i in range(self.nsyms):
            st_name, st_info, st_other, st_shndx, st_value, st_size = struct.unpack_from(
                '<IBBHQQ', self.img, self.symtab + 24 * i)
            self.syms.append((self._str(st_name), st_info >> 4, st_info & 15, st_shndx, st_value, st_size))
        self.exports = {}
        funcs = []
        for (name, bind, typ, shndx, val, sz) in self.syms:
            if shndx != 0 and name:
                self.exports[name] = val
                if typ == 2:
                    funcs.append((val, sz, name))
        funcs.sort()
        self.func_starts = [f[0] for f in funcs]
        self.funcs = funcs

    def _u32(self, o):
        return struct.unpack_from('<I', self.img, o)[0]

    def _str(self, off):
        p = self.strtab + off
        e = self.img.index(b'\0', p)
        return bytes(self.img[p:e]).decode('utf-8', 'replace')

    def _count_syms(self):
        d = self.dyn
        if DT_HASH in d:
            return self._u32(d[DT_HASH][0] + 4)
        h = d[DT_GNU_HASH][0]
        nb, symoff, bloom, shift = struct.unpack_from('<IIII', self.img, h)
        buckets = h + 16 + bloom * 8
        chains = buckets + nb * 4
        maxb = max(self._u32(buckets + 4 * i) for i in range(nb))
        if maxb < symoff:
            return symoff
        idx = maxb
        while not (self._u32(chains + 4 * (idx - symoff)) & 1):
            idx += 1
        return idx + 1

    def relocate(self, base, stub_for):
        """Applique les relocations dans l'image. stub_for(nom, liaison) -> adresse d'un import externe."""
        d = self.dyn
        if DT_RELR in d:
            raise ValueError('relocations RELR non gerees')
        st = {'ABS64': 0, 'GLOB_DAT': 0, 'JUMP_SLOT': 0, 'RELATIVE': 0, 'inconnues': {},
              'internes': 0, 'externes': set()}
        tables = []
        if DT_RELA in d:
            tables.append((d[DT_RELA][0], d[DT_RELASZ][0]))
        if DT_JMPREL in d:
            tables.append((d[DT_JMPREL][0], d[DT_PLTRELSZ][0]))
        cache = {}
        for off, sz in tables:
            for i in range(sz // 24):
                r_off, r_info, r_add = struct.unpack_from('<QQq', self.img, off + 24 * i)
                t, si = r_info & 0xffffffff, r_info >> 32
                if t == R_RELATIVE:
                    val = base + r_add
                    st['RELATIVE'] += 1
                elif t in (R_ABS64, R_GLOB_DAT, R_JUMP_SLOT):
                    if si == 0:
                        S = 0
                    else:
                        name, bind, typ, shndx, value, size = self.syms[si]
                        if shndx != 0:
                            S = base + value
                            st['internes'] += 1
                        else:
                            if name not in cache:
                                cache[name] = stub_for(name, bind)
                            S = cache[name]
                            st['externes'].add(name)
                    val = S + (r_add if t in (R_ABS64, R_GLOB_DAT) else 0)
                    st[{R_ABS64: 'ABS64', R_GLOB_DAT: 'GLOB_DAT', R_JUMP_SLOT: 'JUMP_SLOT'}[t]] += 1
                else:
                    st['inconnues'][t] = st['inconnues'].get(t, 0) + 1
                    continue
                struct.pack_into('<Q', self.img, r_off, val & MASK64)
        return st

    def init_functions(self, base):
        """Adresses des constructeurs, dans l'ordre d'appel (apres relocation)."""
        out = []
        d = self.dyn
        if DT_INIT in d:
            out.append(base + d[DT_INIT][0])
        if DT_INIT_ARRAY in d:
            a, n = d[DT_INIT_ARRAY][0], d[DT_INIT_ARRAYSZ][0]
            for i in range(n // 8):
                p = struct.unpack_from('<Q', self.img, a + 8 * i)[0]
                if p not in (0, MASK64):
                    out.append(p)
        return out


# ----------------------------------------------------------------------------------------------
# Fichiers du jeu : lus dans les APK
# ----------------------------------------------------------------------------------------------
class ApkFs:
    def __init__(self, zips):
        self.zips = [zipfile.ZipFile(z) for z in zips]
        self.names = {}
        for zf in self.zips:
            for n in zf.namelist():
                self.names.setdefault(n, zf)
        self.cache = {}

    def open(self, rel):
        for cand in ('assets/' + rel, rel):
            if cand in self.cache:
                return self.cache[cand]
            zf = self.names.get(cand)
            if zf is not None:
                data = zf.read(cand)
                if len(self.cache) < 200:
                    self.cache[cand] = data
                return data
        return None


# ----------------------------------------------------------------------------------------------
# printf minimal (va_list AArch64)
# ----------------------------------------------------------------------------------------------
_FMT = re.compile(rb'%([-+ #0]*)(\d+|\*)?(?:\.(\d+|\*))?(hh|h|ll|l|z|j|t|L)?([diouxXcsfFeEgGpn%])')


class VaList:
    def __init__(self, core, ptr):
        self.c, self.p = core, ptr

    def _u64(self, a):
        return int.from_bytes(self.c.mem(a, 8), 'little')

    def gp(self):
        c, p = self.c, self.p
        stack, grtop = self._u64(p), self._u64(p + 8)
        gr = int.from_bytes(c.mem(p + 24, 4), 'little', signed=True)
        if gr >= 0 or gr + 8 > 0:
            addr = stack
            c.mem_w(p, ((stack + 8) & MASK64).to_bytes(8, 'little'))
        else:
            addr = grtop + gr
            c.mem_w(p + 24, (gr + 8).to_bytes(4, 'little', signed=True))
        return self._u64(addr)

    def fp(self):
        c, p = self.c, self.p
        stack, vrtop = self._u64(p), self._u64(p + 16)
        vr = int.from_bytes(c.mem(p + 28, 4), 'little', signed=True)
        if vr >= 0 or vr + 16 > 0:
            addr = stack
            c.mem_w(p, ((stack + 8) & MASK64).to_bytes(8, 'little'))
        else:
            addr = vrtop + vr
            c.mem_w(p + 28, (vr + 16).to_bytes(4, 'little', signed=True))
        return struct.unpack('<d', c.mem(addr, 8))[0]


def format_c(core, fmt, va):
    out = bytearray()
    pos = 0
    for m in _FMT.finditer(fmt):
        out += fmt[pos:m.start()]
        pos = m.end()
        flags, width, prec, length, conv = m.groups()
        conv = conv.decode()
        if conv == '%':
            out += b'%'
            continue
        if width == b'*':
            v = va.gp() & 0xffffffff
            width = str(v - (1 << 32) if v >> 31 else v).encode()
        if prec == b'*':
            v = va.gp() & 0xffffffff
            prec = str(max(v - (1 << 32) if v >> 31 else v, 0)).encode()
        spec = '%' + flags.decode() + (width.decode() if width else '') + ('.' + prec.decode() if prec else '')
        if conv in 'di':
            v = va.gp()
            bits = 64 if length in (b'l', b'll', b'z', b'j', b't') else 32
            v &= (1 << bits) - 1
            if v >> (bits - 1):
                v -= 1 << bits
            out += ((spec + 'd') % v).encode()
        elif conv in 'ouxX':
            v = va.gp()
            bits = 64 if length in (b'l', b'll', b'z', b'j', b't') else 32
            out += ((spec + conv) % (v & ((1 << bits) - 1))).encode()
        elif conv == 'c':
            out += bytes([va.gp() & 0xff])
        elif conv == 's':
            p = va.gp()
            s = core.read_cstr(p).decode('latin-1') if p else '(null)'
            out += ((spec + 's') % s).encode('latin-1')
        elif conv in 'fFeEgG':
            out += ((spec + conv) % va.fp()).encode()
        elif conv == 'p':
            out += ('0x%x' % va.gp()).encode()
        elif conv == 'n':
            va.gp()
    out += fmt[pos:]
    return bytes(out)


# ----------------------------------------------------------------------------------------------
# Emulateur
# ----------------------------------------------------------------------------------------------
class Core:
    LIB_BASE = 0x10000000
    STUB_BASE, STUB_SIZE = 0x08000000, 0x100000
    HEAP_BASE, HEAP_SIZE = 0x20000000, 0x10000000
    STACK_BASE, STACK_SIZE = 0x40000000, 0x800000
    TLS_BASE, TLS_SIZE = 0x41000000, 0x10000
    DATA_BASE, DATA_SIZE = 0x42000000, 0x10000
    FILE_BASE = 0x60000000
    DATA_IMPORTS = ('__stack_chk_guard',)

    def __init__(self, libdata, fs, log, assets_dir='/dq/assets', save_dir='/dq/save', real_files=None):
        from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_PROT_ALL
        from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_READ_UNMAPPED, UC_HOOK_MEM_WRITE_UNMAPPED, \
            UC_HOOK_MEM_FETCH_UNMAPPED
        import unicorn.arm64_const as C
        self.C = C
        self.XREG = [getattr(C, 'UC_ARM64_REG_X%d' % i) for i in range(31)]
        self.log, self.fs = log, fs
        self.real_files = real_files or {}
        self.assets_dir, self.save_dir = assets_dir.rstrip('/'), save_dir.rstrip('/')
        self.elf = Elf(libdata)
        uc = self.uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        uc.reg_write(C.UC_ARM64_REG_CPACR_EL1, 0x300000)  # autorise FP / NEON
        # memoire
        uc.mem_map(self.STUB_BASE, self.STUB_SIZE, UC_PROT_ALL)
        uc.mem_write(self.STUB_BASE, bytes.fromhex('c0035fd6') * (self.STUB_SIZE // 4))  # "ret" partout
        uc.mem_map(self.HEAP_BASE, self.HEAP_SIZE, UC_PROT_ALL)
        uc.mem_map(self.STACK_BASE, self.STACK_SIZE, UC_PROT_ALL)
        uc.mem_map(self.TLS_BASE, self.TLS_SIZE, UC_PROT_ALL)
        uc.mem_map(self.DATA_BASE, self.DATA_SIZE, UC_PROT_ALL)
        self.RET = self.STUB_BASE  # adresse de retour des appels (stub 0)
        self.stub_names = ['<retour>']
        self.stub_idx = {}
        self.data_ptr = self.DATA_BASE
        self.heap_ptr = self.HEAP_BASE
        self.sp_top = self.STACK_BASE + self.STACK_SIZE - 0x100
        tp = self.TLS_BASE + 0x100
        uc.mem_write(tp + 0x28, (0x5a3c9e7d11223300).to_bytes(8, 'little'))  # canari de pile (bionic)
        uc.reg_write(C.UC_ARM64_REG_TPIDR_EL0, tp)
        # lib
        self.stats = self.elf.relocate(self.LIB_BASE, self.stub_for)
        uc.mem_map(self.LIB_BASE, self.elf.size, UC_PROT_ALL)
        uc.mem_write(self.LIB_BASE, bytes(self.elf.img))
        # compteurs
        self.import_calls, self.unimpl = {}, {}
        self._arite = {}
        self.files, self.fopen_log = {}, []
        self.next_file = 0
        self.gl_calls, self.gl_count = [], {}
        self.hook_error = None
        self.last_fault = None
        uc.hook_add(UC_HOOK_CODE, self._stub_hook, None, self.STUB_BASE + 4, self.STUB_BASE + self.STUB_SIZE)
        uc.hook_add(UC_HOOK_MEM_READ_UNMAPPED | UC_HOOK_MEM_WRITE_UNMAPPED | UC_HOOK_MEM_FETCH_UNMAPPED,
                    self._mem_hook)

    # -- acces memoire
    def mem(self, a, n):
        return bytes(self.uc.mem_read(a, n))

    def mem_w(self, a, b):
        self.uc.mem_write(a, bytes(b))

    def read_cstr(self, a, limit=1 << 20):
        out = bytearray()
        while len(out) < limit:
            chunk = 64 - (a & 63)
            try:
                b = self.mem(a, chunk)
            except Exception:
                b = self.mem(a, 1)
            i = b.find(b'\0')
            if i >= 0:
                out += b[:i]
                return bytes(out)
            out += b
            a += len(b)
        return bytes(out)

    def malloc(self, n):
        n = (max(int(n), 1) + 15) & ~15
        p = self.heap_ptr
        if p + n > self.HEAP_BASE + self.HEAP_SIZE:
            raise MemoryError('tas epuise')
        self.heap_ptr += n
        return p

    def cstr(self, s):
        b = s.encode() if isinstance(s, str) else s
        p = self.malloc(len(b) + 1)
        self.mem_w(p, b + b'\0')
        return p

    # -- imports
    def stub_for(self, name, bind):
        if name in self.DATA_IMPORTS:
            p = self.data_ptr
            self.data_ptr += 16
            self.uc.mem_write(p, (0x5a3c9e7d11223300).to_bytes(8, 'little'))
            return p
        if name in self.stub_idx:
            return self.stub_idx[name]
        if bind == 2 and not hasattr(self, 'h_' + name):  # faible et inconnu -> NULL
            return 0
        addr = self.STUB_BASE + 4 * len(self.stub_names)
        self.stub_names.append(name)
        self.stub_idx[name] = addr
        return addr

    def _stub_hook(self, uc, address, size, user):
        try:
            name = self.stub_names[(address - self.STUB_BASE) // 4]
            self.import_calls[name] = self.import_calls.get(name, 0) + 1
            n = self._arite.get(name)      # on ne lit que les registres dont le gestionnaire a besoin (appel tres frequent)
            if n is None:
                g = getattr(self, 'h_' + name, None)
                try: n = (g.__func__.__code__.co_argcount - 1) if g is not None else 0
                except AttributeError: n = 8
                self._arite[name] = n
            regs = [uc.reg_read(self.XREG[i]) for i in range(n)]
            h = getattr(self, 'h_' + name, None)
            if h is None:
                self.unimpl[name] = self.unimpl.get(name, 0) + 1
                if self.unimpl[name] == 1:
                    self.log('  [import non gere] %s appele depuis %s' % (
                        name, self.where(uc.reg_read(self.XREG[30]))))
                ret = 0
            else:
                ret = h(*regs)
            if ret is not None:
                uc.reg_write(self.XREG[0], ret & MASK64)
        except Exception:
            self.hook_error = traceback.format_exc()
            uc.emu_stop()

    def _mem_hook(self, uc, access, address, size, value, user):
        self.last_fault = (access, address, size)
        return False

    def stack_arg(self, n):
        sp = self.uc.reg_read(self.C.UC_ARM64_REG_SP)
        return int.from_bytes(self.mem(sp + 8 * n, 8), 'little')

    # -- symboles
    def where(self, addr):
        if self.LIB_BASE <= addr < self.LIB_BASE + self.elf.size:
            off = addr - self.LIB_BASE
            i = bisect.bisect_right(self.elf.func_starts, off) - 1
            if i >= 0:
                s, sz, n = self.elf.funcs[i]
                return '%s+%#x' % (n, off - s)
            return 'lib+%#x' % off
        if self.STUB_BASE <= addr < self.STUB_BASE + self.STUB_SIZE:
            i = (addr - self.STUB_BASE) // 4
            return 'import:' + (self.stub_names[i] if i < len(self.stub_names) else '?')
        return '%#x' % addr

    # -- appels
    def export(self, name):
        off = self.elf.exports.get(name)
        return None if off is None else self.LIB_BASE + off

    def call(self, fn, *args, label='', timeout_s=90):
        from unicorn import UcError
        C, uc = self.C, self.uc
        for i, v in enumerate(args[:8]):
            uc.reg_write(self.XREG[i], int(v) & MASK64)
        uc.reg_write(C.UC_ARM64_REG_SP, self.sp_top)
        uc.reg_write(self.XREG[30], self.RET)
        self.hook_error, self.last_fault = None, None
        t0 = time.time()
        try:
            uc.emu_start(fn, self.RET, timeout=int(timeout_s * 1000000))
        except UcError as e:
            self.report_fault(e, label)
            return None
        if self.hook_error:
            self.log('!!! erreur dans un import emule pendant %s :\n%s' % (label, self.hook_error))
            return None
        pc = uc.reg_read(C.UC_ARM64_REG_PC)
        if pc != self.RET:
            self.log('!!! %s interrompu (duree max %ss ?) PC=%s' % (label, timeout_s, self.where(pc)))
            return None
        self.last_dt = time.time() - t0
        return uc.reg_read(self.XREG[0])

    def report_fault(self, e, label):
        C, uc = self.C, self.uc
        pc = uc.reg_read(C.UC_ARM64_REG_PC)
        self.log('!!! ERREUR D EMULATION dans %s : %s' % (label, e))
        self.log('    PC = %#x  (%s)' % (pc, self.where(pc)))
        try:
            self.log('    octets a PC : %s' % self.mem(pc, 4).hex())
        except Exception:
            pass
        if self.last_fault:
            kind = {16: 'lecture', 17: 'ecriture', 18: 'execution'}.get(self.last_fault[0], str(self.last_fault[0]))
            self.log('    acces invalide : %s a l adresse %#x (%d octets)' % (kind, self.last_fault[1], self.last_fault[2]))
        self.log('    LR = %s   SP = %#x' % (self.where(uc.reg_read(self.XREG[30])), uc.reg_read(C.UC_ARM64_REG_SP)))
        self.log('    x0-x8 : ' + ' '.join('%x' % uc.reg_read(self.XREG[i]) for i in range(9)))
        fp = uc.reg_read(self.XREG[29])
        self.log('    pile d appels (chaine FP) :')
        for _ in range(14):
            try:
                nxt = int.from_bytes(self.mem(fp, 8), 'little')
                ret = int.from_bytes(self.mem(fp + 8, 8), 'little')
            except Exception:
                break
            self.log('      %s' % self.where(ret))
            if nxt <= fp or nxt == 0:
                break
            fp = nxt

    def run_init(self):
        fns = self.elf.init_functions(self.LIB_BASE)
        ok = 0
        for f in fns:
            r = self.call(f, label='constructeur %s' % self.where(f))
            if r is None:
                return False
            ok += 1
        self.log('constructeurs executes : %d / %d' % (ok, len(fns)))
        return True

    # -- fichiers virtuels
    def norm(self, p):
        s = p.decode('latin-1').replace('\\', '/')
        for pre in (self.assets_dir, self.save_dir):
            if s.startswith(pre):
                s = s[len(pre):]
        parts = []
        for c in s.split('/'):
            if c in ('', '.'):
                continue
            if c == '..':
                if parts:
                    parts.pop()
                continue
            parts.append(c)
        return '/'.join(parts)

    def h_fopen(self, path, mode, *a):
        p, m = self.read_cstr(path), self.read_cstr(mode).decode()
        ps = p.decode('latin-1')
        if ps in self.real_files and 'w' not in m and 'a' not in m:
            fp = open(self.real_files[ps], 'rb')
            self.fopen_log.append((ps, True))
            self.next_file += 1
            h = self.FILE_BASE + 0x100 * self.next_file
            self.files[h] = {'fp': fp, 'name': ps}
            return h
        rel = self.norm(p)
        if 'w' in m or 'a' in m:
            data, w = bytearray(), True
        else:
            d = self.fs.open(rel) if self.fs else None
            if d is None:
                self.fopen_log.append((p.decode('latin-1'), False))
                return 0
            data, w = bytearray(d), False
        self.fopen_log.append((p.decode('latin-1'), True))
        self.next_file += 1
        h = self.FILE_BASE + 0x100 * self.next_file
        self.files[h] = {'d': data, 'pos': 0, 'w': w, 'name': rel}
        return h

    def h_fclose(self, h, *a):
        f = self.files.pop(h, None)
        if f and 'fp' in f:
            f['fp'].close()
        return 0

    def h_fread(self, ptr, size, n, h, *a):
        f = self.files.get(h)
        if not f or size == 0:
            return 0
        want = size * n
        if 'fp' in f:
            chunk = f['fp'].read(want)
            items = len(chunk) // size
            if len(chunk) != items * size:
                f['fp'].seek(items * size - len(chunk), 1)
                chunk = chunk[:items * size]
            if chunk:
                self.mem_w(ptr, chunk)
            return items
        chunk = bytes(f['d'][f['pos']:f['pos'] + want])
        items = len(chunk) // size
        chunk = chunk[:items * size]
        if chunk:
            self.mem_w(ptr, chunk)
        f['pos'] += len(chunk)
        return items

    def h_fwrite(self, ptr, size, n, h, *a):
        f = self.files.get(h)
        if not f:
            return 0
        f['d'][f['pos']:f['pos'] + size * n] = self.mem(ptr, size * n)
        f['pos'] += size * n
        return n

    def h_fseek(self, h, off, whence, *a):
        f = self.files.get(h)
        if not f:
            return MASK64
        if off >> 63:
            off -= 1 << 64
        if 'fp' in f:
            f['fp'].seek(off, whence & 3)
            return 0
        base = {0: 0, 1: f['pos'], 2: len(f['d'])}.get(whence & 3, 0)
        f['pos'] = max(0, base + off)
        return 0

    def h_ftell(self, h, *a):
        f = self.files.get(h)
        if f and 'fp' in f:
            return f['fp'].tell()
        return f['pos'] if f else MASK64

    # -- memoire / chaines
    def h_malloc(self, n, *a):
        return self.malloc(n)

    def h_calloc(self, n, m, *a):
        return self.malloc(n * m)

    def h_free(self, p, *a):
        return 0

    def h__Znam(self, n, *a):
        return self.malloc(n)

    h__Znwm = h__Znam

    def h__ZdaPv(self, p, *a):
        return 0

    h__ZdlPv = h__ZdaPv

    def h_memcpy(self, d, s, n, *a):
        if n:
            self.mem_w(d, self.mem(s, n))
        return d

    h_memmove = h_memcpy

    def h___memcpy_chk(self, d, s, n, dl, *a):
        return self.h_memcpy(d, s, n)

    def h_memset(self, d, c, n, *a):
        if n:
            self.mem_w(d, bytes([c & 0xff]) * n)
        return d

    def h_strlen(self, s, *a):
        return len(self.read_cstr(s))

    def h___strlen_chk(self, s, n, *a):
        return len(self.read_cstr(s))

    def h_strcmp(self, a, b, *x):
        s, t = self.read_cstr(a), self.read_cstr(b)
        return (s > t) - (s < t)

    def h_strncmp(self, a, b, n, *x):
        s, t = self.read_cstr(a)[:n], self.read_cstr(b)[:n]
        return (s > t) - (s < t)

    def h_strstr(self, a, b, *x):
        i = self.read_cstr(a).find(self.read_cstr(b))
        return 0 if i < 0 else a + i

    def h_strcpy(self, d, s, *a):
        self.mem_w(d, self.read_cstr(s) + b'\0')
        return d

    def h___strcpy_chk(self, d, s, n, *a):
        return self.h_strcpy(d, s)

    def h_strcat(self, d, s, *a):
        e = d + len(self.read_cstr(d))
        self.mem_w(e, self.read_cstr(s) + b'\0')
        return d

    def h___strcat_chk(self, d, s, n, *a):
        return self.h_strcat(d, s)

    def h_atoi(self, s, *a):
        m = re.match(rb'\s*[-+]?\d+', self.read_cstr(s))
        return int(m.group()) if m else 0

    def h_vsnprintf(self, d, size, fmt, va, *a):
        out = format_c(self, self.read_cstr(fmt), VaList(self, va))
        if size:
            self.mem_w(d, out[:size - 1] + b'\0')
        return len(out)

    def h___vsprintf_chk(self, d, flag, slen, fmt, va, *a):
        out = format_c(self, self.read_cstr(fmt), VaList(self, va))
        self.mem_w(d, out + b'\0')
        return len(out)

    # -- C++ / systeme
    def h___cxa_atexit(self, *a):
        return 0

    def h___cxa_finalize(self, *a):
        return None

    def h___cxa_guard_acquire(self, g, *a):
        return 1 if self.mem(g, 1)[0] == 0 else 0

    def h___cxa_guard_release(self, g, *a):
        self.mem_w(g, b'\x01')
        return None

    def h___stack_chk_fail(self, *a):
        self.hook_error = 'stack_chk_fail : pile corrompue (appele depuis %s)' % self.where(
            self.uc.reg_read(self.XREG[30]))
        self.uc.emu_stop()

    def h___android_log_write(self, prio, tag, text, *a):
        t = self.read_cstr(text).decode('utf-8', 'replace')
        self.log('  [log jeu] %s: %s' % (self.read_cstr(tag).decode('utf-8', 'replace'), t[:300]))
        return 0

    # -- OpenGL (comptage seulement)
    def h_glBindTexture(self, target, tex, *a):
        self.gl_count['glBindTexture'] = self.gl_count.get('glBindTexture', 0) + 1
        return None

    def h_glGetError(self, *a):
        return 0

    def h_glTexSubImage2D(self, target, level, xo, yo, w, h, fmt, typ, *a):
        pix = self.stack_arg(0)
        self.gl_count['glTexSubImage2D'] = self.gl_count.get('glTexSubImage2D', 0) + 1
        if len(self.gl_calls) < 40:
            self.gl_calls.append((target, level, xo, yo, w, h, fmt, typ, pix))
        return None


# ----------------------------------------------------------------------------------------------
# Scenario du test sur DQ4
# ----------------------------------------------------------------------------------------------
def find_apks(path_hint, here):
    cands = [path_hint] if path_hint else []
    cands += [os.path.join(here, '..', '..', 'jeux', 'DQ4', 'patched_apks'),
              os.path.join(here, '..', '..', 'jeux', 'DQ4', 'apks')]
    for c in cands:
        c = os.path.abspath(c)
        if os.path.isfile(os.path.join(c, 'split_config.arm64_v8a.apk')):
            return c
    return None


def summarize_buffer(core, name, ptr, nbytes, kind):
    data = core.mem(ptr, nbytes)
    if kind == 'f':
        vals = struct.unpack('<%df' % (nbytes // 4), data)
        nz = [(i, v) for i, v in enumerate(vals) if v != 0.0 and v == v]
    else:
        vals = struct.unpack('<%di' % (nbytes // 4), data)
        nz = [(i, v) for i, v in enumerate(vals) if v != 0]
    core.log('  tampon %-12s : %d valeurs non nulles' % (name, len(nz)))
    if nz:
        core.log('      premieres : ' + ', '.join('[%d]=%s' % (i, ('%.4g' % v) if kind == 'f' else v) for i, v in nz[:24]))


def run_dq(core, args):
    L = core.log
    e = core.elf
    L('--- 1. Chargement : %d symboles, %d fonctions exportees, %d constructeurs' % (
        e.nsyms, len(e.funcs), len(e.init_functions(0))))
    st = core.stats
    L('    relocations : %d RELATIVE, %d ABS64, %d GLOB_DAT, %d JUMP_SLOT ; inconnues : %s' % (
        st['RELATIVE'], st['ABS64'], st['GLOB_DAT'], st['JUMP_SLOT'], st['inconnues'] or 'aucune'))
    L('    imports externes (%d) : %s' % (len(st['externes']), ' '.join(sorted(st['externes']))))
    absent = [n for n in st['externes'] if not hasattr(core, 'h_' + n)]
    L('    imports SANS emulation : %s' % (' '.join(sorted(absent)) or 'aucun'))
    L('--- 2. Constructeurs C++')
    if not core.run_init():
        return False
    L('--- 3. Appels du jeu')
    f = core.export('IsLoadNativeCode')
    if f:
        L('IsLoadNativeCode() -> %s' % core.call(f, label='IsLoadNativeCode'))
    f = core.export('SetAmazonFlg')
    if f:
        core.call(f, 0, label='SetAmazonFlg')
    f = core.export('DevInit')
    L('DevInit(assets, save, langue=%d)' % args.lang)
    # Le 1er argument de DevInit est le chemin de l'ARCHIVE (APK de donnees) : le moteur lit son contenu lui-meme
    apk_virtual = next(iter(core.real_files), core.assets_dir)
    r = core.call(f, core.cstr(apk_virtual), core.cstr(core.save_dir), args.lang, label='DevInit')
    L('   -> %s' % r)
    if r is None:
        return False
    # Tampons de communication des menus : valeurs lues dans les constantes C# du jeu
    # (c_portNum=32, c_unityTotalSize=129, c_nativeTotalSize=1089, en demi-mots de 16 bits)
    f = core.export('MenuPortInit')
    if f:
        ubuf, nbuf = core.malloc(0x2000), core.malloc(0x2000)
        L('MenuPortInit(32, tampon_unity, 129, tampon_natif, 1089)')
        r = core.call(f, 32, ubuf, 129, nbuf, 1089, label='MenuPortInit')
        L('   -> %s' % r)
        core.menu_bufs = (ubuf, nbuf)
        if r is None:
            return False
    NB = 0x40000
    inp, outp, outf, strb = core.malloc(NB), core.malloc(NB), core.malloc(NB), core.malloc(0x10000)
    L('GameInit(input, output, output_float, str)')
    t0 = time.time()
    r = core.call(core.export('GameInit'), inp, outp, outf, strb, label='GameInit', timeout_s=120)
    L('   -> %s  (%.2f s)' % (r, time.time() - t0))
    if r is None:
        return False
    frames_ok = True
    for i in range(args.frames):
        r = core.call(core.export('GameUpdate'), 0, 0, label='GameUpdate (image %d)' % (i + 1))
        if r is None:
            L('   arret a l image %d' % (i + 1))
            frames_ok = False
            break
        if i < 3 or i == args.frames - 1:
            L('GameUpdate(0,0) image %d -> %s  (%.3f s)' % (i + 1, r, core.last_dt))
    L('--- 4. Etat des tampons de sortie')
    summarize_buffer(core, 'output', outp, NB, 'i')
    summarize_buffer(core, 'output_float', outf, NB, 'f')
    s = core.read_cstr(strb, 0x10000)
    L('  tampon str : %r' % s[:200])
    L('--- 4b. Sondes : le moteur dessine-t-il lui-meme ?')
    f = core.export('GetMessageCanvas')
    if f:
        r = core.call(f, label='GetMessageCanvas')
        L('GetMessageCanvas() -> %s' % (None if r is None else '%#x' % r))
    f = core.export('UnityRenderEvent')
    if f:
        for ev in (0, 1, 2):
            n0 = core.gl_count.get('glTexSubImage2D', 0)
            r = core.call(f, ev, label='UnityRenderEvent(%d)' % ev)
            L('UnityRenderEvent(%d) -> %s ; mises a jour de texture : %d' % (
                ev, r, core.gl_count.get('glTexSubImage2D', 0) - n0))
            if r is None:
                break
    return frames_ok


def final_report(core):
    L = core.log
    L('--- 5. Bilan')
    L('imports appeles : ' + ', '.join('%s x%d' % kv for kv in sorted(core.import_calls.items(), key=lambda kv: -kv[1])))
    if core.unimpl:
        L('imports NON GERES (a ajouter) : ' + ', '.join(core.unimpl))
    from collections import Counter
    ok = Counter(p for p, f in core.fopen_log if f)
    ko = Counter(p for p, f in core.fopen_log if not f)
    L('fichiers ouverts : %d trouves, %d introuvables' % (sum(ok.values()), sum(ko.values())))
    for p, n in list(ok.items())[:40]:
        L('   ok  %s  (x%d)' % (p, n))
    for p, n in list(ko.items())[:40]:
        L('   !!  %s  (x%d)' % (p, n))
    if core.gl_count:
        L('appels OpenGL : %s' % core.gl_count)
        for c in core.gl_calls[:15]:
            L('   glTexSubImage2D cible=%#x niveau=%d x=%d y=%d %dx%d format=%#x type=%#x' % c[:8])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apks')
    ap.add_argument('--lib')
    ap.add_argument('--lang', type=int, default=1)
    ap.add_argument('--frames', type=int, default=30)
    ap.add_argument('--variant', default='')
    ap.add_argument('--dry', action='store_true')
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(errors='replace')
    except Exception:
        pass
    here = os.path.dirname(os.path.abspath(__file__))
    L = Log(os.path.join(here, 'rapport_test.txt'))
    L('Test de faisabilite - portage PC de DQ4 - %s' % time.strftime('%Y-%m-%d %H:%M:%S'))
    L('Python %s' % sys.version.split()[0])
    fs = None
    real_files = {}
    if args.lib:
        libdata = open(args.lib, 'rb').read()
        L('Bibliotheque : %s' % args.lib)
    else:
        d = find_apks(args.apks, here)
        if not d:
            L("APK introuvables : lance ce test depuis DQ Patch\\portage_pc\\test_faisabilite, "
              "ou donne --apks <dossier contenant split_config.arm64_v8a.apk>")
            return 2
        L('APK : %s' % d)
        zname = 'lib/arm64-v8a/libdq4core%s.so' % (('_' + args.variant) if args.variant else '')
        libdata = zipfile.ZipFile(os.path.join(d, 'split_config.arm64_v8a.apk')).read(zname)
        L('Bibliotheque : %s (%d octets)' % (zname, len(libdata)))
        zl = [os.path.join(d, n) for n in ('split_UnityDataAssetPack.apk', 'base.apk')]
        zl = [z for z in zl if os.path.isfile(z)]
        fs = ApkFs(zl) if zl else None
        if os.path.isfile(zl[0] if zl else ''):
            real_files = {'/dq/assets.apk': zl[0]}
        if not zl:
            L("ATTENTION : split_UnityDataAssetPack.apk introuvable, les fichiers de donnees ne seront pas lus")
    if args.dry:
        elf = Elf(libdata)
        fake = {}

        def stub(name, bind):
            fake.setdefault(name, 0x08000000 + 4 * (len(fake) + 1))
            return fake[name]
        st = elf.relocate(Core.LIB_BASE, stub)
        L('[dry] %d symboles, relocations : %s' % (elf.nsyms, {k: v for k, v in st.items() if k != 'externes'}))
        L('[dry] imports : ' + ' '.join(sorted(st['externes'])))
        L('[dry] constructeurs : %d' % len(elf.init_functions(Core.LIB_BASE)))
        return 0
    try:
        import unicorn
    except Exception:
        L("Le module 'unicorn' n'est pas installe (pip install unicorn).")
        return 3
    core = Core(libdata, fs, L, real_files=real_files)
    success = False
    try:
        success = run_dq(core, args)
    except Exception:
        L('!!! exception Python :\n' + traceback.format_exc())
    final_report(core)
    L('')
    L('VERDICT : ' + ('REUSSI - le moteur du jeu s execute sur PC, sans Unity (voie A viable)' if success else
                      'ECHEC - voir le message d erreur plus haut'))
    L('')
    L('Rapport complet : %s' % os.path.join(here, 'rapport_test.txt'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
