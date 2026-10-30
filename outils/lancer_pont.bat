@echo off
setlocal
cd /d "%~dp0..\dq4_pc"
title Dragon Quest IV - pont vers Unity (piste 3)
echo.
echo  Dragon Quest IV - pont moteur vers Unity (piste 3, essai)
echo  ---------------------------------------------------------
echo  Lit tes APK francais dans jeux\DQ4\patched_apks (dossier du depot, sinon ..\..\jeux\DQ4)
echo  et ta derniere sauvegarde dans jeux\DQ4\saves (tes fichiers ne sont jamais modifies).
echo  Le moteur du jeu tourne ici ; Unity ^(projet unity_pc de ce depot, bouton Play^) affiche.
echo  Laisse cette fenetre ouverte pendant que tu joues dans Unity. Ctrl+C pour arreter.
echo.

rem --- 1. Python : recherche de Python 3.10 a 3.14
set "PY="
for %%V in (3.13 3.12 3.11 3.14 3.10) do (
  if not defined PY (
    py -%%V -c "import sys" >nul 2>nul && set "PY=py -%%V"
  )
)
if not defined PY (
  python -c "import sys" >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo  Python est introuvable. Installe Python 3.12 depuis https://www.python.org/downloads/
  echo  ^(coche "Add python.exe to PATH"^), puis relance ce fichier.
  pause
  exit /b 1
)

rem --- 2. environnement isole et modules : dq4_pc\.venv, cree s'il manque
if not exist ".venv\Scripts\python.exe" (
  echo  Creation de l'environnement Python ^(une seule fois^)...
  %PY% -m venv .venv
  if errorlevel 1 ( echo  Echec de la creation de l'environnement. & pause & exit /b 1 )
)
if not exist ".venv\modules_ok.txt" (
  echo  Installation des modules ^(une seule fois, 1 a 3 minutes^)...
  ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check numpy pillow unicorn pyglet UnityPy
  if errorlevel 1 (
    echo.
    echo  L'installation a echoue. Si ton Python est tres recent, installe Python 3.12 :
    echo     winget install Python.Python.3.12
    echo  puis supprime le dossier "dq4_pc\.venv" et relance ce fichier.
    pause
    exit /b 1
  )
  echo ok> ".venv\modules_ok.txt"
)

rem --- 3. le pont : exporte les cartes de depart la premiere fois (quelques secondes), puis attend Unity
".venv\Scripts\python.exe" pont.py --preparer mak1f1,maout %*
echo.
echo  Fin du pont.
pause
