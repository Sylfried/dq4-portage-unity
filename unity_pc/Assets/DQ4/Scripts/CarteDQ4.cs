using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using UnityEngine;
using UnityEngine.Rendering;

namespace DQ4
{
    /// <summary>Une porte (ou autre objet animable) : pivote de 90 degres autour de sa charniere quand le moteur le demande
    /// (meme animation que la visionneuse : 18 images, lissee).</summary>
    public class Porte
    {
        public Transform Pivot;
        public float Signe;
        public int Debut = -1;

        public void Maj(int image)
        {
            if (Debut < 0) { Pivot.localRotation = Quaternion.identity; return; }
            float t = Mathf.Clamp01((image - Debut + 1f) / 18f);
            t = t * t * (3f - 2f * t);
            Pivot.localRotation = Quaternion.Euler(0f, Signe * 90f * t, 0f);
        }
    }

    /// <summary>Une carte (ville, interieur, decor de combat) construite depuis le dossier exporte par le pont (geo.bin + textures PNG).</summary>
    public class CarteDQ4
    {
        public GameObject Racine;
        public float Bx0, Bx1;                      // bornes du decor en x (cadrage 16/9)
        public int NbParties, NbSommets;
        public readonly Dictionary<int, Porte> Portes = new Dictionary<int, Porte>();

        class Partie
        {
            public int Objet;
            public string Nom;
            public int Tex;
            public Vector3[] Pos;
            public Vector2[] Uv;
            public Color32[] Col;
        }

        public static Texture2D ChargerPng(string chemin, bool mips, bool repeter)
        {
            Texture2D t = new Texture2D(2, 2, TextureFormat.RGBA32, mips);
            t.LoadImage(File.ReadAllBytes(chemin), false);
            t.wrapMode = repeter ? TextureWrapMode.Repeat : TextureWrapMode.Clamp;
            t.filterMode = mips ? FilterMode.Trilinear : FilterMode.Bilinear;
            t.anisoLevel = mips ? 8 : 1;
            return t;
        }

        static Mesh CreerMaillage(string nom, Vector3[] pos, Vector2[] uv, Color32[] col)
        {
            Mesh m = new Mesh();
            m.name = nom;
            if (pos.Length > 65000) m.indexFormat = IndexFormat.UInt32;
            m.vertices = pos;
            m.uv = uv;
            m.colors32 = col;
            int[] tri = new int[pos.Length];
            for (int i = 0; i < tri.Length; i++) tri[i] = i;      // sommets deja deplies : 3 par triangle
            m.triangles = tri;
            m.RecalculateBounds();
            return m;
        }

        static Vector3[] LireVecteurs3(BinaryReader r, int n)
        {
            byte[] b = r.ReadBytes(n * 12);
            float[] f = new float[n * 3];
            Buffer.BlockCopy(b, 0, f, 0, b.Length);
            Vector3[] v = new Vector3[n];
            for (int i = 0; i < n; i++) v[i] = new Vector3(f[3 * i], f[3 * i + 1], f[3 * i + 2]);
            return v;
        }

        static Vector2[] LireVecteurs2(BinaryReader r, int n)
        {
            byte[] b = r.ReadBytes(n * 8);
            float[] f = new float[n * 2];
            Buffer.BlockCopy(b, 0, f, 0, b.Length);
            Vector2[] v = new Vector2[n];
            for (int i = 0; i < n; i++) v[i] = new Vector2(f[2 * i], f[2 * i + 1]);
            return v;
        }

        static Color32[] LireCouleurs(BinaryReader r, int n)
        {
            byte[] b = r.ReadBytes(n * 4);
            Color32[] c = new Color32[n];
            for (int i = 0; i < n; i++) c[i] = new Color32(b[4 * i], b[4 * i + 1], b[4 * i + 2], b[4 * i + 3]);
            return c;
        }

