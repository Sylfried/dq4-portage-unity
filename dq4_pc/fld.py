import struct, sys
def chunks(raw):
    """FLD : suite de chunks 'TAG4' + taille(4, taille totale du chunk) ; FHDR a une taille propre."""
    out = []; p = 0
    while p + 8 <= len(raw):
        tag = raw[p:p+4]
        if not tag.isalpha(): break
        sz = struct.unpack_from('<I', raw, p + 4)[0]
        if sz < 8 or p + sz > len(raw): out.append((tag.decode(), p, len(raw) - p)); break
        out.append((tag.decode(), p, sz)); p = (p + sz + 15) & ~15
    return out
def map_objs(raw):
    cs = {t: (p, s) for t, p, s in chunks(raw)}
    p, s = cs['FMAP']
    nobj = struct.unpack_from('<I', raw, p + 8)[0]
    objs = []
    for i in range(nobj):
        o = p + 0x20 + 0x50 * i
        pos = struct.unpack_from('<3f', raw, o); oid = struct.unpack_from('<i', raw, o + 12)[0]
        rot = struct.unpack_from('<3f', raw, o + 16); mdl, com = struct.unpack_from('<hH', raw, o + 28)
        scl = struct.unpack_from('<3f', raw, o + 32); uid = struct.unpack_from('<i', raw, o + 44)[0]
        flag, prio = struct.unpack_from('<HH', raw, o + 48); rest = raw[o + 52:o + 80]
        objs.append(dict(i=i, pos=pos, id=oid, rot=rot, mdl=mdl, com=com, scl=scl, uid=uid, flag=flag, prio=prio, rest=rest.hex()))
    return nobj, objs
if __name__ == '__main__':
    raw = open(sys.argv[1], 'rb').read()
    print([(t, hex(p), hex(s)) for t, p, s in chunks(raw)])
    n, objs = map_objs(raw); print('nobj', n)
    for o in objs: print(o)
