@echo off
rem Essai du combat (piste 3) : charge l'emplacement 4 (sur la carte du monde), marche au hasard jusqu'au premier combat, puis te laisse la main dans Unity.
call "%~dp0lancer_pont.bat" --slot 4 --rencontre --preparer btl_pl_d,btl_wd_d %*