        public static CarteDQ4 Charger(string dossier, string nom, Shader shader)
        {
            CarteDQ4 carte = new CarteDQ4();
            List<Partie> parties = new List<Partie>();
            int nt;
            using (FileStream f = File.OpenRead(Path.Combine(dossier, "geo.bin")))
            using (BinaryReader r = new BinaryReader(f))
            {
                byte[] magie = r.ReadBytes(4);
                if (magie.Length != 4 || magie[0] != (byte)'D' || magie[1] != (byte)'Q' || magie[2] != (byte)'4' || magie[3] != (byte)'M')
                    throw new InvalidDataException("geo.bin invalide dans " + dossier);
                int version = r.ReadInt32();
                if (version != 1) throw new InvalidDataException("version d'export non geree : " + version);
                carte.Bx0 = r.ReadSingle();
                carte.Bx1 = r.ReadSingle();
                nt = r.ReadInt32();
                int np = r.ReadInt32();
                for (int k = 0; k < np; k++)
                {
                    Partie p = new Partie();
                    p.Objet = r.ReadInt32();
                    int ln = r.ReadInt32();
                    p.Nom = Encoding.UTF8.GetString(r.ReadBytes(ln));
                    p.Tex = r.ReadInt32();
                    int nv = r.ReadInt32();
                    p.Pos = LireVecteurs3(r, nv);
                    p.Uv = LireVecteurs2(r, nv);
                    p.Col = LireCouleurs(r, nv);
                    parties.Add(p);
                    carte.NbSommets += nv;
                }
            }
            carte.NbParties = parties.Count;

            Material[] materiaux = new Material[nt];
            for (int i = 0; i < nt; i++)
            {
                Texture2D tex = ChargerPng(Path.Combine(dossier, "tex_" + i + ".png"), true, true);
                Material m = new Material(shader);
                m.name = "carte_" + nom + "_" + i;
                m.mainTexture = tex;
                m.SetFloat("_Cutoff", 0.5f);
                m.SetFloat("_Net", 0f);
                m.renderQueue = 2450;
                materiaux[i] = m;
            }

            carte.Racine = new GameObject("Carte " + nom);

            // decor fixe
            foreach (Partie p in parties)
            {
                if (p.Objet >= 0) continue;
                GameObject go = new GameObject("decor_" + p.Tex);
                go.transform.SetParent(carte.Racine.transform, false);
                go.AddComponent<MeshFilter>().sharedMesh = CreerMaillage(go.name, p.Pos, p.Uv, p.Col);
                go.AddComponent<MeshRenderer>().sharedMaterial = materiaux[p.Tex];
            }

            // objets animables (portes) : un pivot a la charniere par objet
            Dictionary<int, List<Partie>> objets = new Dictionary<int, List<Partie>>();
            foreach (Partie p in parties)
            {
                if (p.Objet < 0) continue;
                List<Partie> l;
                if (!objets.TryGetValue(p.Objet, out l)) { l = new List<Partie>(); objets[p.Objet] = l; }
                l.Add(p);
            }
            foreach (KeyValuePair<int, List<Partie>> kv in objets)
            {
                float mini = float.MaxValue, maxi = float.MinValue, sz = 0f;
                int n = 0;
                foreach (Partie p in kv.Value)
                    foreach (Vector3 v in p.Pos)
                    {
                        if (v.x < mini) mini = v.x;
                        if (v.x > maxi) maxi = v.x;
                        sz += v.z;
                        n++;
                    }
                if (n == 0) continue;
                float cx = (mini + maxi) / 2f;
                Vector3 charniere = new Vector3(cx > 0f ? maxi : mini, 0f, sz / n);
                GameObject pivot = new GameObject("objet_" + kv.Key + "_" + kv.Value[0].Nom);
                pivot.transform.SetParent(carte.Racine.transform, false);
                pivot.transform.localPosition = charniere;
                foreach (Partie p in kv.Value)
                {
                    Vector3[] rel = new Vector3[p.Pos.Length];
                    for (int i = 0; i < rel.Length; i++) rel[i] = p.Pos[i] - charniere;
                    GameObject go = new GameObject("morceau_" + p.Tex);
                    go.transform.SetParent(pivot.transform, false);
                    go.AddComponent<MeshFilter>().sharedMesh = CreerMaillage(go.name, rel, p.Uv, p.Col);
                    go.AddComponent<MeshRenderer>().sharedMaterial = materiaux[p.Tex];
                }
                Porte porte = new Porte();
                porte.Pivot = pivot.transform;
                porte.Signe = cx > 0f ? -1f : 1f;
                carte.Portes[kv.Key] = porte;
            }
            return carte;
        }

        public void Detruire()
        {
            if (Racine != null) UnityEngine.Object.Destroy(Racine);
            Racine = null;
        }
    }
}
