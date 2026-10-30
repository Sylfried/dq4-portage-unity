using System;
using System.Collections.Generic;
using System.IO;
using UnityEngine;

namespace DQ4
{
    /// <summary>Les monstres du combat : un maillage dynamique par emplacement (quadrilateres a plat, tournes vers la camera),
    /// reconstruit quand le moteur en envoie une nouvelle version. Meme regles de dessin que la visionneuse :
    /// fusion alpha, pas d'ecriture de profondeur, pas de masquage des faces, parties dessinees dans l'ordre des indices.</summary>
    public class MonstresDQ4
    {
        class Monstre
        {
            public GameObject Go;
            public MeshRenderer R;
            public Mesh Mesh;
            public Material Mat;
            public int Modele = int.MinValue;
            public int Shader = int.MinValue;
            public Vector3[] Pos;
            public Vector2[] Uv;
            public int[] Idx;
            public byte[] Quads;
            public bool Sale;
            public bool Pret;
            public bool Actif;
        }

        readonly Transform parent;
        readonly Shader shader;
        readonly string dossier;
        readonly Dictionary<int, Texture2D> textures = new Dictionary<int, Texture2D>();
        readonly Dictionary<int, Monstre> slots = new Dictionary<int, Monstre>();
        readonly HashSet<int> actifs = new HashSet<int>();

        public int NbVisibles;

        public MonstresDQ4(Transform parent, Shader shader, string dossierExport)
        {
            this.parent = parent;
            this.shader = shader;
            dossier = Path.Combine(dossierExport, "monstres");
        }

        Texture2D Texture(int modele)
        {
            Texture2D t;
            if (textures.TryGetValue(modele, out t)) return t;
            string chemin = Path.Combine(dossier, "m" + modele + ".png");
            if (File.Exists(chemin)) t = CarteDQ4.ChargerPng(chemin, false, true);
            else Debug.LogWarning("texture de monstre absente : " + chemin);
            textures[modele] = t;
            return t;
        }

        Monstre Slot(int s)
        {
            Monstre e;
            if (slots.TryGetValue(s, out e)) return e;
            e = new Monstre();
            e.Go = new GameObject("monstre_" + s);
            e.Go.transform.SetParent(parent, false);
            e.Go.SetActive(false);
            e.Mesh = new Mesh();
            e.Mesh.name = "monstre_" + s;
            e.Go.AddComponent<MeshFilter>().sharedMesh = e.Mesh;
            e.R = e.Go.AddComponent<MeshRenderer>();
            e.Mat = new Material(shader);
            e.Mat.SetFloat("_Cutoff", 0.05f);
            e.Mat.SetFloat("_Net", 1f);
            e.Mat.SetFloat("_ZWrite", 0f);
            e.R.sharedMaterial = e.Mat;
            slots[s] = e;
            return e;
        }

        static float[] LireFloats(string b64)
        {
            byte[] b = Convert.FromBase64String(b64);
            float[] f = new float[b.Length / 4];
            Buffer.BlockCopy(b, 0, f, 0, f.Length * 4);
            return f;
        }

        static int[] LireEntiers(string b64)
        {
            byte[] b = Convert.FromBase64String(b64);
            int[] f = new int[b.Length / 4];
            Buffer.BlockCopy(b, 0, f, 0, f.Length * 4);
            return f;
        }

        /// <summary>Un sommet = [u, v, x, y, z] ; la coordonnee v du moteur est celle de la visionneuse + 1.</summary>
        static void Decouper(float[] f, out Vector3[] pos, out Vector2[] uv)
        {
            int n = f.Length / 5;
            pos = new Vector3[n];
            uv = new Vector2[n];
            for (int i = 0; i < n; i++)
            {
                uv[i] = new Vector2(f[i * 5], 1f + f[i * 5 + 1]);
                pos[i] = new Vector3(f[i * 5 + 2], f[i * 5 + 3], f[i * 5 + 4]);
            }
        }

