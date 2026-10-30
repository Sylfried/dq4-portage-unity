using System;
using System.Collections.Generic;
using System.IO;
using UnityEngine;

namespace DQ4
{
    /// <summary>Piste 3 : Unity comme afficheur du moteur du jeu (emule en Python par dq4_pc/pont.py).
    /// Se cree tout seul au lancement (Play) dans n'importe quelle scene : camera, decor, sprites et interface sont construits ici.
    /// Le moteur envoie l'etat de la scene ~30 fois par seconde ; Unity renvoie les touches.</summary>
    public class DQ4Jeu : MonoBehaviour
    {
        const float FOV_VERTICAL = 20f;                 // champ de vision vertical des villes (camera Unity du jeu)

        PontClient pont;
        Shader shader;
        Camera cam;
        CarteDQ4 carte;
        SpritesDQ4 sprites;
        MonstresDQ4 monstres;
        InterfaceDQ4 ui;

        string exportDir = "";
        string nomCarte = "";
        int ncCharge = -1;
        bool champ, combat;
        float[] camPos, camCible, camRot;
        float camFov;
        bool projectionCombat;
        int masqueEnvoye = -1;
        bool statut = true;                             // bandeau d'information en haut (F1)
        bool cadrage = true;                            // recentrage 16/9 identique a la visionneuse (F6)
        string erreur = "";

        int recues, recuesParSec, imagesUnity, fpsUnity;
        float tCompteur;
        float tCarte = -1f;
        int nbCaptures, erreursTrame;
        readonly HashSet<string> cartesCapturees = new HashSet<string>();

        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
        static void Demarrer()
        {
            if (FindObjectOfType<DQ4Jeu>() != null) return;
            new GameObject("DQ4").AddComponent<DQ4Jeu>();
        }

        void Awake()
        {
            JournalDQ4.Demarrer();
            Application.runInBackground = true;
            QualitySettings.antiAliasing = 4;
            QualitySettings.anisotropicFiltering = AnisotropicFiltering.ForceEnable;

            shader = Shader.Find("DQ4/Unlit");
            if (shader == null) Debug.LogError("Shader DQ4/Unlit introuvable (Assets/DQ4/Shaders/DQ4Unlit.shader).");
            else if (!shader.isSupported) Debug.LogError("Shader DQ4/Unlit non supporte par cette carte graphique.");

            foreach (Camera c in Camera.allCameras) c.enabled = false;
            GameObject gc = new GameObject("DQ4 Camera");
            gc.tag = "MainCamera";
            cam = gc.AddComponent<Camera>();
            cam.clearFlags = CameraClearFlags.SolidColor;
            cam.backgroundColor = Color.black;
            cam.fieldOfView = FOV_VERTICAL;
            cam.nearClipPlane = 1f;
            cam.farClipPlane = 200f;
            cam.allowMSAA = true;
            cam.transform.SetParent(transform, false);

            ui = new InterfaceDQ4();

            int port = 7740;
            string env = Environment.GetEnvironmentVariable("DQ4_PORT");
            int p;
            if (!string.IsNullOrEmpty(env) && int.TryParse(env, out p)) port = p;
            pont = new PontClient("127.0.0.1", port);
            pont.Demarrer();
            Debug.Log("DQ4 : en attente du pont sur 127.0.0.1:" + port + " (lance outils\\lancer_pont.bat)");
        }

        void OnDestroy()
        {
            if (pont != null) pont.Arreter();
            JournalDQ4.Arreter();
        }

        // ---- clavier (Input Manager classique)
        static bool Maintenue(KeyCode k)
        {
#if ENABLE_LEGACY_INPUT_MANAGER || !ENABLE_INPUT_SYSTEM
            return Input.GetKey(k);
#else
            return false;
#endif
        }

        static bool Pressee(KeyCode k)
        {
#if ENABLE_LEGACY_INPUT_MANAGER || !ENABLE_INPUT_SYSTEM
            return Input.GetKeyDown(k);
#else
            return false;
#endif
        }

