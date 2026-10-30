"""Faux client Unity pour tester le pont (dq4_pc/pont.py) sans Unity : se connecte, lit les trames, envoie des touches.
Usage : python pont_client_test.py [--port 7740] [--images 300] [--script "20:0x10,22:0,60:0x2"] [--dump trames.jsonl]
  --script : "image:masque,..." (masque de touches maintenues : HAUT 1, BAS 2, GAUCHE 4, DROITE 8, A 0x10, B 0x20)"""
import argparse, json, socket, time, zlib, base64


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=7740)
    ap.add_argument('--images', type=int, default=300)
    ap.add_argument('--script', default='')
    ap.add_argument('--dump', default=None)
    ap.add_argument('--attente', type=float, default=120.0, help='secondes max pour se connecter')
    a = ap.parse_args()
    pts = sorted((int(x.split(':')[0]), int(x.split(':')[1], 0)) for x in a.script.split(',') if x)
    fin = time.time() + a.attente; s = None
    while time.time() < fin:
        try: s = socket.create_connection(('127.0.0.1', a.port), timeout=2); break
        except OSError: time.sleep(0.5)
    if s is None: raise SystemExit('pont introuvable')
    s.settimeout(120)
    f = s.makefile('rb'); dump = open(a.dump, 'w') if a.dump else None
    masque = 0; n = 0; carte = None; nc = None; vu = set()
    while n < a.images:
        lig = f.readline()
        if not lig: print('connexion fermee'); break
        if dump: dump.write(lig.decode('utf-8'))
        t = json.loads(lig)
        if 'hello' in t: print('hello', t); continue
        if 'attente' in t and 'i' not in t: print('attente :', t['attente']); continue
        while pts and pts[0][0] <= n:
            masque = pts.pop(0)[1]; s.sendall(('K %d\n' % masque).encode()); print('image %d : touches %#x' % (n, masque))
        if t['nc'] != nc or t['carte'] != carte:
            nc, carte = t['nc'], t['carte']; print('image %d : carte %r (chargement n=%d), partie %#x, champ %d' % (n, carte, nc, t['partie'], t['champ']))
        sp = tuple((q['s'], q['id']) for q in t.get('spr', []))
        if sp not in vu: vu.add(sp); print('image %d : sprites %s, camera %s -> %s' % (n, sp, t.get('cp'), t.get('cc')))
        m = t['msg']
        if m.get('b'):
            raw = zlib.decompress(base64.b64decode(m['b']), -15)
            print('image %d : message n=%d visible=%s bitmap %dx%d (%d octets, %d pixels non nuls)' % (n, m['n'], m['v'], m['w'], m['h'], len(raw), sum(1 for b in raw if b)))
        if t.get('an'): print('image %d : animations d\'objets %s' % (n, t['an']))
        for mn in t['menus']:
            cle = ('menu', mn['p'], tuple(l['t'] for l in mn['l']), mn['cur'], mn['a'])
            if cle not in vu: vu.add(cle); print('image %d : menu port %d actif=%s curseur=%d %s' % (n, mn['p'], mn['a'], mn['cur'], [(l['t'], l['d']) for l in mn['l']]))
        n += 1
    s.close()
    print('termine : %d trames, derniere carte %r' % (n, carte))


if __name__ == '__main__':
    main()
