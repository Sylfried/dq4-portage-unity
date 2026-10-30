using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Text;
using UnityEngine;
using UnityEngine.UI;

namespace DQ4
{
    /// <summary>Interface en uGUI (Canvas Scaler 1920x1080 : s'adapte seule a toute proportion d'ecran) :
    /// fenetre de message (bitmap de texte rendu par le moteur), fenetres de menu en texte simple, ligne d'etat.
    /// Premiere version : fonctionnelle, pas fidele (les vraies fenetres sont du code C# compile du jeu d'origine).</summary>
    public class InterfaceDQ4
    {
        const float ECH_MSG = 1.95f;                 // 1 pixel du canevas du moteur = 1,3 pixel a 720p = 1,95 pixel a 1080p (comme la visionneuse)
        static readonly Color FOND = new Color(8f / 255f, 12f / 255f, 70f / 255f, 235f / 255f);

        class PanneauMenu
        {
            public RectTransform Racine, Bord, Fond;
            public Text Gauche, Droite;
            public string Cle = "";
        }

        public string Attente = "";

        readonly Canvas canvas;
        readonly Font police;
        readonly Text statut, attente;
        readonly RectTransform msgBord, msgFond, msgImageRt;
        readonly RawImage msgImage;
        Texture2D msgTex;
        int msgN = -1;
        readonly Dictionary<int, PanneauMenu> menus = new Dictionary<int, PanneauMenu>();
        readonly HashSet<int> menusVus = new HashSet<int>();

        public InterfaceDQ4()
        {
            police = TrouverPolice();
            GameObject go = new GameObject("DQ4 Interface");
            canvas = go.AddComponent<Canvas>();
            canvas.renderMode = RenderMode.ScreenSpaceOverlay;
            canvas.sortingOrder = 10;
            CanvasScaler cs = go.AddComponent<CanvasScaler>();
            cs.uiScaleMode = CanvasScaler.ScaleMode.ScaleWithScreenSize;
            cs.referenceResolution = new Vector2(1920f, 1080f);
            cs.screenMatchMode = CanvasScaler.ScreenMatchMode.MatchWidthOrHeight;
            cs.matchWidthOrHeight = 0.5f;

            // fenetre de message : cadre blanc, fond bleu, bitmap du texte
            msgBord = NouveauRect("Message", canvas.transform);
            msgBord.gameObject.AddComponent<Image>().color = new Color(0.96f, 0.96f, 0.96f, 235f / 255f);
            msgFond = NouveauRect("Fond", msgBord);
            msgFond.gameObject.AddComponent<Image>().color = FOND;
            msgFond.anchorMin = Vector2.zero; msgFond.anchorMax = Vector2.one;
            msgFond.offsetMin = new Vector2(6f, 6f); msgFond.offsetMax = new Vector2(-6f, -6f);
            msgImageRt = NouveauRect("Texte", msgBord);
            msgImage = msgImageRt.gameObject.AddComponent<RawImage>();
            msgImage.raycastTarget = false;
            msgBord.gameObject.SetActive(false);

            statut = NouveauTexte("Statut", canvas.transform, 22, TextAnchor.UpperLeft, new Color(1f, 0.9f, 0.6f, 1f));
            statut.rectTransform.anchorMin = statut.rectTransform.anchorMax = new Vector2(0f, 1f);
            statut.rectTransform.pivot = new Vector2(0f, 1f);
            statut.rectTransform.anchoredPosition = new Vector2(16f, -12f);
            statut.rectTransform.sizeDelta = new Vector2(1500f, 200f);
            statut.gameObject.AddComponent<Shadow>();

            attente = NouveauTexte("Attente", canvas.transform, 40, TextAnchor.MiddleCenter, Color.white);
            attente.rectTransform.anchorMin = Vector2.zero; attente.rectTransform.anchorMax = Vector2.one;
            attente.rectTransform.offsetMin = attente.rectTransform.offsetMax = Vector2.zero;
            attente.gameObject.AddComponent<Shadow>();
        }

        static Font TrouverPolice()
        {
            try
            {
                Font f = Font.CreateDynamicFontFromOSFont(new string[] { "Segoe UI", "Arial", "Verdana" }, 28);
                if (f != null) return f;
            }
            catch (Exception) { }
            return Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
        }