        static int LireTouches()
        {
            int p = 0;
            if (Maintenue(KeyCode.UpArrow) || Maintenue(KeyCode.Z) || Maintenue(KeyCode.W)) p |= 0x1;
            if (Maintenue(KeyCode.DownArrow) || Maintenue(KeyCode.S)) p |= 0x2;
            if (Maintenue(KeyCode.LeftArrow) || Maintenue(KeyCode.Q) || Maintenue(KeyCode.A)) p |= 0x4;
            if (Maintenue(KeyCode.RightArrow) || Maintenue(KeyCode.D)) p |= 0x8;
            if (Maintenue(KeyCode.Return) || Maintenue(KeyCode.KeypadEnter) || Maintenue(KeyCode.Space) || Maintenue(KeyCode.E)) p |= 0x10;
            if (Maintenue(KeyCode.Backspace) || Maintenue(KeyCode.X)) p |= 0x20;
            return p;
        }

        // ---- boucle
        void Update()
        {
            imagesUnity++;
            tCompteur += Time.unscaledDeltaTime;
            if (tCompteur >= 1f)
            {
                fpsUnity = imagesUnity; recuesParSec = recues;
                imagesUnity = 0; recues = 0; tCompteur -= 1f;
            }

            int m = LireTouches();
            if (m != masqueEnvoye)
            {
                pont.Envoyer("K " + m);
                masqueEnvoye = m;
            }

            int n = 0;
            string ligne;
            while (n < 200 && pont.Recus.TryDequeue(out ligne))
            {
                try { Traiter(ligne); }
                catch (Exception ex)
                {
                    erreursTrame++;
                    if (erreursTrame <= 5 || erreursTrame % 300 == 0) Debug.LogError("Trame du pont non appliquée (" + erreursTrame + ") : " + ex);
                }
                n++;
            }

            if (Pressee(KeyCode.F6))
            {
                cadrage = !cadrage;
                Debug.Log("Recentrage 16/9 de la camera : " + (cadrage ? "oui" : "non (camera du moteur telle quelle)"));
            }
            if (Pressee(KeyCode.F1)) statut = !statut;
            if (Pressee(KeyCode.F12)) Capturer("manuel_" + DateTime.Now.ToString("HHmmss"));
            CaptureAuto();

            string etat = pont.Connecte ? "pont connecté" : "en attente du pont (double-clic sur outils\\lancer_pont.bat)";
            string txt = "DQ4 / Unity | " + etat + " | carte : " + (champ ? "monde (non géré)" : nomCarte + (combat ? " (combat)" : "")) + " | Unity " + fpsUnity + " i/s, moteur " + recuesParSec + " i/s"
                + "\nFlèches/ZQSD : marcher | Entrée/Espace : valider | Retour arrière/X : menu, annuler | F1 : masquer ce texte | F6 recentrage 16/9 : " + (cadrage ? "oui" : "non") + " | F12 : capture";
            if (!string.IsNullOrEmpty(erreur)) txt += "\n" + erreur;
            ui.MajStatut(statut ? txt : "");
        }

        /// <summary>Camera de combat du Unity d'origine (NativeManager.cmd_camera + SceneCamera.Update) : position telle quelle, rotation Euler(-x, -(y + 180), z),
        /// champ nominal 48 x 4/3 applique a l'horizontale d'une vue 4/3 (on garde la meme hauteur de champ en 16/9 : plus de decor sur les cotes), decalage vertical 0,04.</summary>
        void CameraCombat()
        {
            cam.transform.position = new Vector3(camPos[0], camPos[1], camPos[2]);
            cam.transform.rotation = RotationCamera();
            float nom = camFov > 0f ? camFov : 48f;
            float fovH = nom * (4f / 3f) * Mathf.Deg2Rad;
            float fovV = 2f * Mathf.Atan(Mathf.Tan(fovH * 0.5f) / (4f / 3f)) * Mathf.Rad2Deg;
            cam.nearClipPlane = 0.25f;
            cam.farClipPlane = 500f;
            Matrix4x4 p = Matrix4x4.Perspective(fovV, cam.aspect, 0.25f, 500f);
            p[1, 3] += 0.04f;
            cam.projectionMatrix = p;
            projectionCombat = true;
            if (sprites != null) sprites.Orienter(Vector3.forward);
        }

