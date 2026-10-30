# Piste 3 : Unity comme afficheur (essai)

Le moteur du jeu (`libdq4core.so`) reste émulé en Python (`dq4_pc/pont.py`) ; **Unity ne fait que l'affichage** : caméra, décor, personnages, fenêtres, en 16/9 natif (le Canvas Scaler s'adapte à n'importe quelle proportion). Aucun contenu du jeu n'est dans ce dossier : le pont exporte tes cartes depuis tes APK dans `dq4_pc\cache\unity` (ignoré par git).

## Il te faut
- Unity Hub + Éditeur **2022.3.62f3** (module Windows suffit, licence Personal).
- Tes fichiers du jeu (APK français dans `jeux\DQ4\patched_apks`, une sauvegarde dans `jeux\DQ4\saves`).

## Lancer (deux fenêtres, dans l'ordre que tu veux)
1. **Le pont** : double-clic sur `outils\lancer_pont.bat`. La première fois il prépare Python (1 à 3 min) et exporte l'église et le village (quelques secondes), puis affiche « Pont prêt ». Laisse la fenêtre ouverte.
2. **Unity** : Unity Hub → *Ajouter* → *Ajouter un projet à partir du disque* → dossier `unity_pc` (dans ce dépôt) (version 2022.3.62f3). La première ouverture importe le projet (2 à 5 min).
3. Dans l'Éditeur : menu **DQ4 → Lancer le jeu (scène vide + Play)**. (Autre façon : ouvrir une scène vide et appuyer sur Play, le jeu se construit tout seul.)
4. **Clique dans la vue Game** (sinon le clavier ne répond pas) et laisse cet onglet visible. Pour juger le 16/9 : menu déroulant de la vue Game → *16:9 Aspect* (ou *Free Aspect* et agrandis la fenêtre).

Le pont attend Unity, et Unity attend le pont : l'ordre n'a pas d'importance, et on peut relancer Play sans relancer le pont.

## Essai du combat
Double-clic sur **`outils\lancer_pont_combat.bat`** à la place de `lancer_pont.bat` (puis Play dans Unity comme ci-dessus). Il charge l'emplacement 4 (sur la carte du monde, que Unity n'affiche pas encore : écran noir et un message), **marche tout seul** au hasard jusqu'à la première rencontre, puis te rend la main : arène (décor ×6), monstres, caméra de combat, fenêtres de commandes (texte simple). Une capture automatique `auto_btl_*.png` est prise 2 s après le début.

## Touches
Flèches ou ZQSD : marcher / déplacer le curseur · Entrée, Espace ou E : valider, parler, avancer un message · Retour arrière ou X : menu de commandes, annuler · **F6** : recentrage 16/9 de la caméra oui/non (le même que la visionneuse) · **F12** : capture d'écran.

## Ce qu'on doit voir
L'église (`mak1f1`), le héros, le message d'accueil en bas puis la liste de l'église (fenêtres simplifiées, texte seul). Choisir « Rien », marcher vers le bas : la porte s'ouvre, le village (`maout`) se charge. La carte du monde n'est pas encore gérée par Unity (la visionneuse pyglet, elle, la gère) ; le combat s'essaie avec `lancer_pont_combat.bat` (voir plus haut).

## Ce que tu me renvoies
Dis-moi ce que tu vois (ou pas). Je lis moi-même, dans `captures\unity\` : `journal.txt` (la console d'Unity recopiée : erreurs, avertissements, temps de chargement) et `auto_*.png` (une capture prise automatiquement par carte). Pas besoin de copier-coller. Menu **DQ4 → Ouvrir le dossier des captures** pour y aller.

## Si ça ne démarre pas
- **Des erreurs rouges dans la Console à l'ouverture** (le bouton Play est refusé) : lance `outils\copier_log_unity.bat` ; il copie le journal de l'Éditeur dans `captures\unity\Editor.log`. Dis-moi simplement « erreurs de compilation ».
- **Le Hub refuse le dossier** : crée un projet *3D (Built-in Render Pipeline)* en 2022.3.62f3 ailleurs, puis copie dedans le dossier `unity_pc\Assets\DQ4`.
- **« en attente du pont » reste affiché** : la fenêtre du pont est-elle ouverte et prête ? Le port par défaut est 7740 ; pour en changer, `lancer_pont.bat --port 7750` et la variable d'environnement `DQ4_PORT=7750` avant d'ouvrir Unity.
- **Tout est rose ou noir** : regarde `journal.txt` (le shader maison `DQ4/Unlit` est dans `Assets/DQ4/Shaders`).

## Où est quoi
- `dq4_pc/pont.py` (serveur : moteur + socket 127.0.0.1) et `dq4_pc/pont_export.py` (cartes et sprites exportés pour Unity) ; protocole en tête de `pont.py`.
- `outils/pont_client_test.py` : faux client Unity, pour tester le pont sans Unity.
- `Assets/DQ4/Scripts` : `DQ4Jeu` (boucle, caméra, clavier, captures), `PontClient` (réseau), `CarteDQ4` (décor et portes), `SpritesDQ4`, `MonstresDQ4` (combat), `InterfaceDQ4` (uGUI), `JournalDQ4`, `Trame` (format des messages). `Assets/DQ4/Shaders/DQ4Unlit.shader`.
