@echo off
rem Copie le journal de l'Editeur Unity dans captures\unity\Editor.log (a la racine du depot) pour qu'il puisse etre lu sans copier-coller.
setlocal
set "SRC=%LOCALAPPDATA%\Unity\Editor\Editor.log"
set "DST=%~dp0..\captures\unity"
if not exist "%DST%" mkdir "%DST%"
if not exist "%SRC%" (
  echo  Journal introuvable : %SRC%
  echo  Ouvre d'abord Unity au moins une fois.
  pause
  exit /b 1
)
copy /y "%SRC%" "%DST%\Editor.log" >nul
if errorlevel 1 ( echo  Copie impossible. & pause & exit /b 1 )
echo  Copie faite : %DST%\Editor.log
pause