        Quaternion RotationCamera()
        {
            return Quaternion.Euler(-camRot[0], -(camRot[1] + 180f), camRot[2]);
        }

        void LateUpdate()
        {
            if (combat && camPos != null && camRot != null) { CameraCombat(); return; }
            if (projectionCombat)
            {
                cam.ResetProjectionMatrix();
                cam.nearClipPlane = 1f;
                cam.farClipPlane = 200f;
                projectionCombat = false;
            }
            if (camPos == null || camCible == null) return;
            Vector3 cp = new Vector3(camPos[0], camPos[1], camPos[2]);
            Vector3 cc = new Vector3(camCible[0], camCible[1], camCible[2]);
            if (cadrage && carte != null && !champ) Cadrer(ref cp, ref cc);
            cam.transform.position = cp;
            Vector3 d = cc - cp;
            if (d.sqrMagnitude > 1e-8f) cam.transform.rotation = Quaternion.LookRotation(d, Vector3.up);
            cam.fieldOfView = FOV_VERTICAL;
            if (sprites != null) sprites.Orienter(d);
        }

        /// <summary>Meme recentrage que la visionneuse : le moteur centre la camera pour un ecran portrait ;
        /// en large on la garde dans les bornes du decor pour que son bord reste hors champ.</summary>
        void Cadrer(ref Vector3 cp, ref Vector3 cc)
        {
            Vector3 fv = cc - cp;
            fv.y = 0f;
            float lg = fv.magnitude;
            if (lg < 1e-6f) return;
            fv /= lg;
            if (Mathf.Abs(fv.z) <= 0.95f) return;
            float w = (cc - cp).magnitude * Mathf.Tan(FOV_VERTICAL * 0.5f * Mathf.Deg2Rad) * cam.aspect * 0.85f;
            float lo = carte.Bx0 + w, hi = carte.Bx1 - w;
            float cx = lo >= hi ? (carte.Bx0 + carte.Bx1) / 2f : Mathf.Min(Mathf.Max(cc.x, lo), hi);
            float dx = cx - cc.x;
            cp.x += dx;
            cc.x += dx;
        }

        // ---- messages du pont
        void Traiter(string ligne)
        {
            Trame t;
            try { t = JsonUtility.FromJson<Trame>(ligne); }
            catch (Exception ex) { Debug.LogWarning("Message du pont illisible : " + ex.Message); return; }
            if (t == null) return;

            if (shader == null) return;                 // erreur deja signalee au demarrage
            if (t.hello > 0)
            {
                exportDir = t.export ?? "";
                masqueEnvoye = -1;
                ncCharge = -1;
                if (sprites != null) sprites.Detruire();
                sprites = new SpritesDQ4(transform, shader, exportDir);
                if (monstres != null) monstres.Detruire();
                monstres = new MonstresDQ4(transform, shader, exportDir);
                Debug.Log("Pont connecté (protocole " + t.proto + "), dossier d'export : " + exportDir);
                return;
            }
            if (!string.IsNullOrEmpty(t.attente) && string.IsNullOrEmpty(t.carte))
            {
                ui.Attente = t.attente;
                return;
            }
            AppliquerTrame(t);
        }