        static RectTransform NouveauRect(string nom, Transform parent)
        {
            GameObject go = new GameObject(nom, typeof(RectTransform));
            go.transform.SetParent(parent, false);
            return go.GetComponent<RectTransform>();
        }

        Text NouveauTexte(string nom, Transform parent, int taille, TextAnchor ancre, Color couleur)
        {
            RectTransform rt = NouveauRect(nom, parent);
            Text t = rt.gameObject.AddComponent<Text>();
            t.font = police;
            t.fontSize = taille;
            t.alignment = ancre;
            t.color = couleur;
            t.horizontalOverflow = HorizontalWrapMode.Overflow;
            t.verticalOverflow = VerticalWrapMode.Overflow;
            t.supportRichText = false;
            t.raycastTarget = false;
            return t;
        }

        public void MajStatut(string texte)
        {
            statut.text = texte;
            attente.text = Attente ?? "";
        }

        // ---- fenetre de message
        static byte[] Decompresser(byte[] src, int attendu)
        {
            using (MemoryStream ms = new MemoryStream(src))
            using (DeflateStream ds = new DeflateStream(ms, CompressionMode.Decompress))
            {
                byte[] sortie = new byte[attendu];
                int lu = 0;
                while (lu < attendu)
                {
                    int n = ds.Read(sortie, lu, attendu - lu);
                    if (n <= 0) break;
                    lu += n;
                }
                return sortie;
            }
        }

        public void Message(MsgT m)
        {
            if (m == null) return;
            if (!string.IsNullOrEmpty(m.b) && m.n != msgN && m.w > 0 && m.h > 0)
            {
                msgN = m.n;
                byte[] a = Decompresser(Convert.FromBase64String(m.b), m.w * m.h);
                if (msgTex == null || msgTex.width != m.w || msgTex.height != m.h)
                {
                    if (msgTex != null) UnityEngine.Object.Destroy(msgTex);
                    msgTex = new Texture2D(m.w, m.h, TextureFormat.RGBA32, false);
                    msgTex.filterMode = FilterMode.Bilinear;
                    msgTex.wrapMode = TextureWrapMode.Clamp;
                    msgImage.texture = msgTex;
                    float marge = 18f * ECH_MSG;
                    float largeurTexte = m.w * ECH_MSG;
                    float bw = largeurTexte * 0.78f + 2f * marge, bh = m.h * ECH_MSG + 2f * marge;
                    msgBord.anchorMin = msgBord.anchorMax = new Vector2(0.5f, 0f);
                    msgBord.pivot = new Vector2(0.5f, 0f);
                    msgBord.anchoredPosition = new Vector2(0f, 22f * ECH_MSG);
                    msgBord.sizeDelta = new Vector2(bw, bh);
                    msgImageRt.anchorMin = msgImageRt.anchorMax = new Vector2(0f, 1f);
                    msgImageRt.pivot = new Vector2(0f, 1f);
                    msgImageRt.anchoredPosition = new Vector2(marge, -marge);
                    msgImageRt.sizeDelta = new Vector2(largeurTexte, m.h * ECH_MSG);
                }
                Color32[] px = new Color32[m.w * m.h];
                for (int y = 0; y < m.h; y++)
                {
                    int ligneTex = (m.h - 1 - y) * m.w;          // la ligne 0 du moteur est en haut ; celle d'une texture Unity est en bas
                    for (int x = 0; x < m.w; x++) px[ligneTex + x] = new Color32(255, 255, 255, a[y * m.w + x]);
                }
                msgTex.SetPixels32(px);
                msgTex.Apply(false);
            }
            msgBord.gameObject.SetActive(m.v && msgTex != null);
        }

        // ---- fenetres de menu
        PanneauMenu Panneau(int port)
        {
            PanneauMenu pm;
            if (menus.TryGetValue(port, out pm)) return pm;
            pm = new PanneauMenu();
            pm.Racine = NouveauRect("Menu " + port, canvas.transform);
            pm.Bord = pm.Racine;
            pm.Racine.gameObject.AddComponent<Image>().color = new Color(0.62f, 0.62f, 0.68f, 235f / 255f);
            pm.Fond = NouveauRect("Fond", pm.Racine);
            pm.Fond.gameObject.AddComponent<Image>().color = FOND;
            pm.Fond.anchorMin = Vector2.zero; pm.Fond.anchorMax = Vector2.one;
            pm.Fond.offsetMin = new Vector2(5f, 5f); pm.Fond.offsetMax = new Vector2(-5f, -5f);
            pm.Gauche = NouveauTexte("Gauche", pm.Racine, 28, TextAnchor.UpperLeft, Color.white);
            pm.Droite = NouveauTexte("Droite", pm.Racine, 28, TextAnchor.UpperRight, Color.white);
            menus[port] = pm;
            return pm;
        }

