"""Pont moteur -> Unity (piste 3, issue #57).
Le moteur du jeu (libdq4core.so) tourne ici, emule en Python ; Unity (projet `unity_pc/`) ne fait que l'affichage.

Protocole (TCP 127.0.0.1, texte, une ligne par message) :
  Python -> Unity : {"hello":1,...} a la connexion, puis une ligne JSON par image du moteur (30 Hz), voir `trame()`.
                    {"attente":"..."} pendant un export long (carte jamais vue).
  Unity -> Python : "K <masque>"  touches maintenues (HAUT 1, BAS 2, GAUCHE 4, DROITE 8, A 0x10, B 0x20)
                    "S <n>"       charge la sauvegarde n (1-5)
                    "R"           demande un renvoi complet de l'etat
Les donnees du jeu (cartes, sprites) sont lues dans les APK du joueur puis exportees dans `--export` (voir pont_export.py)."""
import os, sys, time, json, glob, zlib, base64, socket, select, argparse, zipfile, random, struct
import numpy as np
import moteur as MO
import donnees as DO
import pont_export as PE

PROTO = 1
BTN_PAROLE, BTN_MENU = 0x100, 0x800        # arg2 de GameUpdate : parler / examiner (exactement 0x100), ouvrir le menu de commandes
HZ = 30
MORT_APRES, MORT_FONDU = 8, 12      # combat : un monstre sans nouveau maillage depuis 8 images est vaincu ; il disparait en 12 images


def log(*a):
    print(*a, flush=True)


def lignes_menu(f):
    """Cases d'une fenetre -> lignes (numero de ligne du moteur, texte, etiquette, valeur, est_un_titre). Copie de fenetre.py."""
    rows = {}
    for (r, c), t in f['cases'].items(): rows.setdefault(r, {})[c] = t
    out = []
    for r in sorted(rows):
        cols = rows[r]
        pre, lab, val = cols.get(5, ''), cols.get(2, ''), cols.get(3, '')
        princ = ' '.join(' '.join(t.split('\n')) for c, t in sorted(cols.items()) if c not in (2, 3, 5) and t)
        if not (princ or val or lab): continue
        out.append((r, (pre + ' ' + princ).strip(), lab, val, princ.rstrip().endswith('?')))
    return [l for l in out if l[4]] + [l for l in out if not l[4]]


def deflate_brut(octets):
    c = zlib.compressobj(6, zlib.DEFLATED, -15)
    return c.compress(octets) + c.flush()


