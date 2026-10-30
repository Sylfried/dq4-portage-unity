"""Le moteur du jeu (libdq4core.so, ARM64) execute dans un emulateur + decodage de ce qu'il demande a Unity."""
import os, re, struct, zipfile, unicorn
import numpy as np
import emu_core as T
from pktlog import PacketLog

CUR_I, CUR_F = 0x258278, 0x258280          # curseurs d'ecriture (entiers / flottants) dans la bibliotheque
PARTIE_COMBAT = 0xd
PARTIE_CHAMP = 0xe                         # FieldPart : carte du monde
GLOBAL_PTR = 0x23d438                       # singleton Global
NOM_RESSOURCE = 0x258290                    # nom de la ressource carte demandee a Unity (ex. m/ma/in/mak1f1)
LIB_APK = 'lib/arm64-v8a/libdq4core.so'

# touches (masque envoye a GameUpdate) : haut, bas, gauche, droite verifies ; A/B a confirmer
HAUT, BAS, GAUCHE, DROITE, A, B = 0x1, 0x2, 0x4, 0x8, 0x10, 0x20
OBJ_MESSAGE = 0x252330                     # objet SDrawCTRL (canevas de texte des messages) dans la bibliotheque
CANEVAS_L, CANEVAS_H = 512, 512            # texture du canevas de message d'Unity (MenuMessage : Texture2D(512, 512)) ; le moteur y ecrit en boucle
PILE_SHORTS = 4096                         # taille reelle allouee pour la file des messages (Unity en annonce 1089)
DUREE_ANIM = 18                            # images (30 Hz) avant de dire au moteur qu'une animation d'objet (porte...) est finie


class Silencieux:
    def __init__(self): self.lignes = []
    def __call__(self, *a):
        self.lignes.append(' '.join(str(x) for x in a)); del self.lignes[:-200]


class CoreJeu(T.Core):
    """Core + sauvegardes : lecture dans un dossier reel, ecriture persistante (jamais dans les originaux)."""
    dossier_lecture = None
    dossier_ecriture = None

    def h_fopen(self, path, mode, *a):
        p = self.read_cstr(path).decode('latin-1'); m = self.read_cstr(mode).decode()
        mm = re.search(r'(data\d+\.dat)$', p)
        if mm:
            nom = mm.group(1)
            if 'w' in m or 'a' in m:
                r = super().h_fopen(path, mode, *a)
                if r: self.files[r]['persist'] = os.path.join(self.dossier_ecriture, nom)
                return r
            for d in (self.dossier_ecriture, self.dossier_lecture):
                if d and os.path.exists(os.path.join(d, nom)):
                    self.real_files[p] = os.path.join(d, nom); break
        return super().h_fopen(path, mode, *a)

    def h_fclose(self, h, *a):
        f = self.files.get(h)
        if f and f.get('persist'):
            os.makedirs(os.path.dirname(f['persist']), exist_ok=True)
            with open(f['persist'], 'wb') as fh: fh.write(bytes(f['d']))
        return super().h_fclose(h, *a)


