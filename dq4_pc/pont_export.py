"""Export des donnees graphiques du jeu vers des fichiers simples que lit le projet Unity (piste 3, issue #57).
Rien n'est fourni avec ce programme : tout vient des APK du joueur et reste dans son dossier `cache/` (ignore par git).

Dossier d'export (par defaut dq4_pc/cache/unity) :
  carte_<nom>/geo.bin        geometrie de la carte (format ci-dessous) + tex_<i>.png (textures de la carte)
  carte_btl_*/fond.json      (combats) couleur de fond estimee : [r, g, b]
  sprites/h<id>.png          feuille de sprites d'un personnage (cases de 24x32, 3 colonnes)
  monstres/m<id>.png         texture d'un monstre (combat)

geo.bin (little-endian) :
  'DQ4M', int32 version, float32 bx0, float32 bx1 (bornes en x du decor : cadrage 16/9),
  int32 nb_textures, int32 nb_parties, puis pour chaque partie :
    int32 objet (-1 = decor fixe ; >= 0 = indice FMAP d'un objet animable, ex. une porte),
    int32 longueur_nom + nom UTF-8, int32 indice_texture, int32 nb_sommets,
    float32[n*3] positions (espace Unity), float32[n*2] UV, uint8[n*4] couleurs RGBA.
  Les sommets sont deplies : chaque groupe de 3 sommets est un triangle.
"""
import os, json, struct
import numpy as np
from PIL import Image

VERSION_EXPORT = 1
SPRITE_L, SPRITE_H = 24, 32


def fond_combat(c):
    """Le moteur n'envoie pas de couleur de fond en combat et le decor laisse un vide entre le bord du sol et le ciel :
    couleur moyenne (texture x couleur de sommet) du sol a la peripherie de l'arene, ciel exclu (meme calcul que la visionneuse). -> (r, g, b) ou None."""
    try:
        pts = []
        for iobj, nm in c.get('noms', {}).items():
            if 'sora' in nm.lower(): continue
            for key, deb, n in c['plages'].get(iobj, []):
                pos, uv, col = c['groupes'][key]; tex = c['textures'].get(key)
                if tex is None: continue
                p, u, k = pos[deb:deb + n], uv[deb:deb + n], col[deb:deb + n]
                h, w = tex.shape[:2]
                px = tex[np.clip(((1 - u[:, 1]) * (h - 1)).astype(int) % h, 0, h - 1), np.clip((u[:, 0] * (w - 1)).astype(int) % w, 0, w - 1)]
                rgb = px[:, :3] / 255.0 * k[:, :3]
                ok = px[:, 3] > 128
                pts.append(np.c_[np.hypot(p[:, 0], p[:, 2] - 3.0), p[:, 1], rgb][ok])
        a = np.concatenate(pts)
        sol = a[(a[:, 1] < 2.0) & (a[:, 0] >= np.percentile(a[:, 0], 60))]
        if len(sol) < 20: sol = a[a[:, 0] >= np.percentile(a[:, 0], 85)]
        return tuple(float(x) for x in sol[:, 2:].mean(0))
    except Exception:
        return None


def _png(chemin, arr):
    os.makedirs(os.path.dirname(chemin), exist_ok=True)
    Image.fromarray(np.ascontiguousarray(arr, dtype=np.uint8), 'RGBA').save(chemin)


