# Manière de travailler

## Branches
- `main` : toujours lançable. On n'y pousse pas directement.
- Une branche par sujet, créée depuis `main` : `feat/<sujet>`, `fix/<sujet>`, `recherche/<sujet>`, `docs/<sujet>`.
- Pull request vers `main` (fusion « squash »), branche supprimée après fusion.

## Commits
Courts, au présent, préfixés : `feat:`, `fix:`, `docs:`, `refactor:`, `chore:`. Un commit = une idée.

## Issues
Chaque tâche, bug ou piste de recherche a une issue (modèles dans `.github/ISSUE_TEMPLATE`).

## Avant de fusionner
1. Pont et faux client : `python outils/pont_client_test.py` contre `dq4_pc/pont.py` (voir l'en-tête du fichier).
2. Code C# : il ne peut pas être exécuté en cloud (pas d'Éditeur Unity), seulement compilé contre une API factice ; l'essai réel se fait dans Unity chez Sylfried, qui rapporte captures et `journal.txt`.

## Règles du projet
- **Aucun contenu Square Enix** dans le dépôt, qui est **public** (voir `.gitignore`). Les outils lisent les fichiers de l'utilisateur sur son PC.
- Séparé du patch FR : ne jamais toucher à ses fichiers (Excel, `DQ_Patch_FR.exe`, `build_exe.bat`).
- Les sauvegardes de l'utilisateur ne sont jamais modifiées (les nouvelles vont dans `dq4_pc\sauvegardes`).
- Claude gère issues, commits, pushs, pull requests et fusions (squash vers `main`) après vérification en cloud.
- Le moteur (`dq4_pc/`) est une copie de celui du dépôt `dq4-pc` : voir `docs/piste-3-projet-unity.md` pour la resynchronisation.