class Etat:
    """Ce que Unity saurait a l'ecran : decode des paquets du moteur."""
    def __init__(self):
        self.carte = None; self.cam_pos = None; self.cam_cible = None; self.cam_rot = None
        self.symboles = {}                            # carte du monde : icones (paquet 29) idx -> {genre, x, y (coin haut-gauche en px), alpha, visible}
        self.modeles = {}; self.maillages = {}       # combat : emplacement -> id de modele (texture m<id>) ; emplacement -> maillage dynamique (paquet 22)
        self.mesh_etat = {}                           # combat : emplacement -> {'shader', 'vis', 'col', 'off'} (paquets 21, 23, 24, 25, 26 : MeshChara cote Unity)
        self.cam_fov = None                           # paquet 11 (camera_sub) : champ de vision nominal en degres (Unity le multiplie par 4/3, SceneCamera.Update) ; None = valeur du combat (48)
        self.icones = {}                              # paquets 19/20 (Riseup) : emplacement (0..15) -> {'id' (case 32 x 32 de la feuille MenuTexture), 'pos', 'alpha'} : bulle « ! » au-dessus d'un personnage
        self.sprites = {}; self.cartes_chargees = 0; self.evt_anim = []; self.fond = (0.0, 0.0, 0.0)
        self.partie = 0; self.champ = None        # partie du jeu (0xc ville, 0xe carte du monde) ; carte du monde : tuiles a afficher
        self.maillages_vus = set()                # combat : emplacements dont le maillage (paquet 22) est arrive a CETTE image : Unity dessine en mode immediat, pas de paquet = rien a l'ecran (clignotement d'un coup recu, monstre vaincu)
        self.fondu = {'a': 0.0, 'fin': 0.0, 'pas': 0.0, 'n': 0, 'col': (0.0, 0.0, 0.0), 'noir': 0}      # Fade.FadePlane : opacite courante, opacite visee, pas par image, images restantes, couleur, images passees tout noir
        self.sons = []; self.musique = None       # paquets 35/36/37 : ordres de son en attente (type, valeur), vides par la fenetre a chaque image ; musique demandee en dernier (None = arret)
        self.monde = None                         # carte du monde entiere lue dans le moteur : {'ids': tuiles (H x L) apres animation, 'l', 'h'} ; None hors de la carte du monde

    def appliquer(self, paquets, lire_nom):
        self.maillages_vus = set()
        for t, a, f in paquets:
            if t == 0:
                self.carte = lire_nom(); self.cartes_chargees += 1; self.sprites.clear(); self.fond = (0.0, 0.0, 0.0); self.modeles.clear(); self.maillages.clear(); self.mesh_etat.clear(); self.cam_rot = None; self.cam_fov = None; self.symboles.clear(); self.icones.clear()
            elif t == 8 and len(f) >= 6:
                self.fond = tuple((f[i] + f[i + 3]) / 2 for i in range(3))     # couleur de fond (ciel) : deux teintes, on prend la moyenne
            elif t == 28 and len(a) >= 4:           # FieldData::drawBlock : fenetre de tuiles 16x16 de la carte du monde (cols x lignes) autour de la camera
                self.champ = {'x0': a[0], 'y0': a[1], 'cols': a[2], 'tuiles': a[4:4 + a[3]], 'cam': (f[0], f[1]) if len(f) >= 2 else None}
            elif t == 29 and a:                     # FieldData::draw_symbol : idx | genre << 8 + [x, y, alpha] ; 0x8000 | idx = cacher ; 0xc000 | idx = remontrer
                v = a[0]; i = v & 0xff
                if v & 0xc000 == 0xc000:
                    if i in self.symboles: self.symboles[i]['vis'] = True
                elif v & 0x8000:
                    if i in self.symboles: self.symboles[i]['vis'] = False
                elif len(f) >= 3: self.symboles[i] = {'genre': (v >> 8) & 0x7f, 'x': f[0], 'y': f[1], 'alpha': f[2], 'vis': True}
            elif t == 12 and len(a) >= 2:
                if a[1] >= 0: self.sprites[a[0]] = {'id': a[1], 'pos': None, 'col': (1, 1, 1, 1), 'pose': 0, 'dir': 0, 'vis': True}
                else: self.sprites.pop(a[0], None)
            elif t == 14 and len(a) >= 2:           # cmd_chara_on_off : Chara.SetVisible(valeur != 0) = Renderer.enabled du personnage (et de son ombre)
                if a[0] in self.sprites: self.sprites[a[0]]['vis'] = a[1] != 0
            elif t == 19 and len(a) >= 2 and 0 <= a[0] < 16:        # cmd_rise_icon_load : emplacement, numero d'icone (< 0 = liberation) ; objet Riseup, materiau Custom/Riseup sur la feuille MenuTexture
                if a[1] >= 0: self.icones[a[0]] = {'id': a[1], 'pos': None, 'alpha': 1.0}
                else: self.icones.pop(a[0], None)
            elif t == 20 and a and len(f) >= 4:     # cmd_rise_icon : emplacement ; x, y, z (SetPosition) puis alpha (Material.color = 1, 1, 1, alpha)
                ic = self.icones.get(a[0])
                if ic is not None: ic['pos'] = tuple(f[:3]); ic['alpha'] = f[3]
            elif t == 9 and a:                      # cmd_fade : duree en images = v & 0x7ff ; bit 12 : sortie vers une couleur (sinon entree) ; bit 11 : couleur r, g, b en flottants ; bit 13 : blanc (sinon noir) ; bit 14 : devant l'interface
                self._fondu(a[0], f)
            elif t == 5 and a:
                self.evt_anim.append((a[0] & 0xffff, a[0] >> 16))
            elif t == 10 and a:
                fl = list(f)
                if a[0] & 1 and len(fl) >= 3: self.cam_pos = tuple(fl[:3]); fl = fl[3:]
                if a[0] & 2 and len(fl) >= 3: self.cam_cible = tuple(fl[:3]); fl = fl[3:]
                if a[0] & 4 and len(fl) >= 3: self.cam_rot = tuple(fl[:3])        # combat : rotation (tangage, lacet, roulis) en degres
            elif t == 21 and len(a) >= 2:           # DSSAObject::send_unity_load(emplacement, id de modele) ; id < 0 = liberation
                if a[1] >= 0:
                    self.modeles[a[0]] = a[1]
                    self.mesh_etat[a[0]] = {'shader': 3 if a[1] > 999 else 1, 'vis': True, 'col': (1.0, 1.0, 1.0), 'off': (0.0, 0.0)}      # MeshChara..ctor : shader 1 (DssaMonster0) sauf id > 999 (DssaCharaTransparent) ; couleur blanche
                else: self.modeles.pop(a[0], None); self.maillages.pop(a[0], None); self.mesh_etat.pop(a[0], None)
            elif t == 23 and len(a) >= 2:           # cmd_mesh_on_off : emplacement, visible
                if a[0] in self.mesh_etat: self.mesh_etat[a[0]]['vis'] = bool(a[1])
            elif t == 24 and a:                     # cmd_mesh_shader : emplacement | numero de shader << 8 (0 Chara, 1 DssaMonster0, 2 DssaMonster1, 3 DssaCharaTransparent)
                if (a[0] & 0xff) in self.mesh_etat: self.mesh_etat[a[0] & 0xff]['shader'] = a[0] >> 8
            elif t == 25 and a and len(f) >= 3:     # cmd_mesh_color : emplacement ; r, g, b (Material.color, alpha 1)
                if a[0] in self.mesh_etat: self.mesh_etat[a[0]]['col'] = tuple(f[:3])
            elif t == 26 and a and len(f) >= 2:     # cmd_mesh_tex_offset : emplacement ; u, v (Material.SetTextureOffset)
                if a[0] in self.mesh_etat: self.mesh_etat[a[0]]['off'] = tuple(f[:2])
            elif t == 11 and a and a[0] & 4:        # cmd_camera_sub : bit 0 near, bit 1 far, bit 2 fov (flottants dans l'ordre des bits)
                k = (a[0] & 1) + ((a[0] >> 1) & 1)
                if len(f) > k: self.cam_fov = min(max(f[k], 0.0), 78.0)      # SceneCamera.SetFOV2 : borne a 78
            elif t == 22 and len(a) >= 2:           # maillage dynamique : [emplacement, nb sommets, couleurs (1 octet/sommet, 4 par entier), indices...] + flottants [x, y, z, (u, v, x, y, z) * nb sommets]
                nv = a[1]; nc = (nv + 3) // 4; idx = a[2 + nc:]
                if len(f) >= 3 + 5 * nv and idx:
                    v = [f[3 + 5 * i:8 + 5 * i] for i in range(nv)]
                    self.maillages[a[0]] = {'pos': tuple(f[:3]), 'v': v, 'idx': list(idx), 'col': list(a[2:2 + nc])}       # col : un octet par quad (4 sommets), 255 = opaque
                    self.maillages_vus.add(a[0])
            elif t in (35, 36, 37) and a:           # cmd_se_play / cmd_se_stop / cmd_bgm (Sound.PlaySE, StopSE, PlayBGM ; valeur >= 4096 = StopBGM)
                self.sons.append((t, a[0]))
                if t == 37: self.musique = a[0] if 0 <= a[0] < 4096 else None
            elif t == 13 and a:
                fl = list(f); flags = a[0]; s = self.sprites.get(flags & 0x7ff)
                if s is None: continue
                if flags & 0x800: fl = fl[1:]
                if flags & 0x1000: s['col'] = tuple(fl[:4]); fl = fl[4:]
                if flags & 0x2000 or flags & 0x8000: s['pos'] = tuple(fl[:3]); fl = fl[3:]      # 0x8000 : meme position, version carte du monde (pixels)
                if flags & 0x4000 and len(a) > 1: s['pose'] = a[1] & 0xff; s['dir'] = (a[1] >> 8) & 0xff

    def _fondu(self, v, f):
        """Fade.SetFadeIn / SetFadeOutBlack / SetFadeOutWhite / SetFadeOutColor (FadePlane.Command) : sortie = vers l'opacite 1 depuis l'opacite courante ;
        entree = le plan repart de l'opacite 1 vers 0. Duree <= 1 image : immediat."""
        d = v & 0x7ff; fd = self.fondu; fd['noir'] = 0
        if v & 0x1000:
            fd['col'] = tuple(f[:3]) if v & 0x800 and len(f) >= 3 else ((1.0, 1.0, 1.0) if v & 0x2000 else (0.0, 0.0, 0.0))
            fd['fin'] = 1.0
            if d <= 1: fd['a'] = 1.0; fd['n'] = 0; fd['pas'] = 0.0
            else: fd['pas'] = (1.0 - fd['a']) / d; fd['n'] = d
        else:
            fd['fin'] = 0.0
            if d <= 1: fd['a'] = 0.0; fd['n'] = 0; fd['pas'] = 0.0
            else: fd['a'] = 1.0; fd['pas'] = -1.0 / d; fd['n'] = d

    def tick_fondu(self):
        """FadePlane.Update : une fois par image du jeu. Garde-fou : un ecran reste tout noir plus de 6 s (fondu d'entree lance par Unity lui-meme, pas par le moteur) est rouvert."""
        fd = self.fondu
        if fd['n'] >= 1:
            fd['n'] -= 1
            if fd['n'] < 1: fd['a'] = fd['fin']
            else: fd['a'] += fd['pas']
        if fd['a'] >= 0.999 and fd['n'] < 1:
            fd['noir'] += 1
            if fd['noir'] > 180: fd['fin'] = 0.0; fd['pas'] = -1.0 / 15; fd['n'] = 15; fd['noir'] = 0
        else: fd['noir'] = 0