        public void Menus(MenuT[] liste)
        {
            menusVus.Clear();
            float xGauche = 40f;
            if (liste != null)
            {
                foreach (MenuT m in liste)
                {
                    if (m.l == null || m.l.Length == 0) continue;
                    PanneauMenu pm = Panneau(m.p);
                    menusVus.Add(m.p);
                    pm.Racine.gameObject.SetActive(true);
                    StringBuilder g = new StringBuilder(), d = new StringBuilder();
                    for (int i = 0; i < m.l.Length; i++)
                    {
                        LigneT l = m.l[i];
                        string pre = l.ti ? "" : ((m.a && l.r == m.cur) ? "> " : "    ");
                        if (i > 0) { g.Append('\n'); d.Append('\n'); }
                        g.Append(pre).Append(l.t ?? "");
                        d.Append(l.d ?? "");
                    }
                    string cle = g.ToString() + "\u0001" + d.ToString() + (m.a ? "A" : "-");
                    if (cle != pm.Cle)
                    {
                        pm.Cle = cle;
                        pm.Gauche.text = g.ToString();
                        pm.Droite.text = d.ToString();
                        pm.Bord.GetComponent<Image>().color = m.a ? new Color(1f, 1f, 1f, 235f / 255f) : new Color(0.62f, 0.62f, 0.68f, 235f / 255f);
                        float pad = 22f, ecart = 36f;
                        float wl = pm.Gauche.preferredWidth, wr = pm.Droite.preferredWidth, h = pm.Gauche.preferredHeight;
                        float w = pad * 2f + wl + (wr > 1f ? wr + ecart : 0f);
                        pm.Racine.sizeDelta = new Vector2(w, pad * 2f + h);
                        pm.Gauche.rectTransform.anchorMin = pm.Gauche.rectTransform.anchorMax = new Vector2(0f, 1f);
                        pm.Gauche.rectTransform.pivot = new Vector2(0f, 1f);
                        pm.Gauche.rectTransform.anchoredPosition = new Vector2(pad, -pad);
                        pm.Gauche.rectTransform.sizeDelta = new Vector2(wl, h);
                        pm.Droite.rectTransform.anchorMin = pm.Droite.rectTransform.anchorMax = new Vector2(1f, 1f);
                        pm.Droite.rectTransform.pivot = new Vector2(1f, 1f);
                        pm.Droite.rectTransform.anchoredPosition = new Vector2(-pad, -pad);
                        pm.Droite.rectTransform.sizeDelta = new Vector2(wr, h);
                    }
                    RectTransform rt = pm.Racine;
                    if (m.p == 3)          // or : en haut a droite
                    {
                        rt.anchorMin = rt.anchorMax = new Vector2(1f, 1f); rt.pivot = new Vector2(1f, 1f); rt.anchoredPosition = new Vector2(-24f, -24f);
                    }
                    else if (m.p == 2)     // Oui / Non : a droite
                    {
                        rt.anchorMin = rt.anchorMax = new Vector2(1f, 0.62f); rt.pivot = new Vector2(1f, 1f); rt.anchoredPosition = new Vector2(-40f, 0f);
                    }
                    else                   // les autres s'empilent de gauche a droite
                    {
                        rt.anchorMin = rt.anchorMax = new Vector2(0f, 1f); rt.pivot = new Vector2(0f, 1f); rt.anchoredPosition = new Vector2(xGauche, -24f);
                        xGauche += rt.sizeDelta.x + 18f;
                    }
                }
            }
            foreach (KeyValuePair<int, PanneauMenu> kv in menus)
                if (!menusVus.Contains(kv.Key) && kv.Value.Racine.gameObject.activeSelf) kv.Value.Racine.gameObject.SetActive(false);
        }
    }
}
