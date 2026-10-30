"""Journal structure des paquets moteur->Unity (coupes aux ecritures des curseurs)."""
import struct, unicorn
CUR_I, CUR_F = 0x258278, 0x258280


class PacketLog:
    def __init__(self, core):
        self.core = core; L = core.LIB_BASE
        self.ci, self.cf = L + CUR_I, L + CUR_F
        self.ev = []
        core.uc.hook_add(unicorn.UC_HOOK_MEM_WRITE, self._w, None, self.ci, self.ci + 7)
        core.uc.hook_add(unicorn.UC_HOOK_MEM_WRITE, self._w2, None, self.cf, self.cf + 7)
        g = core.export('_Z14UnityGetPacketi')
        core.uc.hook_add(unicorn.UC_HOOK_CODE, lambda uc, a, s, u: self.ev.append(('G', 0)), None, g, g)
    def _w(self, uc, acc, addr, size, value, user):
        if size == 8: self.ev.append(('I', value & 0xffffffffffffffff))
    def _w2(self, uc, acc, addr, size, value, user):
        if size == 8: self.ev.append(('F', value & 0xffffffffffffffff))
    def begin(self):
        self.ev = []
    def packets(self):
        """-> liste [type, ints sans le type, floats] pour l'image qui vient d'etre calculee.
        Les flottants vont au paquet "ouvert" (UnityGetPacket appele, pas encore valide) sinon au dernier paquet valide."""
        c = self.core
        out = []; pi = c.outp + 4; pf = c.outf; pend = []; opened = False
        for kind, val in self.ev:
            if kind == 'G': opened = True
            elif kind == 'F' and val > pf:
                n = (val - pf) // 4
                fl = list(struct.unpack('<%df' % n, c.mem(pf, n * 4))); pf = val
                if opened: pend += fl
                elif out: out[-1][2] += fl
            elif kind == 'I' and pi < val <= c.outp + 0x40000:
                n = (val - pi) // 4
                ints = list(struct.unpack('<%di' % n, c.mem(pi, n * 4)))
                out.append([ints[0], ints[1:], pend]); pend = []; pi = val; opened = False
        if pend and out: out[-1][2] += pend
        return out