class Exporteur:
    def __init__(self, donnees, dossier, log=print):
        self.d, self.dossier, self.log = donnees, os.path.abspath(dossier), log
        os.makedirs(self.dossier, exist_ok=True)
        self.nl = {}                  # id de feuille -> nombre de rangees (4 ou 8), 0 si introuvable
        self.mons = {}                # id de monstre -> True si sa texture est exportee

    def dossier_carte(self, nom):
        return os.path.join(self.dossier, 'carte_' + nom)

    @staticmethod
    def _a_jour(geo):
        try:
            with open(geo, 'rb') as f:
                tete = f.read(8)
            return tete[:4] == b'DQ4M' and struct.unpack('<i', tete[4:8])[0] == VERSION_EXPORT
        except OSError:
            return False

    def est_exportee(self, nom):
        dc = self.dossier_carte(nom)
        if nom.startswith('btl_') and not os.path.exists(os.path.join(dc, 'fond.json')): return False       # decor de combat : le fond est exporte avec lui
        return self._a_jour(os.path.join(dc, 'geo.bin'))

    def fond(self, nom):
        """Couleur de fond estimee d'un decor de combat deja exporte -> (r, g, b) ou None."""
        try:
            with open(os.path.join(self.dossier_carte(nom), 'fond.json')) as f: v = json.load(f)
            return tuple(float(x) for x in v[:3]) if v else None
        except (OSError, ValueError): return None

    def monstre(self, mid):
        """Exporte la texture m<mid> d'un monstre -> True si elle est disponible."""
        if mid in self.mons: return self.mons[mid]
        chemin = os.path.join(self.dossier, 'monstres', 'm%d.png' % mid)
        ok = os.path.exists(chemin)
        if not ok:
            try:
                arr = self.d.texture('m%03d' % mid)
                if arr is not None: _png(chemin, arr); ok = True
                else: self.log('texture de monstre introuvable : m%03d' % mid)
            except Exception as ex:
                self.log('texture de monstre illisible (m%03d) : %s' % (mid, ex))
        self.mons[mid] = ok
        return ok

    def carte(self, nom, forcer=False):
        """Exporte la carte `nom` si besoin -> dossier de la carte. Leve KeyError si la carte est introuvable."""
        dc = self.dossier_carte(nom); geo = os.path.join(dc, 'geo.bin')
        if not forcer and self.est_exportee(nom): return dc
        c = self.d.carte(nom)
        groupes, textures = c['groupes'], c['textures']
        plages, noms = c.get('plages', {}), c.get('noms', {})
        cles = [k for k in groupes if k in textures]
        index = {k: i for i, k in enumerate(cles)}
        portes = sorted(i for i, n in noms.items() if 'door' in n.lower())          # objets animables : meme critere que la visionneuse
        parties = []
        for k in cles:                                                               # decor fixe : un maillage par texture, sans les portes
            pos, uv, col = groupes[k]
            garde = np.ones(len(pos), bool)
            for i in portes:
                for kk, deb, n in plages.get(i, []):
                    if kk == k: garde[deb:deb + n] = False
            if garde.any(): parties.append((-1, '', index[k], pos[garde], uv[garde], col[garde]))
        for i in portes:                                                             # chaque porte : ses propres maillages
            for kk, deb, n in plages.get(i, []):
                if kk in index:
                    pos, uv, col = groupes[kk]
                    parties.append((i, noms[i], index[kk], pos[deb:deb + n], uv[deb:deb + n], col[deb:deb + n]))
        tous = np.concatenate([groupes[k][0] for k in cles]) if cles else np.zeros((1, 3), np.float32)
        bx = (float(np.percentile(tous[:, 0], 0.5)), float(np.percentile(tous[:, 0], 99.5)))
        for k in cles: _png(os.path.join(dc, 'tex_%d.png' % index[k]), textures[k])
        os.makedirs(dc, exist_ok=True)
        if nom.startswith('btl_'):
            with open(os.path.join(dc, 'fond.json'), 'w') as f: json.dump(fond_combat(c), f)
        tmp = geo + '.tmp'
        with open(tmp, 'wb') as f:
            f.write(b'DQ4M' + struct.pack('<iffii', VERSION_EXPORT, bx[0], bx[1], len(cles), len(parties)))
            for obj, nom_p, tex, pos, uv, col in parties:
                nb = nom_p.encode('utf-8')
                f.write(struct.pack('<ii', obj, len(nb)) + nb + struct.pack('<ii', tex, len(pos)))
                f.write(np.ascontiguousarray(pos, '<f4').tobytes())
                f.write(np.ascontiguousarray(uv, '<f4').tobytes())
                f.write(np.clip(col * 255.0 + 0.5, 0, 255).astype(np.uint8).tobytes())
        os.replace(tmp, geo)
        self.log('carte %s exportee : %d textures, %d parties, %d portes' % (nom, len(cles), len(parties), len(portes)))
        return dc

    def feuille(self, ident):
        """Exporte la feuille de sprites `h<ident>` -> nombre de rangees (8 si la 5e rangee existe, sinon 4), 0 si introuvable."""
        if ident in self.nl: return self.nl[ident]
        arr = self.d.feuille(ident)
        if arr is None:
            self.nl[ident] = 0; return 0
        nl = 8 if arr[4 * SPRITE_H:5 * SPRITE_H, :72, 3].any() else 4              # meme regle que la visionneuse
        chemin = os.path.join(self.dossier, 'sprites', 'h%d.png' % ident)
        if not os.path.exists(chemin): _png(chemin, arr)
        self.nl[ident] = nl
        return nl
