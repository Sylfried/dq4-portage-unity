using System.Collections.Generic;
using System.IO;
using UnityEngine;

namespace DQ4
{
    /// <summary>Les personnages : des panneaux verticaux (feuille de sprites `h&lt;id&gt;`, cases de 24x32) toujours tournes vers la camera,
    /// poses aux positions donnees par le moteur (memes dimensions que la visionneuse : echelle 0,045).</summary>
    public class SpritesDQ4
    {
        const int CASE_L = 24, CASE_H = 32;
        const float ECHELLE = 0.045f;

        class Feuille
        {
            public Texture2D Tex;
            public Material Mat;       // dessin normal
            public Material MatSil;    // silhouette derriere un decor (equipe)
        }

        class Entree
        {
            public GameObject Go, Sil;
            public MeshRenderer R, RSil;
            public int Id = int.MinValue;
            public MaterialPropertyBlock Pb = new MaterialPropertyBlock();
            public bool Actif;
        }

        readonly Transform parent;
        readonly Shader shader;
        readonly string dossierSprites;
        readonly Dictionary<int, Feuille> feuilles = new Dictionary<int, Feuille>();
        readonly Dictionary<int, Entree> slots = new Dictionary<int, Entree>();
        readonly HashSet<int> actifs = new HashSet<int>();
        Mesh quad;

        public int NbVisibles;

        public SpritesDQ4(Transform parent, Shader shader, string dossierExport)
        {
            this.parent = parent;
            this.shader = shader;
            dossierSprites = Path.Combine(dossierExport, "sprites");
        }

        Mesh Quad()
        {
            if (quad != null) return quad;
            float w = CASE_L * ECHELLE, h = CASE_H * ECHELLE;
            quad = new Mesh();
            quad.name = "quad_sprite";
            quad.vertices = new Vector3[] { new Vector3(-w / 2f, 0f, 0f), new Vector3(w / 2f, 0f, 0f), new Vector3(w / 2f, h, 0f), new Vector3(-w / 2f, h, 0f) };
            quad.uv = new Vector2[] { new Vector2(0f, 0f), new Vector2(1f, 0f), new Vector2(1f, 1f), new Vector2(0f, 1f) };
            quad.colors32 = new Color32[] { new Color32(255, 255, 255, 255), new Color32(255, 255, 255, 255), new Color32(255, 255, 255, 255), new Color32(255, 255, 255, 255) };
            quad.triangles = new int[] { 0, 1, 2, 0, 2, 3 };
            quad.RecalculateBounds();
            return quad;
        }

        Feuille Obtenir(int id)
        {
            Feuille f;
            if (feuilles.TryGetValue(id, out f)) return f;
            string chemin = Path.Combine(dossierSprites, "h" + id + ".png");
            if (!File.Exists(chemin)) { feuilles[id] = null; Debug.LogWarning("feuille de sprites absente : " + chemin); return null; }
            f = new Feuille();
            f.Tex = CarteDQ4.ChargerPng(chemin, false, false);
            f.Mat = Creer(f.Tex, 4f, 1f, 2460);
            f.MatSil = Creer(f.Tex, 5f, 0f, 2470);
            feuilles[id] = f;
            return f;
        }

        Material Creer(Texture2D tex, float ztest, float zwrite, int file)
        {
            Material m = new Material(shader);
            m.mainTexture = tex;
            m.SetFloat("_Cutoff", 0.05f);
            m.SetFloat("_Net", 1f);                 // pixel art net
            m.SetFloat("_ZTest", ztest);
            m.SetFloat("_ZWrite", zwrite);
            m.renderQueue = file;
            return m;
        }

        Entree Slot(int s)
        {
            Entree e;
            if (slots.TryGetValue(s, out e)) return e;
            e = new Entree();
            e.Go = new GameObject("sprite_" + s);
            e.Go.transform.SetParent(parent, false);
            e.Go.AddComponent<MeshFilter>().sharedMesh = Quad();
            e.R = e.Go.AddComponent<MeshRenderer>();
            e.Sil = new GameObject("silhouette");
            e.Sil.transform.SetParent(e.Go.transform, false);
            e.Sil.AddComponent<MeshFilter>().sharedMesh = Quad();
            e.RSil = e.Sil.AddComponent<MeshRenderer>();
            slots[s] = e;
            return e;
        }

        static void Regler(MeshRenderer r, MaterialPropertyBlock pb, Vector4 rect, Color couleur)
        {
            pb.Clear();
            pb.SetVector("_Rect", rect);
            pb.SetColor("_Color", couleur);
            r.SetPropertyBlock(pb);
        }

        /// <summary>Met a jour les sprites d'apres la liste du moteur (les emplacements absents disparaissent).</summary>
        public void Appliquer(SprT[] liste)
        {
            actifs.Clear();
            NbVisibles = 0;
            if (liste != null)
            {
                foreach (SprT s in liste)
                {
                    if (s.p == null || s.p.Length < 3 || s.c == null || s.c.Length < 4) continue;
                    if (s.c[3] < 0.02f) continue;                       // invisible (fondu)
                    Feuille f = Obtenir(s.id);
                    if (f == null) continue;
                    Entree e = Slot(s.s);
                    actifs.Add(s.s);
                    if (e.Id != s.id)
                    {
                        e.Id = s.id;
                        e.R.sharedMaterial = f.Mat;
                        e.RSil.sharedMaterial = f.MatSil;
                    }
                    e.Go.transform.position = new Vector3(s.p[0], s.p[1], s.p[2]);
                    int nl = s.nl > 0 ? s.nl : 4;
                    int col = ((s.pose % 3) + 3) % 3, rang = ((s.dir % nl) + nl) % nl;
                    float tw = f.Tex.width, th = f.Tex.height;
                    Vector4 rect = new Vector4(col * CASE_L / tw, 1f - (rang + 1) * CASE_H / th, (col + 1) * CASE_L / tw, 1f - rang * CASE_H / th);
                    Color c = new Color(s.c[0], s.c[1], s.c[2], s.c[3]);
                    Regler(e.R, e.Pb, rect, c);
                    bool equipe = s.s < 8;                             // l'equipe reste visible en transparence derriere un decor
                    e.Sil.SetActive(equipe);
                    if (equipe) Regler(e.RSil, e.Pb, rect, new Color(c.r, c.g, c.b, c.a * 0.4f));
                    NbVisibles++;
                }
            }
            foreach (KeyValuePair<int, Entree> kv in slots)
            {
                bool actif = actifs.Contains(kv.Key);
                if (kv.Value.Actif != actif) { kv.Value.Go.SetActive(actif); kv.Value.Actif = actif; }
            }
        }

        /// <summary>Tourne les panneaux vers la camera (autour de l'axe vertical seulement).</summary>
        public void Orienter(Vector3 avantCamera)
        {
            Vector3 h = new Vector3(avantCamera.x, 0f, avantCamera.z);
            if (h.sqrMagnitude < 1e-6f) h = Vector3.forward;
            Quaternion q = Quaternion.LookRotation(h.normalized, Vector3.up);
            foreach (KeyValuePair<int, Entree> kv in slots) kv.Value.Go.transform.rotation = q;
        }

        public void Detruire()
        {
            foreach (KeyValuePair<int, Entree> kv in slots) if (kv.Value.Go != null) Object.Destroy(kv.Value.Go);
            slots.Clear();
        }
    }
}
