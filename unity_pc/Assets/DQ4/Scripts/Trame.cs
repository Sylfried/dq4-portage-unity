using System;

namespace DQ4
{
    // Messages du pont (dq4_pc/pont.py -> Unity), une ligne JSON chacun. Noms courts = noms des champs JSON.
    [Serializable] public class SprT { public int s; public int id; public int nl; public float[] p; public float[] c; public int pose; public int dir; }
    [Serializable] public class MsgT { public bool v; public int n; public int w; public int h; public string b; }
    [Serializable] public class LigneT { public int r; public string t; public string d; public bool ti; }
    // monstre du combat : maillage dynamique du moteur ; vb/ib/qb (base64) ne sont presents que lorsqu'ils changent
    [Serializable] public class MonT { public int s; public int m; public int sh; public int vi; public float al; public float[] c; public float[] o; public float[] p; public string vb; public string ib; public string qb; }
    [Serializable] public class MenuT { public int p; public bool a; public int cur; public LigneT[] l; }

    [Serializable]
    public class Trame
    {
        // message de bienvenue
        public int hello; public int proto; public string export; public int hz;
        // message d'attente (export long)
        public string attente;
        // une image du moteur
        public int i;               // numero d'image du moteur
        public int partie;          // 0xc ville, 0xd combat, 0xe carte du monde
        public int nc;              // compteur de cartes chargees (change = recharger la carte)
        public string carte;
        public int champ;           // 1 = carte du monde (non geree par Unity pour l'instant)
        public float[] fond;        // couleur de fond
        public float[] cp, cc, cr;  // camera : position, cible, rotation (combat)
        public float fov;           // combat : champ de vision nominal (0 = valeur par defaut, 48)
        public MonT[] mon;          // combat : monstres
        public SprT[] spr;          // sprites de la scene
        public int[] an;            // animations d'objets (paires : indice d'objet, animation)
        public MsgT msg;            // fenetre de message (bitmap du texte, envoye quand il change)
        public MenuT[] menus;       // fenetres de menu visibles
        public string info;
    }
}