class Pont:
    def __init__(self, a):
        self.a = a
        log('Chargement du moteur (quelques secondes)...')
        self.donnees = DO.Donnees(None, a.cache, log=log)
        self.moteur = MO.Moteur(a.apk, a.saves_lecture, a.saves_ecriture)
        self.donnees.apk = self.moteur.apk_donnees
        self.export = PE.Exporteur(self.donnees, a.export, log)
        self.pad = 0; self.pad_prec = 0; self.rep = {}; self.menu_relacher = None
        self.auto = bool(a.rencontre); self.auto_n = 0; self.auto_dir = 0; self.hasard = random.Random(4)      # --rencontre : marche au hasard sur la carte du monde jusqu'au premier combat
        self.client = None; self.tampon = b''
        self.nc_exporte = None; self.msg_hash = None; self.msg_n = 0
        self.mon_h = {}                    # (emplacement, champ) -> empreinte de ce que Unity a deja recu des maillages de monstres
        self.mon_maj = {}                 # emplacement -> derniere image du moteur ou son maillage (paquet 22) a ete mis a jour
        paquets = self.moteur.pk.packets
        def espion():
            pq = paquets()
            for t, ar, f in pq:
                if t == 22 and ar: self.mon_maj[ar[0]] = self.moteur.images
            return pq
        self.moteur.pk.packets = espion
        if a.slot: self.charger(a.slot)
        for nom in [x for x in (a.preparer or '').split(',') if x]:
            try: self.export.carte(nom)
            except Exception as ex: log('export impossible (%s) : %s' % (nom, ex))
        for s in list(self.moteur.etat.sprites.values()): self.export.feuille(s['id'])

    # ---- moteur
    def charger(self, slot):
        ok = self.moteur.charger_sauvegarde(slot)
        log('sauvegarde %d chargee : %s' % (slot, 'oui' if ok else 'NON (data%d.dat introuvable ou invalide dans %s)' % (slot, self.a.saves_lecture)))
        if ok:
            for _ in range(6): self.moteur.step(0, 0)

    def saisie_menu(self, port, decl):
        """Meme logique que la visionneuse : le curseur appartient a l'interface, le moteur est prevenu par messages."""
        mo = self.moteur; f = mo.ui.f[port]
        sel = [l[0] for l in lignes_menu(f) if not l[4]]
        cle = (port, f['ordre'])
        if sel and (cle != getattr(self, 'focus_vu', None) or f['cur'] not in sel):
            self.focus_vu = cle
            if f['cur'] not in sel: f['cur'] = sel[0]; mo.envoyer_menu(port, 1, sel[0], 0)
        if sel and (decl & (MO.HAUT | MO.BAS)):
            i = sel.index(f['cur']) if f['cur'] in sel else 0
            i = (i + (1 if decl & MO.BAS else -1)) % len(sel)
            f['cur'] = sel[i]; mo.envoyer_menu(port, 1, sel[i], 0)
        elif decl & MO.A:
            mo.envoyer_menu(port, 1, -1, 0x10); self.menu_relacher = port
        elif decl & MO.B:
            mo.envoyer_menu(port, 1, -1, 0x20); self.menu_relacher = port

    def tick(self):
        """Une image du moteur avec les touches venues d'Unity (meme aiguillage que la visionneuse)."""
        mo = self.moteur; p = self.pad
        if self.auto:
            e = mo.etat
            if e.partie == MO.PARTIE_COMBAT:
                self.auto = False; log('Combat atteint : a toi de jouer.')
            else:
                if self.auto_n % 45 == 0: self.auto_dir = self.hasard.choice((MO.HAUT, MO.BAS, MO.GAUCHE, MO.DROITE))
                self.auto_n += 1
                mo.step(self.auto_dir if e.partie == MO.PARTIE_CHAMP else 0, 0)
                return
        decl = p & ~self.pad_prec
        for bit in (MO.HAUT, MO.BAS, MO.GAUCHE, MO.DROITE):       # repetition des directions maintenues (curseurs de menu)
            if p & bit:
                self.rep[bit] = self.rep.get(bit, 0) + 1
                if self.rep[bit] > 12 and (self.rep[bit] - 12) % 4 == 0: decl |= bit
            else: self.rep[bit] = 0
        self.pad_prec = p
        if self.menu_relacher is not None:
            mo.envoyer_menu(self.menu_relacher, 1, -1, 0); self.menu_relacher = None
        port = mo.menu_actif() if not mo.msg_visible else None
        if port is not None:
            self.saisie_menu(port, decl); p = decl = 0
        elif not mo.msg_visible:
            if decl & MO.A: decl = BTN_PAROLE
            elif decl & MO.B: decl = BTN_MENU
            p &= ~(MO.A | MO.B)
        mo.step(p, decl)

    # ---- trame envoyee a Unity
    def trame(self, neuf=False):
        mo = self.moteur; e = mo.etat
        champ = e.partie == MO.PARTIE_CHAMP and e.champ is not None
        t = {'i': mo.images, 'partie': e.partie, 'nc': e.cartes_chargees, 'carte': e.carte or '', 'champ': 1 if champ else 0,
             'fond': [round(x, 4) for x in e.fond]}
        if not champ and e.carte and (neuf or self.nc_exporte != e.cartes_chargees):
            if not self.export.est_exportee(e.carte): self.envoyer({'attente': 'Export de la carte %s (une seule fois)...' % e.carte})
            try: self.export.carte(e.carte)
            except Exception as ex:
                log('export impossible (%s) : %s' % (e.carte, ex)); t['info'] = 'export impossible (%s) : %s' % (e.carte, ex)
            self.nc_exporte = e.cartes_chargees
        r = lambda v: [round(float(x), 5) for x in v]
        if self.auto: t['info'] = "Carte du monde (non affichee pour l'instant) : le pont marche tout seul jusqu'au premier combat..."
        if e.cam_pos is not None: t['cp'] = r(e.cam_pos)
        if e.cam_cible is not None: t['cc'] = r(e.cam_cible)
        if e.cam_rot is not None: t['cr'] = r(e.cam_rot)
        if e.partie == MO.PARTIE_COMBAT:
            if e.cam_fov is not None: t['fov'] = round(float(e.cam_fov), 4)
            if not any(e.fond):                                   # le moteur n'envoie pas de fond en combat : estimation tiree du decor
                f = self.export.fond(e.carte or '')
                if f: t['fond'] = [round(x, 4) for x in f]
            t['mon'] = self.monstres(e, neuf, r)
        else: self.mon_h.clear(); self.mon_maj.clear()
        if not champ:
            spr = []
            for slot, s in sorted(e.sprites.items()):
                if s['pos'] is None: continue
                nl = self.export.feuille(s['id'])
                if not nl: continue
                spr.append({'s': slot, 'id': s['id'], 'nl': nl, 'p': r(s['pos']), 'c': r(s['col']), 'pose': s['pose'], 'dir': s['dir']})
            t['spr'] = spr
        if e.evt_anim:
            t['an'] = [v for a in e.evt_anim for v in a]; e.evt_anim.clear()
        msg = {'v': bool(mo.msg_visible), 'n': self.msg_n}
        a = self.lire_message()
        if a is not None:
            h = hash(a.tobytes())
            if h != self.msg_hash or neuf:
                if h != self.msg_hash: self.msg_n += 1
                self.msg_hash = h; msg['n'] = self.msg_n
                msg['w'], msg['h'] = int(a.shape[1]), int(a.shape[0])
                msg['b'] = base64.b64encode(deflate_brut(np.ascontiguousarray(a).tobytes())).decode('ascii')
        t['msg'] = msg
        actif = mo.menu_actif(); menus = []
        for port, f in mo.ui.visibles():
            ls = lignes_menu(f)
            if not ls: continue
            menus.append({'p': port, 'a': port == actif, 'cur': f['cur'],
                          'l': [{'r': l[0], 't': l[1], 'd': ' '.join(x.replace('\n', ' ') for x in (l[2], l[3]) if x), 'ti': bool(l[4])} for l in ls]})
        t['menus'] = menus
        return t

    def lire_message(self):
        """Comme Moteur.lire_message, mais le canevas du texte (256 lignes) est CIRCULAIRE : le defilement (`defil`) continue au-dela de sa hauteur
        et le texte revient en haut. Lu sans bouclage, le texte disparait des que le combat a fait defiler plus d'une page."""
        mo = self.moteur
        a = mo.lire_canevas()
        if a is None: return None
        c = mo.core
        i16 = lambda ad: struct.unpack('<h', c.mem(ad, 2))[0]
        lignes = i16(mo.L + 0x2523a0) + 1; lh = i16(mo.obj_msg + 0x42); defil = i16(mo.obj_msg + 0x4a)
        if not (1 <= lignes <= 8 and 8 <= lh <= 64): lignes, lh, defil = 4, 28, -3
        haut = max(0, defil + 3)
        return np.take(a, np.arange(haut, haut + lignes * lh), axis=0, mode='wrap')

    def monstres(self, e, neuf, r):
        """Monstres du combat (paquets 21-26) : un maillage dynamique par emplacement. Les gros tableaux (sommets `vb` = float32 x 5 [u, v, x, y, z],
        indices `ib` = int32, opacite par quad `qb` = octets) ne sont envoyes que lorsqu'ils changent ; Unity garde le dernier recu."""
        if neuf: self.mon_h.clear()
        out = []
        for slot, mm in sorted(e.maillages.items()):
            mid = e.modeles.get(slot)
            if mid is None or not self.export.monstre(mid): continue
            st = e.mesh_etat.get(slot) or {'shader': 1, 'vis': True, 'col': (1.0, 1.0, 1.0), 'off': (0.0, 0.0)}
            # un monstre vaincu ne recoit plus de maillage (les vivants en recoivent a chaque image, au plus 3 images d'interruption) :
            # on le fait disparaitre en fondu (le moteur ne l'enleve qu'a la fin du combat)
            age = self.moteur.images - self.mon_maj.setdefault(slot, self.moteur.images)
            al = 1.0 if age < MORT_APRES else max(0.0, 1.0 - (age - MORT_APRES) / float(MORT_FONDU))
            d = {'s': slot, 'm': int(mid), 'sh': int(st['shader']), 'vi': 1 if (st['vis'] and al > 0) else 0,
                 'c': r(st['col']), 'al': round(al, 3), 'o': r(st['off']), 'p': r(mm['pos'])}
            V = np.ascontiguousarray(np.array(mm['v'], np.float32).reshape(len(mm['v']), -1)[:, :5])
            for cle, arr in (('vb', V), ('ib', np.array(mm['idx'], np.int32)), ('qb', np.array(mm.get('col') or [255], np.uint8))):
                b = arr.tobytes(); h = hash(b)
                if self.mon_h.get((slot, cle)) != h:
                    self.mon_h[(slot, cle)] = h; d[cle] = base64.b64encode(b).decode('ascii')
            out.append(d)
        return out

    # ---- reseau
    def ecouter(self):
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # Windows : SO_REUSEADDR laisserait deux ponts partager le meme port ; on prend le port en exclusivite
        if os.name == 'nt': self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else: self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try: self.srv.bind(('127.0.0.1', self.a.port))
        except OSError as ex:
            raise SystemExit('Le port %d est deja utilise (un autre pont tourne-t-il ?) : %s' % (self.a.port, ex))
        self.srv.listen(1); self.srv.setblocking(False)
        log('Pont pret sur 127.0.0.1:%d. Lance Unity (Play) : le jeu apparait la-bas. Ctrl+C pour arreter.' % self.a.port)

    def accepter(self):
        try: c, adr = self.srv.accept()
        except (BlockingIOError, InterruptedError): return False
        except OSError: return False
        self.fermer_client()
        c.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1); c.settimeout(2.0)
        self.client = c; self.tampon = b''; self.pad = 0; self.pad_prec = 0
        self.msg_hash = None
        log('Unity connecte.')
        self.envoyer({'hello': 1, 'proto': PROTO, 'export': self.export.dossier, 'hz': HZ})
        return True

    def fermer_client(self):
        if self.client is not None:
            try: self.client.close()
            except OSError: pass
            self.client = None; self.pad = 0

    @staticmethod
    def _fini(o):
        """JsonUtility (Unity) refuse NaN/Infinity : on les remplace par 0."""
        if isinstance(o, float): return o if o - o == 0 else 0.0
        if isinstance(o, dict): return {k: Pont._fini(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)): return [Pont._fini(v) for v in o]
        return o

    def envoyer(self, obj):
        if self.client is None: return
        try:
            try: texte = json.dumps(obj, separators=(',', ':'), allow_nan=False)
            except ValueError: texte = json.dumps(self._fini(obj), separators=(',', ':'), allow_nan=False)
            self.client.sendall((texte + '\n').encode('utf-8'))
        except (OSError, socket.timeout) as ex:
            log('Unity deconnecte (%s).' % ex); self.fermer_client()

    def lire_client(self):
        if self.client is None: return
        try:
            while select.select([self.client], [], [], 0)[0]:
                d = self.client.recv(4096)
                if not d: log('Unity deconnecte.'); self.fermer_client(); return
                self.tampon += d
        except (OSError, socket.timeout) as ex:
            log('Unity deconnecte (%s).' % ex); self.fermer_client(); return
        while b'\n' in self.tampon:
            lig, self.tampon = self.tampon.split(b'\n', 1)
            m = lig.decode('utf-8', 'replace').strip().split()
            if not m: continue
            if m[0] == 'K' and len(m) > 1:
                try: self.pad = int(m[1]) & 0x3f
                except ValueError: pass
            elif m[0] == 'S' and len(m) > 1:
                try: self.charger(int(m[1])); self.nc_exporte = None
                except ValueError: pass
            elif m[0] == 'R':
                self.msg_hash = None; self.nc_exporte = None

    def boucle(self):
        self.ecouter()
        prochain = time.perf_counter(); neuf = True
        try:
            while True:
                if self.accepter(): neuf = True
                self.lire_client()
                if self.client is None:
                    time.sleep(0.05); prochain = time.perf_counter(); continue       # personne ne regarde : le moteur attend
                self.tick()
                self.envoyer(self.trame(neuf)); neuf = False
                prochain += 1.0 / HZ
                reste = prochain - time.perf_counter()
                if reste > 0: time.sleep(reste)
                elif reste < -0.2: prochain = time.perf_counter()
        except KeyboardInterrupt:
            log('Arret.')
        finally:
            self.fermer_client()