DBG = bool(os.environ.get('DQ_UIDBG'))


class Interface:
    """Ce que l'interface Unity memorise des messages du moteur : une fenetre par port (liste de textes, curseur, ouverte ou non).
    Messages d'un port (apres le premier octet = nombre d'arguments) : [1,..] ouvre ; [7,0] ferme ; [11,ligne,colonne] annonce
    que le message suivant du port est le texte de cette case ; [4,i] curseur sur la ligne i ; [12,ligne,x] et [10,..] reglages."""
    def __init__(self):
        self.f = {}; self.attente = {}; self.ordre = 0
        self.msg = {'type': 0, 'y': 0, 'feed': False, 'shake': 0}      # fenetre de message (port 1) : type de fenetre, defilement du canevas, curseur de fin de page
        self.obs = None            # observateur facultatif (visionneuse) : ouvrir(port, type), fermer(port), param_int/param_str/code/selection

    def fen(self, port):
        if port not in self.f: self.f[port] = {'cases': {}, 'vis': False, 'cur': 0, 'ordre': 0, 'indic': {}, 'type': None, 'act': False, 'req': False, 'draw': False, 'force': False, 'ign': False, 'sv': True}
        return self.f[port]

    def appliquer(self, liste):
        if DBG:
            for port, a in liste:
                if port in (0, 1) or port >= 12: print('MSG image %s port %d %s' % (getattr(self, 'image', '?'), port, a[:10]), flush=True)
        for port, a in liste:
            if port == 1:                                       # fenetre de message : dessinee d'apres le canevas du moteur ; MenuMessage.NoticeReceive : 0 type, 1 curseur, 2 secousse, 3 defilement
                if len(a) >= 2:
                    c, v = a[0], a[1]
                    if c == 0: self.msg['type'] = v
                    elif c == 1: self.msg['feed'] = (v & 0xffff) == 1
                    elif c == 3: self.msg['y'] = v
                continue
            if port == 0:
                if len(a) >= 2 and 2 <= a[0] < 32:
                    if a[1] == 3: self.fen(a[0])['sv'] = bool(a[2]) if len(a) > 2 else True       # [fenetre, 3, visible] : MenuPort.SetVisible
                    elif a[1] == 2:                                                                # [fenetre, 2] : fermee
                        f = self.fen(a[0]); f['act'] = f['vis'] = False
                        if self.obs: self.obs.fermer(a[0])
                    elif a[1] == 1 and len(a) >= 4:                                                # [fenetre, 1, categorie, type] : cree un menu du type donne (MenuPortItemTable.Make_*)
                        f = self.fen(a[0]); f['type'] = a[3]; f['cases'] = {}; f['indic'] = {}; f['cur'] = 0
                        f['act'] = f['req'] = f['draw'] = f['force'] = f['ign'] = False; f['sv'] = True; f['vis'] = False; f['att'] = None; f['plaq'] = {}
                        if self.obs: self.obs.ouvrir(a[0], a[3])
                continue
            f = self.fen(port)
            if port in self.attente:                            # texte de la case annoncee
                cle = self.attente.pop(port); txt = ''.join(chr(v & 0xffff) for v in a if v); f['cases'][cle] = txt
                if self.obs: self.obs.param_str(port, cle[0], cle[1], txt)
                continue
            if f['type'] == 31 and f.get('att'):               # plaque de monstre : messages qui suivent un code > 0 (nom, puis [x, y, ...])
                i, etape = f['att']
                if etape == 'nom': f['plaq'][i]['nom'] = ''.join(chr(v & 0xffff) for v in a if v); f['att'] = (i, 'prm')
                else:
                    f['plaq'][i]['prm'] = list(a); f['att'] = None
                    if self.obs and hasattr(self.obs, 'plaque'): self.obs.plaque(port, i, f['plaq'][i]['nom'], f['plaq'][i]['prm'], f['plaq'][i]['code'])
                continue
            if not a: continue
            cmd = a[0]
            if cmd != 7 and not f['act']: f['act'] = True; f['force'] = True       # MenuPortItem.NoticeReceive : premiere commande -> parties activees, dessin force
            if cmd == 11 and len(a) >= 3: self.attente[port] = (a[1], a[2])
            elif cmd == 1:
                if port >= 12: f['cases'] = {}; f['indic'] = {}; f['cur'] = 0           # fenetre dynamique : contenu neuf
                self.ordre += 1; f['ordre'] = self.ordre
            elif cmd == 2 and len(a) >= 3: f['grille'] = tuple(a[1:4])                 # setItemMenu : colonnes, lignes, nombre de cases du curseur du moteur
            elif cmd == 6: f['inp'] = True                                              # demande de saisie (a chaque image tant que le menu attend une touche)
            elif cmd == 5: f['req'] = True                                             # demande de dessin : le moteur la renvoie a chaque image tant que la fenetre doit etre vue
            elif cmd == 7: f['act'] = False                                            # arret : parties desactivees
            elif cmd == 8 and len(a) >= 2: f['ign'] = (a[1] & 0xffff) != 0            # ignorer les demandes de dessin (toujours visible)
            elif cmd == 4 and len(a) >= 2:
                f['cur'] = a[1]
                if self.obs: self.obs.selection(port, a[1])
            elif cmd == 10 and len(a) >= 5:                    # setItemParamInt [id, no, bas, haut] : valeur 32 bits
                v = (a[3] & 0xffff) | ((a[4] & 0xffff) << 16)
                if self.obs: self.obs.param_int(port, a[1], a[2], v - (1 << 32) if v & 0x80000000 else v)
            elif cmd == 12 and len(a) >= 3:
                f['indic'][a[1]] = a[2]
                if f['type'] == 31 and a[1] < 4 and a[2] >= 1:                                  # MonsterNamePlate.ReceiveParameters(code >= 1)
                    f.setdefault('plaq', {})[a[1]] = {'code': a[2]}; f['att'] = (a[1], 'nom')
                elif f['type'] == 31 and a[1] == 5:                                             # disposition des plaques (nombre de plaques)
                    if self.obs and hasattr(self.obs, 'disposer'): self.obs.disposer(port, a[2])
                elif self.obs: self.obs.code(port, a[1], a[2])

        for f in self.f.values():                              # MenuPortItem.Update : visible tant que le moteur demande le dessin a chaque image
            if not f['ign']:
                if f['force']: f['force'] = False; f['draw'] = f['req']
                elif f['draw'] != f['req']: f['draw'] = f['req']
            f['req'] = False; f['entree'] = f.get('inp', False); f['inp'] = False
            f['vis'] = f['act'] and f['sv'] and (f['draw'] or f['ign'])

    def visibles(self):
        return sorted(((p, f) for p, f in self.f.items() if f['vis'] and f['cases']), key=lambda x: x[1]['ordre'])


