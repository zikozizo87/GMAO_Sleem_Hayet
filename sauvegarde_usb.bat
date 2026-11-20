@echo off
chcp 65001 >nul
set "SOURCE=C:\Users\sms-t\Videos\GMAO_Sleem_Hayet"

REM Remplacez E: par la lettre de votre cle USB si necessaire
set "DEST_USB=E:\Sauvegarde_GMAO_Sleem_Hayet"

if not exist "E:\" (
    echo [ERREUR] Cle USB non detectee sur E:\. Branchez la cle puis reessayez.
    pause
    exit /b 1
)

if not exist "%DEST_USB%" mkdir "%DEST_USB%"

echo Sauvegarde en cours vers %DEST_USB% ...

REM 1. Copie des fichiers JSON locaux
robocopy "%SOURCE%" "%DEST_USB%" gmao_local_data.json gmao_stock_data.json gmao_stock_mouvements.json /R:2 /W:3 /NFL /NDL /NJH /NJS

REM 2. Copie des dossiers d'images et signatures
robocopy "%SOURCE%\uploads_pieces" "%DEST_USB%\uploads_pieces" /E /R:2 /W:3 /NFL /NDL /NJH /NJS
robocopy "%SOURCE%\uploads_signatures" "%DEST_USB%\uploads_signatures" /E /R:2 /W:3 /NFL /NDL /NJH /NJS

echo.
echo [OK] Sauvegarde terminee le %date% a %time%.
timeout /t 4 >nul