def extraire_datapack(apk, export):
    """Copie datapack.unity3d (AssetBundle Android) hors de l'APK, pour l'essai de chargement direct dans Unity (menu DQ4)."""
    pack = os.path.join(apk, 'split_UnityDataAssetPack.apk')
    cible = os.path.join(export, 'datapack.unity3d')
    os.makedirs(export, exist_ok=True)
    with zipfile.ZipFile(pack) as z, z.open(DO.DATAPACK) as src, open(cible, 'wb') as dst:
        while True:
            b = src.read(1 << 20)
            if not b: break
            dst.write(b)
    log('datapack extrait : %s (%.1f Mo)' % (cible, os.path.getsize(cible) / 1e6))


def main():
    ici = os.path.dirname(os.path.abspath(__file__))
    jeux = os.path.normpath(os.path.join(ici, '..', 'jeux', 'DQ4'))                    # 1) dossier jeux/ a la racine du depot (ignore par git)
    if not os.path.isdir(jeux): jeux = os.path.normpath(os.path.join(ici, '..', '..', 'jeux', 'DQ4'))   # 2) sinon jeux/ a cote du depot
    ap = argparse.ArgumentParser(description='Pont moteur -> Unity (piste 3)')
    ap.add_argument('--apk', default=os.path.join(jeux, 'patched_apks'), help='dossier des 3 APK (version francaise)')
    ap.add_argument('--saves-lecture', default=None, help='dossier contenant data1.dat... (par defaut : la plus recente sauvegarde de jeux/DQ4/saves)')
    ap.add_argument('--saves-ecriture', default=os.path.join(ici, 'sauvegardes'))
    ap.add_argument('--cache', default=os.path.join(ici, 'cache'))
    ap.add_argument('--export', default=None, help='dossier d\'export lu par Unity (par defaut <cache>/unity)')
    ap.add_argument('--slot', type=int, default=1, help='numero de sauvegarde a charger (1-5), 0 = aucune')
    ap.add_argument('--port', type=int, default=7740)
    ap.add_argument('--rencontre', action='store_true', help="(essai combat) marche au hasard sur la carte du monde jusqu'au premier combat, puis rend la main (utiliser avec --slot 4)")
    ap.add_argument('--preparer', default='', help='cartes a exporter avant de demarrer, ex. "mak1f1,maout"')
    ap.add_argument('--forcer-export', action='store_true', help='refait l\'export des cartes demandees par --preparer meme s\'il existe')
    ap.add_argument('--extraire-datapack', action='store_true', help='copie datapack.unity3d dans le dossier d\'export puis quitte')
    a = ap.parse_args()
    if a.export is None: a.export = os.path.join(a.cache, 'unity')
    if a.saves_lecture is None:
        dossiers = sorted(glob.glob(os.path.join(jeux, 'saves', '*')))
        a.saves_lecture = dossiers[-1] if dossiers else a.saves_ecriture
    if a.extraire_datapack:
        extraire_datapack(a.apk, a.export); return
    p = Pont(a)
    if a.forcer_export:
        for nom in [x for x in a.preparer.split(',') if x]: p.export.carte(nom, forcer=True)
    p.boucle()


if __name__ == '__main__':
    main()
