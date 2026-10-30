# Dragon Quest IV : portage PC, version Unity (essai)

Portage sur PC de **Dragon Quest IV (Android)**, à partir de la version déjà patchée en français. Ici, **Unity sert uniquement d'afficheur** : le moteur du jeu tourne tel quel, émulé en Python, et envoie à Unity l'état de l'écran (caméra, décor, personnages, monstres, fenêtres) ; Unity redessine le tout en **16/9 natif**.

## Principe
Le jeu Android est un moteur C++ autonome (`libdq4core.so`, ARM64) piloté par une couche Unity. On garde le moteur et on réécrit la couche d'affichage :

```
APK du jeu (les tiens) --> dq4_pc/emu_core.py (Unicorn exécute libdq4core.so)
                           dq4_pc/moteur.py   (pilotage, décodage des paquets moteur -> Unity)
                           dq4_pc/pont.py     (serveur TCP 127.0.0.1:7740, une ligne JSON par image à 30 Hz)
                                  |  <-- touches : "K <masque>"
                           unity_pc/          (projet Unity 2022.3.62f3 : cartes, sprites, monstres, fenêtres)
```

- `dq4_pc/` : `emu_core.py`, `moteur.py`, `pktlog.py` (moteur) · `donnees.py`, `fld.py` (lecture des données Unity du jeu) · `pont.py`, `pont_export.py` (pont vers Unity, export des cartes en `geo.bin` + PNG).
- `unity_pc/` : le projet Unity (scripts C#, shader `DQ4/Unlit`). Notice : [`unity_pc/LISEZMOI.md`](unity_pc/LISEZMOI.md).
- `outils/` : `lancer_pont.bat` (pont), `lancer_pont_combat.bat` (essai d'un combat), `pont_client_test.py` (faux client Unity pour tester sans Éditeur), `copier_log_unity.bat`.
- `docs/` : [`piste-3-projet-unity.md`](docs/piste-3-projet-unity.md) (état, résultats des essais, reste à faire), [`notes-technique-portage-dq4.md`](docs/notes-technique-portage-dq4.md) (architecture, protocole, combat, données), [`workflow.md`](docs/workflow.md).

La version **native sans Unity** (visionneuse pyglet/OpenGL) reste dans le dépôt `dq4-pc`.

## ⚠️ Aucun contenu Square Enix ici
Ce dépôt est **public** et ne contient **que du code et des notes**. Le programme lit *tes* fichiers (APK patchés, sauvegardes) sur *ton* PC ; rien n'est copié ni distribué. Les APK, `.so`, données Unity, sauvegardes, caches, captures et fichiers exportés sont exclus par `.gitignore` : ne jamais les forcer (`git add -f`). Ce dépôt est séparé du patch FR.

## Lancer (Windows)
Disposition attendue (les `.bat` et `pont.py` cherchent les jeux en `..\..\jeux\DQ4` depuis `dq4_pc\`) :

```
Projet DQ\
  jeux\DQ4\patched_apks\   (base.apk, split_config.arm64_v8a.apk, split_UnityDataAssetPack.apk)
  jeux\DQ4\saves\<horodatage>\data1.dat
  dq4-portage-unity\       <- ce dépôt
```

1. Double-clic sur `outils\lancer_pont.bat` (la première fois : environnement Python local `dq4_pc\.venv`, 1 à 3 minutes ; Python 3.10 à 3.14).
2. Unity Hub → *Ajouter un projet à partir du disque* → `unity_pc` (Éditeur **2022.3.62f3**), menu **DQ4 → Lancer le jeu**.
3. Pour tester un combat : `outils\lancer_pont_combat.bat`.

Touches, résultats des essais et limites connues : voir `unity_pc/LISEZMOI.md` et `docs/piste-3-projet-unity.md`.
