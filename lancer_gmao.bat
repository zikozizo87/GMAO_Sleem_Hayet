@echo off
title GMAO - Societe Sleem Hayet (Serveur Local Wi-Fi)
cd /d "%~dp0"

echo [1/3] Verification des bibliotheques...
python -m pip install -r requirements.txt

echo [2/3] Recherche de l'adresse IP du PC sur le Wi-Fi...
for /f "tokens=2 delims=[]" %%a in ('ping -n 1 -4 "%computername%"') do set "LOCAL_IP=%%a"

echo.
echo ========================================================
echo   APPLICATION GMAO PRETE SUR LE RESEAU LOCAL !
echo ========================================================
echo   Ouvrez ce lien sur le navigateur de votre telephone :
echo   http://%LOCAL_IP%:8501
echo ========================================================
echo.

echo [3/3] Demarrage du serveur Streamlit...
python -m streamlit run app.py --server.address 0.0.0.0 --server.port 8501

pause
