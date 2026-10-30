"""Lecture des donnees graphiques du jeu (datapack.unity3d, dans l'APK de donnees de l'utilisateur).
Rien n'est fourni avec ce programme : tout est lu dans les fichiers du joueur puis mis en cache sur son disque."""
import os, re, math, pickle, zipfile, struct
import numpy as np
import fld

DATAPACK = 'assets/bin/Data/datapack.unity3d'


def _quat_mat(x, y, z, w):
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]], dtype=np.float64)


def euler_mat(rx, ry, rz):
    """Quaternion.Euler d'Unity (degres) : Z, puis X, puis Y."""
    a, b, c = math.radians(rx), math.radians(ry), math.radians(rz)
    Rx = np.array([[1, 0, 0], [0, math.cos(a), -math.sin(a)], [0, math.sin(a), math.cos(a)]])
    Ry = np.array([[math.cos(b), 0, math.sin(b)], [0, 1, 0], [-math.sin(b), 0, math.cos(b)]])
    Rz = np.array([[math.cos(c), -math.sin(c), 0], [math.sin(c), math.cos(c), 0], [0, 0, 1]])
    return Ry @ Rx @ Rz


class Donnees:
    def __init__(self, apk_donnees, cache_dir, log=print):
        self.apk, self.cache, self.log = apk_donnees, cache_dir, log
        os.makedirs(cache_dir, exist_ok=True)
        self._env = None
        self._objs = None
        self._mesh = {}
        self._go_by_name = None

    # ---- UnityPy (charge a la demande, seulement si le cache ne suffit pas)
    def env(self):
        if self._env is None:
            import UnityPy
            self.log('Lecture du paquet de donnees du jeu (une seule fois, quelques secondes)...')
            raw = zipfile.ZipFile(self.apk).read(DATAPACK)
            self._env = UnityPy.load(raw)
            self._objs = {o.path_id: o for o in self._env.objects}
        return self._env

    def _gos(self):
        if self._go_by_name is None:
            self.env()
            self._go_by_name = {}
            for pid, o in self._objs.items():
                if o.type.name == 'GameObject':
                    self._go_by_name.setdefault(o.read().m_Name, pid)
        return self._go_by_name

    def _mesh_data(self, ref):
        from UnityPy.helpers.MeshHelper import MeshHandler
        if ref.path_id not in self._mesh:
            m = ref.deref_parse_as_object(); h = MeshHandler(m); h.process()
            v = np.array(h.m_Vertices, np.float32).reshape(-1, 3)
            uv = np.array(h.m_UV0, np.float32).reshape(len(v), -1)[:, :2] if h.m_UV0 is not None else np.zeros((len(v), 2), np.float32)
            col = np.array(h.m_Colors, np.float32).reshape(len(v), -1) if h.m_Colors is not None else np.ones((len(v), 4), np.float32)
            if col.shape[1] == 3: col = np.c_[col, np.full(len(col), 255.0, np.float32)]
            if col.size and col.max() > 1.0: col = col / 255.0
            tris = [np.array(t, np.int64).reshape(-1, 3) for t in h.get_triangles()]
            self._mesh[ref.path_id] = (v, uv, col, tris)
        return self._mesh[ref.path_id]

    def _texture(self, matref, tex_out):
        """-> (cle, scale, offset) ; remplit tex_out[cle] = tableau RGBA (haut en premier)"""
        try:
            m = matref.deref_parse_as_object()
            for k, v in m.m_SavedProperties.m_TexEnvs:
                if v.m_Texture.path_id:
                    key = v.m_Texture.path_id
                    if key not in tex_out:
                        t = v.m_Texture.deref_parse_as_object()
                        tex_out[key] = np.array(t.image.convert('RGBA'))
                    return key, (v.m_Scale.x, v.m_Scale.y), (v.m_Offset.x, v.m_Offset.y)
        except Exception as ex:
            self.log('  texture illisible: %s' % ex)
        return None, (1, 1), (0, 0)

    def _components(self, g):
        out = {}
        for c in g.m_Components:
            o = (c.component if hasattr(c, 'component') else c).deref(); out[o.type.name] = o
        return out

    def _map_text(self, nom):
        self.env()
        for pid, o in self._objs.items():
            if o.type.name == 'TextAsset':
                t = o.read()
                if t.m_Name == nom + '.map':
                    return t.m_Script.encode('utf-8', 'surrogateescape') if isinstance(t.m_Script, str) else bytes(t.m_Script)
        return None

    # ---- une carte
    def carte(self, nom):
        """-> dict : 'groupes' {cle_texture: (positions Nx3, uv Nx2, couleurs Nx4)}, 'textures' {cle: RGBA}, 'objets' (FMAP)"""
        f = os.path.join(self.cache, 'carte2_%s.pkl' % nom)
        if os.path.exists(f):
            with open(f, 'rb') as fh: return pickle.load(fh)
        self.log('Extraction de la carte %s ...' % nom)
        gos = self._gos(); raw = self._map_text(nom)
        root = gos.get(nom + '.fld')
        if root is None or raw is None: raise KeyError('carte introuvable dans les donnees : ' + nom)
        nobj, objs = fld.map_objs(raw)
        kids = {}
        rg = self._objs[root].read(); rt = self._components(rg)['Transform'].read()
        for ch in rt.m_Children:
            cg = self._objs[ch.deref_parse_as_object().m_GameObject.path_id].read()
            m = re.match(r'(\d+)_', cg.m_Name)
            if m: kids[int(m.group(1))] = cg
        tex = {}; acc = {}; lens = {}; plages = {}; noms = {}
        for iobj, o in enumerate(objs):
            cg = kids.get(o['mdl'])
            if cg is None: continue
            noms[iobj] = cg.m_Name
            comps = self._components(cg)
            if 'MeshFilter' not in comps or 'MeshRenderer' not in comps: continue
            t = comps['Transform'].read(); p, q, s = t.m_LocalPosition, t.m_LocalRotation, t.m_LocalScale
            L = np.eye(4); L[:3, :3] = _quat_mat(q.x, q.y, q.z, q.w) @ np.diag([s.x, s.y, s.z]); L[:3, 3] = (p.x, p.y, p.z)
            M = np.eye(4); M[:3, :3] = euler_mat(*o['rot']) @ np.diag(o['scl']); M[:3, 3] = o['pos']
            W = M @ L
            mr = comps['MeshRenderer'].read()
            v, uv, col, tris = self._mesh_data(comps['MeshFilter'].read().m_Mesh)
            vw = (np.c_[v, np.ones(len(v))] @ W.T)[:, :3].astype(np.float32)
            for i, tr in enumerate(tris):
                mat = mr.m_Materials[min(i, len(mr.m_Materials) - 1)]
                key, sc, off = self._texture(mat, tex)
                idx = tr.ravel()
                a = acc.setdefault(key, ([], [], []))
                plages.setdefault(iobj, []).append((key, lens.get(key, 0), len(idx))); lens[key] = lens.get(key, 0) + len(idx)
                a[0].append(vw[idx]); a[1].append(uv[idx] * np.array(sc, np.float32) + np.array(off, np.float32)); a[2].append(col[idx])
        groupes = {k: tuple(np.concatenate(x).astype(np.float32) for x in a) for k, a in acc.items()}
        res = {'groupes': groupes, 'textures': {k: tex[k] for k in groupes if k in tex}, 'objets': objs, 'plages': plages, 'noms': noms}
        with open(f, 'wb') as fh: pickle.dump(res, fh, protocol=4)
        return res

    def texture(self, nom):
        """Texture Unity par son nom (ex. `field` : tuiles de la carte du monde), RGBA haut en bas ; mise en cache."""
        f = os.path.join(self.cache, 'tex_%s.npy' % nom)
        if os.path.exists(f): return np.load(f)
        self.env()
        for pid, o in self._objs.items():
            if o.type.name == 'Texture2D':
                t = o.read()
                if t.m_Name == nom:
                    a = np.array(t.image.convert('RGBA')); np.save(f, a); return a
        return None

    # ---- feuille de sprites d'un personnage
    def feuille(self, ident):
        f = os.path.join(self.cache, 'sprite_h%d.npy' % ident)
        if os.path.exists(f): return np.load(f)
        self.env()
        want = {'h%d' % ident, 'h%03d' % ident}
        for pid, o in self._objs.items():
            if o.type.name == 'Texture2D':
                t = o.read()
                if t.m_Name in want:
                    a = np.array(t.image.convert('RGBA')); np.save(f, a); return a
        return None