class Moteur:
    def __init__(self, dossier_apk, dossier_lecture, dossier_ecriture, langue=1):
        self.log = Silencieux()
        lib = zipfile.ZipFile(os.path.join(dossier_apk, 'split_config.arm64_v8a.apk')).read(LIB_APK)
        pack = os.path.join(dossier_apk, 'split_UnityDataAssetPack.apk')
        self.apk_donnees = pack
        CoreJeu.dossier_lecture, CoreJeu.dossier_ecriture = dossier_lecture, dossier_ecriture
        c = self.core = CoreJeu(lib, T.ApkFs([pack]), self.log, real_files={'/dq/assets.apk': pack})
        self.E = c.export; self.L = c.LIB_BASE
        if not c.run_init(): raise RuntimeError('constructeurs C++ en echec')
        c.call(self.E('DevInit'), c.cstr('/dq/assets.apk'), c.cstr('/dq/'), langue)
        self.buf1, self.buf2 = c.malloc(0x2000), c.malloc(0x2000)          # files de messages interface <-> moteur (allouees par l'appelant)
        c.call(self.E('MenuPortInit'), 32, self.buf1, 129, self.buf2, 1089)
        NB = 0x40000
        c.inp, c.outp, c.outf, c.strb = c.malloc(NB), c.malloc(NB), c.malloc(NB), c.malloc(0x10000)
        c.call(self.E('GameInit'), c.inp, c.outp, c.outf, c.strb)
        self.pk = PacketLog(c)
        self.etat = Etat()
        self.msgs_ui = []                        # messages moteur -> interface (menus), derniere image
        self.ui = Interface()                    # fenetres de menu memorisees
        self.images = 0
        # animations d'objets de la carte (portes...) : le moteur attend qu'Unity les termine (bit0 de l'octet +0x14 de l'entree)
        self.anims = []                          # [(image d'echeance, index d'objet)]
        self.fldobj = 0
        self.msg_visible = False                 # fenetre de message affichee ? (MenuPort::SetVisible sur le port du message)
        def _sv(uc, a, sz, u):
            port = uc.reg_read(c.XREG[0]) & 0xffffffffffff
            if port and port == struct.unpack('<Q', c.mem(self.L + OBJ_MESSAGE + 0x30, 8))[0]: self.msg_visible = bool(uc.reg_read(c.XREG[1]) & 0xff)
        hv = c.export('_ZN8MenuPort10SetVisibleEb')
        c.uc.hook_add(unicorn.UC_HOOK_CODE, _sv, None, hv, hv)
        self.fielddata = 0; self._grille = None; self._grille_img = -999
        def _fd(uc, a, sz, u): self.fielddata = uc.reg_read(c.XREG[0]) & 0xffffffffffff          # FieldData::drawBlock(this) : l'objet qui porte la carte du monde
        hfd = c.export('_ZN9FieldData9drawBlockEv')
        c.uc.hook_add(unicorn.UC_HOOK_CODE, _fd, None, hfd, hfd)
        def _sca(uc, a, sz, u): self.fldobj = uc.reg_read(c.XREG[0])
        uc_hook = c.export('_ZN9FLDObject18SetCommonAnimationEii')
        c.uc.hook_add(unicorn.UC_HOOK_CODE, _sca, None, uc_hook, uc_hook)
        for _ in range(3): self.step(0, 0)       # le titre tourne quelques images (curseurs initialises)
        self._creer_canevas()

    def _creer_canevas(self):
        c = self.core
        # canevas de texte des messages : Unity le cree par un message de port (largeur, hauteur) ; on appelle directement le gestionnaire
        self.obj_msg = self.L + OBJ_MESSAGE
        shorts = [0]
        for v in (1, 0, CANEVAS_L, CANEVAS_H, 0): shorts += [v & 0xffff, (v >> 16) & 0xffff]
        pm = c.malloc(64); c.mem_w(pm, struct.pack('<%dH' % len(shorts), *shorts))
        c.call(self.E('_ZN9SDrawCTRL11CanvasSetupEPvPKs'), self.obj_msg, pm, label='CanvasSetup')

    def _global(self):
        return struct.unpack('<Q', self.core.mem(self.L + GLOBAL_PTR, 8))[0]

    def demarrer_champ(self):
        """(essai) force le passage a la carte du monde : Global::startField()."""
        self.core.call(self.E('_ZN6Global10startFieldEv'), self._global(), label='startField')

    def _nom_carte(self):
        s = self.core.read_cstr(self.L + NOM_RESSOURCE).decode('latin-1')
        return s.split('/')[-1]

    def step(self, tenu=0, decl=None):
        """Une image du jeu. tenu = touches maintenues (marche) ; decl = touches qui viennent d'etre pressees (boutons, menus).
        Les fondus d ecran sont rejoues dans Etat (tick_fondu). -> liste de paquets"""
        if decl is None: decl = tenu
        c = self.core
        w = struct.unpack('<I', c.mem(c.outp, 4))[0]
        if w & 1: c.mem_w(c.outp, struct.pack('<I', w & ~1))
        self._finir_anims()
        self.pk.begin()
        r = c.call(self.E('GameUpdate'), tenu, decl, label='GameUpdate')
        if r is None: raise RuntimeError('le moteur s est arrete :\n' + '\n'.join(self.log.lignes[-12:]))
        pq = self.pk.packets()
        if os.environ.get('DQ_PKT') and self.etat.partie == PARTIE_COMBAT:       # (test) journal des paquets de combat : image, [(type, ints...)]
            with open(os.environ['DQ_PKT'], 'a') as fj:
                fj.write('%d %s\n' % (self.images, [(t, a[:2]) if t != 22 else (22, a[0], a[1], [round(x, 2) for x in f[:3]], [round(min(f[3 + 5 * i + 2] for i in range(a[1])), 2), round(max(f[3 + 5 * i + 2] for i in range(a[1])), 2), round(min(f[3 + 5 * i + 3] for i in range(a[1])), 2), round(max(f[3 + 5 * i + 3] for i in range(a[1])), 2)]) for t, a, f in pq if t not in (12, 13, 14)]))
        if os.environ.get('DQ_PKTALL'):       # (test temporaire) tous les paquets sauf 22/28/29
            with open(os.environ['DQ_PKTALL'], 'a') as fj:
                for t, a, f in pq:
                    if t not in (22, 28, 29): fj.write('%d p=%#x %d %s %s\n' % (self.images, self.etat.partie, t, a[:6], [round(x, 3) for x in f[:6]]))
        self.ui.image = self.images
        self._consommer_messages()
        for p in pq:
            if p[0] == 5 and p[1]: self.anims.append((self.images + DUREE_ANIM, p[1][0] & 0xffff))
        self.etat.appliquer(pq, self._nom_carte)
        self.etat.tick_fondu()
        self.etat.partie = struct.unpack('<H', c.mem(self._global() + 0xee, 2))[0]
        if self.etat.partie != PARTIE_CHAMP: self.etat.champ = None; self.etat.monde = None
        else: self._lire_monde()
        self.images += 1
        return pq

    def _lire_monde(self):
        """Carte du monde entiere (FieldData : grille CWorldMap +0x188 de 256 x 256 numeros de bloc, table des blocs +0x180 de 6 octets,
        decalages d'animation de l'eau aux adresses 0x275830/34/38). Meme regle que FieldData::drawBlock (verifiee : 1300 tuiles sur 1300).
        Le moteur n'envoie que 25 colonnes autour du heros ; ici on peut dessiner autour de lui autant qu'on veut."""
        c, fd = self.core, self.fielddata
        if not fd: return
        try:
            if self._grille is None or self.images - self._grille_img >= 30:
                gp, = struct.unpack('<Q', c.mem(fd + 0x188, 8)); l, h = struct.unpack('<ii', c.mem(fd + 0x198, 8))
                if not gp or not (0 < l <= 1024 and 0 < h <= 1024): return
                self._grille = np.frombuffer(c.mem(gp, l * h * 2), '<u2').reshape(h, l).astype(np.int32); self._grille_img = self.images
            tbl, = struct.unpack('<Q', c.mem(fd + 0x180, 8))
            ch = np.frombuffer(c.mem(tbl, 1024 * 6), np.uint8).reshape(1024, 6)
            b0, b1, b2 = (struct.unpack('<h', c.mem(self.L + a, 2))[0] for a in (0x275830, 0x275834, 0x275838))
            t = ch[:, 1]; s2 = np.ascontiguousarray(ch[:, 2:4]).view('<i2').ravel().astype(np.int32); s4 = np.ascontiguousarray(ch[:, 4:6]).view('<i2').ravel().astype(np.int32)
            tuile = np.where(t == 0, s4 + b0, np.where(t == 0x18, s2 + b2, np.where(t == 8, s2 + b1, s2)))
            h, l = self._grille.shape
            self.etat.monde = {'ids': tuile[(self._grille - 1) & 0x3ff], 'l': l, 'h': h}
        except Exception:
            self.etat.monde = None

    def lire_canevas(self):
        """Bitmap du texte du message (hauteur x largeur, 0-255 = opacite), ou None si le canevas n'existe pas encore."""
        c = self.core
        buf = struct.unpack('<Q', c.mem(self.obj_msg + 0x10, 8))[0]
        if not buf: return None
        import numpy as np
        return np.frombuffer(c.mem(buf, CANEVAS_L * CANEVAS_H), np.uint8).reshape(CANEVAS_H, CANEVAS_L)

    def _consommer_messages(self):
        """Fait ce que ferait l'interface Unity : lit les messages que le moteur a poses pour chaque port (menus, fenetres...)
        puis vide la file. Sans cela la file grossit sans fin et finit par ecraser la memoire voisine."""
        c = self.core
        b2 = self.buf2
        a = struct.unpack('<%dh' % PILE_SHORTS, c.mem(b2, 2 * PILE_SHORTS))
        out = []
        for port in range(32):
            idx = a[port] & 0xffff; garde = 0
            while 0 < idx < PILE_SHORTS - 2 and garde < 600:
                n = a[idx]
                if n < 0 or idx + n + 1 >= PILE_SHORTS: break
                out.append((port, a[idx + 1: idx + 1 + n]))
                idx = a[idx + 1 + n] & 0xffff; garde += 1
        self.msgs_ui = out
        self.ui.appliquer(out)
        c.mem_w(b2, b'\0' * 128 + struct.pack('<h', 65))              # tetes/queues a 0, pointeur libre = 65

    def menu_actif(self):
        """Port de la fenetre de menu qui attend les touches (active et acceptant la saisie), ou None."""
        c = self.core; L = self.L
        u = lambda a, n, sg=False: int.from_bytes(c.mem(a, n), 'little', signed=sg)
        base = u(L + 0x252098, 8); cnt = u(L + 0x252094, 2, True)
        if not base: return None
        cand = []
        for i in range(1, cnt):
            e = base + 0x20 * i
            port = u(e + 0x28, 2, True); this = u(e + 0x30, 8)
            if this and u(e + 0x38, 8) and port > 1 and u(this + 8, 1) == 1 and u(this + 0x50, 2):
                f = self.ui.f.get(port)
                if f and f['vis'] and f['cases']: cand.append((f['ordre'], port))
        if not cand:                                 # sinon : menu dont le moteur demande la saisie (commande 6 a chaque image), meme sous un message : Oui / Non d'une question
            cand = [(self.ui.f[p]['ordre'], p) for p in self.menus_saisie()]
        return max(cand)[1] if cand else None

    def menus_saisie(self):
        """Tous les ports de menu dont le moteur demande la saisie (le toucher agit sur chacun d'eux) : le plus recent d'abord."""
        return sorted((p for p, f in self.ui.f.items() if p > 1 and f.get('entree') and f['vis'] and f['cases']), key=lambda p: -self.ui.f[p]['ordre'])

    def envoyer_menu(self, port, *args):
        """Ce que fait Unity : depose un message [n, args...] dans la file moteur<-interface du port (touches/pointeur des menus)."""
        c = self.core
        a = list(struct.unpack('<%dh' % PILE_SHORTS, c.mem(self.buf1, 2 * PILE_SHORTS)))
        n = len(args); idx = a[64] or 65
        a[idx] = n
        for i, v in enumerate(args): a[idx + 1 + i] = v
        a[idx + n + 1] = 0
        if a[port]: a[a[32 + port]] = idx
        else: a[port] = idx
        a[32 + port] = idx + n + 1
        a[64] = idx + n + 2
        c.mem_w(self.buf1, struct.pack('<%dh' % PILE_SHORTS, *a))

    def lire_message(self):
        """Partie du canevas actuellement visible dans la fenetre de message : n lignes a partir du defilement courant.
        -> tableau (hauteur x largeur) de 0-255, ou None."""
        a = self.lire_canevas()
        if a is None: return None
        c = self.core
        i16 = lambda ad: struct.unpack('<h', c.mem(ad, 2))[0]
        lignes = i16(self.L + 0x2523a0) + 1            # nombre de lignes visibles de la fenetre (reglage du moteur)
        lh = i16(self.obj_msg + 0x42)                  # hauteur d'une ligne (pixels du canevas)
        defil = i16(self.obj_msg + 0x4a)               # defilement courant (le moteur l'anime de 8 pixels par image)
        if not (1 <= lignes <= 8 and 8 <= lh <= 64): lignes, lh, defil = 4, 28, -3
        h = lignes * lh; top = max(0, defil + 3)
        sub = a[top:top + h]
        if len(sub) < h:
            import numpy as np
            sub = np.vstack([sub, np.zeros((h - len(sub), a.shape[1]), np.uint8)])
        return sub

    def _finir_anims(self):
        c = self.core
        due = [a for a in self.anims if a[0] <= self.images]
        if not due: return
        self.anims = [a for a in self.anims if a[0] > self.images]
        table = struct.unpack('<Q', c.mem(self.fldobj + 8, 8))[0]
        for _, idx in due:
            ad = table + idx * 32 + 0x14
            c.mem_w(ad, bytes([c.mem(ad, 1)[0] & ~1]))

    def charger_sauvegarde(self, numero):
        """numero 1..5 -> lit data<numero>.dat ; le moteur relance la ville sauvegardee"""
        r = self.core.call(self.E('_ZN7profile8SaveLoad8loadbankEi'), numero - 1, label='loadbank')
        return bool(r)