        static void Reconstruire(Monstre e)
        {
            e.Sale = false;
            int n = e.Pos.Length;
            int max = -1;
            for (int i = 0; i < e.Idx.Length; i++) if (e.Idx[i] > max) max = e.Idx[i];
            if (n == 0 || e.Idx.Length < 3 || e.Idx.Length % 3 != 0 || max >= n)
            {
                e.Pret = false;
                Debug.LogWarning("maillage de monstre incoherent : " + n + " sommets, " + e.Idx.Length + " indices, indice max " + max);
                return;
            }
            Color32[] col = new Color32[n];
            for (int i = 0; i < n; i++)
            {
                byte a = 255;
                if (e.Quads != null && e.Quads.Length > 0) a = e.Quads[Mathf.Min(i / 4, e.Quads.Length - 1)];     // un octet par quad = opacite de la partie
                col[i] = new Color32(255, 255, 255, a);
            }
            Mesh m = e.Mesh;
            m.Clear();
            if (n > 65000) m.indexFormat = UnityEngine.Rendering.IndexFormat.UInt32;
            m.vertices = e.Pos;
            m.uv = e.Uv;
            m.colors32 = col;
            m.triangles = e.Idx;
            m.bounds = new Bounds(Vector3.zero, new Vector3(200f, 200f, 200f));      // sommets dynamiques : pas de recalcul, jamais ecarte par le frustum
            e.Pret = true;
        }

        static int FileDeRendu(int sh)
        {
            // Custom/DssaChara, DssaMonster0, DssaMonster1, DssaCharaTransparent : ordre de dessin des shaders d'origine (zone "Transparent" d'Unity : tri du plus loin au plus proche)
            switch (sh) { case 0: return 3100; case 1: return 3150; case 2: return 3200; case 3: return 3850; default: return 3150; }
        }

        /// <summary>Met a jour les monstres. `billboard` = rotation de la camera x Euler(0, 180, 0) (MeshChara.SetBillboard).</summary>
        public void Appliquer(MonT[] liste, Quaternion billboard)
        {
            actifs.Clear();
            NbVisibles = 0;
            if (liste != null)
            {
                foreach (MonT m in liste)
                {
                    if (m.p == null || m.p.Length < 3) continue;
                    Monstre e = Slot(m.s);
                    if (!string.IsNullOrEmpty(m.vb)) { Decouper(LireFloats(m.vb), out e.Pos, out e.Uv); e.Sale = true; }
                    if (!string.IsNullOrEmpty(m.ib)) { e.Idx = LireEntiers(m.ib); e.Sale = true; }
                    if (!string.IsNullOrEmpty(m.qb)) { e.Quads = Convert.FromBase64String(m.qb); e.Sale = true; }
                    if (e.Sale && e.Pos != null && e.Idx != null) Reconstruire(e);
                    if (e.Modele != m.m)
                    {
                        e.Modele = m.m;
                        e.Mat.mainTexture = Texture(m.m);
                    }
                    if (e.Shader != m.sh)
                    {
                        e.Shader = m.sh;
                        e.Mat.SetFloat("_ZTest", m.sh == 3 ? 8f : 4f);           // DssaCharaTransparent : ZTest Always
                        e.Mat.renderQueue = FileDeRendu(m.sh);
                    }
                    float ox = m.o != null && m.o.Length >= 2 ? m.o[0] : 0f, oy = m.o != null && m.o.Length >= 2 ? m.o[1] : 0f;
                    e.Mat.SetVector("_Rect", new Vector4(ox, oy, 1f + ox, 1f + oy));          // decalage de texture (paquet 26)
                    float al = m.al > 0f ? m.al : (m.vi != 0 ? 1f : 0f);                       // fondu de disparition d'un monstre vaincu
                    Color c = m.c != null && m.c.Length >= 3 ? new Color(m.c[0], m.c[1], m.c[2], al) : new Color(1f, 1f, 1f, al);
                    e.Mat.SetColor("_Color", c);
                    e.Go.transform.SetPositionAndRotation(new Vector3(m.p[0], m.p[1], m.p[2]), billboard);
                    bool visible = m.vi != 0 && e.Pret && e.Mat.mainTexture != null;
                    if (visible) { actifs.Add(m.s); NbVisibles++; }
                }
            }
            foreach (KeyValuePair<int, Monstre> kv in slots)
            {
                bool actif = actifs.Contains(kv.Key);
                if (kv.Value.Actif != actif) { kv.Value.Go.SetActive(actif); kv.Value.Actif = actif; }
            }
        }

        public void Detruire()
        {
            foreach (KeyValuePair<int, Monstre> kv in slots)
            {
                if (kv.Value.Go != null) UnityEngine.Object.Destroy(kv.Value.Go);
                if (kv.Value.Mesh != null) UnityEngine.Object.Destroy(kv.Value.Mesh);
                if (kv.Value.Mat != null) UnityEngine.Object.Destroy(kv.Value.Mat);
            }
            slots.Clear();
            foreach (KeyValuePair<int, Texture2D> kv in textures) if (kv.Value != null) UnityEngine.Object.Destroy(kv.Value);
            textures.Clear();
        }
    }
}