        void AppliquerTrame(Trame t)
        {
            recues++;
            ui.Attente = "";
            champ = t.champ == 1;
            combat = t.partie == 13;                    // partie 0xd : combat
            if (!string.IsNullOrEmpty(t.info)) erreur = t.info;

            if (!champ && !string.IsNullOrEmpty(t.carte) && (t.nc != ncCharge || t.carte != nomCarte)) ChargerCarte(t.carte, t.nc);     // le nom change aussi quand le moteur revient d'un combat
            if (carte != null && carte.Racine != null)
            {
                carte.Racine.SetActive(!champ);
                carte.Racine.transform.localScale = combat ? new Vector3(6f, 6f, 6f) : Vector3.one;      // en combat le decor est agrandi x6 (FldObject.SetDispScale)
            }

            if (t.fond != null && t.fond.Length >= 3) cam.backgroundColor = new Color(t.fond[0], t.fond[1], t.fond[2], 1f);
            if (t.cp != null && t.cp.Length >= 3 && t.cc != null && t.cc.Length >= 3)
            {
                camPos = t.cp;
                camCible = t.cc;
            }
            if (t.cp != null && t.cp.Length >= 3 && t.cr != null && t.cr.Length >= 3)
            {
                camPos = t.cp;
                camRot = t.cr;
            }
            camFov = t.fov;

            if (sprites != null) sprites.Appliquer(champ ? null : t.spr);
            if (monstres != null)
            {
                Quaternion bb = camRot != null && camRot.Length >= 3 ? RotationCamera() * Quaternion.Euler(0f, 180f, 0f) : Quaternion.identity;
                monstres.Appliquer(combat ? t.mon : null, bb);
            }

            if (carte != null)
            {
                if (t.an != null)
                {
                    for (int k = 0; k + 1 < t.an.Length; k += 2)
                    {
                        Porte p;
                        if (carte.Portes.TryGetValue(t.an[k], out p) && p.Debut < 0) p.Debut = t.i;
                    }
                }
                foreach (Porte p in carte.Portes.Values) p.Maj(t.i);
            }

            ui.Message(t.msg);
            ui.Menus(t.menus);
        }

        void ChargerCarte(string nom, int nc)
        {
            ncCharge = nc;
            nomCarte = nom;
            tCarte = Time.time;
            if (carte != null) { carte.Detruire(); carte = null; }
            if (shader == null) return;
            string dossier = Path.Combine(exportDir, "carte_" + nom);
            try
            {
                float t0 = Time.realtimeSinceStartup;
                carte = CarteDQ4.Charger(dossier, nom, shader);
                carte.Racine.transform.SetParent(transform, false);
                Debug.Log("Carte " + nom + " : " + carte.NbParties + " morceaux, " + carte.NbSommets + " sommets, " + carte.Portes.Count
                    + " objets animables, chargée en " + (Time.realtimeSinceStartup - t0).ToString("0.00") + " s");
                erreur = "";
            }
            catch (Exception ex)
            {
                carte = null;
                erreur = "Carte " + nom + " : " + ex.Message;
                Debug.LogError("Carte " + nom + " : " + ex);
            }
            Resources.UnloadUnusedAssets();
        }

        // ---- captures d'ecran (dossier captures/unity a la racine du depot)
        void Capturer(string nom)
        {
            if (string.IsNullOrEmpty(JournalDQ4.Dossier)) return;
            string chemin = Path.Combine(JournalDQ4.Dossier, nom + ".png");
            ScreenCapture.CaptureScreenshot(chemin);
            nbCaptures++;
            Debug.Log("Capture : " + chemin + " (fenêtre " + Screen.width + "x" + Screen.height + ")");
        }

        /// <summary>Une capture automatique par carte (2 s apres son affichage, quand un personnage est visible), 4 au plus.</summary>
        void CaptureAuto()
        {
            if (nbCaptures >= 4 || carte == null || champ || tCarte < 0f) return;
            if (cartesCapturees.Contains(nomCarte)) return;
            bool visible = (sprites != null && sprites.NbVisibles > 0) || (monstres != null && monstres.NbVisibles > 0);
            if (Time.time - tCarte < 2f || !visible) return;
            cartesCapturees.Add(nomCarte);
            Capturer("auto_" + nomCarte);
        }
    }
}
