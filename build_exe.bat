@echo off
REM Genera un .exe autocontingut de panoStreetViewGUI.py (no cal Python al PC destí)
REM Cal executar aquest .bat a la mateixa carpeta on tens panoStreetViewGUI.py

echo Instal·lant/actualitzant dependencies...
python -m pip install --upgrade pyinstaller requests pillow numpy

echo.
echo Generant l'executable...
python -m PyInstaller --onefile --windowed --name "StreetViewPano" panoStreetViewGUI.py

echo.
echo Fet! Troba l'executable a la carpeta "dist\StreetViewPano.exe"
pause
