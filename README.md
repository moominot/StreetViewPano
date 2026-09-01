# StreetViewPano

Aplicació d'escriptori (GUI en Python/Tkinter) per descarregar panorames equirectangulars d'alta resolució de Google Street View a partir d'una adreça, unes coordenades o un enllaç de Google Maps — i extreure'n vistes planes orientades a mida, pensades per fer-les servir com a context o Sky Texture en renders arquitectònics (Enscape, etc.).

## Característiques

- **Cerca per adreça, coordenades o enllaç**: accepta una adreça de text (geocodificada amb Nominatim/OpenStreetMap), unes coordenades `lat,lng`, o un enllaç de Google Maps (curt o complet) — en aquest darrer cas extreu el `panoid` directament del redirect, sense necessitat de geocodificar.
- **Previsualització abans de descarregar**: primer baixa només una tessel·la de baixa resolució (zoom 0) perquè puguis confirmar que és el punt correcte abans de llançar la descàrrega definitiva.
- **Resolució seleccionable**: de 512×512 fins a 16384×8192, segons disponibilitat del panorama origen.
- **Extractor de vista plana**: un cop descarregat el panorama, permet triar orientació (*heading*), inclinació (*pitch*) i camp de visió (*fov*) amb lliscadors interactius, i n'extreu una imatge rectilínia normal (reprojecció equirectangular → perspectiva amb `numpy`), amb previsualització en directe.
- **Sense clau d'API de pagament**: la geocodificació fa servir Nominatim (OpenStreetMap), gratuït i sense registre.

## Requisits

- Python 3.9+
- Llibreries: `requests`, `pillow`, `numpy` (i `tkinter`, inclòs de sèrie amb la majoria d'instal·lacions de Python a Windows/macOS; a Linux pot caldre `sudo apt install python3-tk`)

```bash
pip install requests pillow numpy
```

## Ús

```bash
python panoStreetViewGUI.py
```

Al camp principal, introdueix qualsevol d'aquestes tres opcions:

- Una adreça: `Carrer Eclipsi 12, Porto`
- Unes coordenades: `49.6511, 5.9241`
- Un enllaç de Google Maps: `https://maps.app.goo.gl/...`

Prem **"Cercar i previsualitzar"**. Si el punt és el correcte, prem **"Descarrega resolució desitjada"** per baixar-ne la resolució definitiva. Un cop descarregat, mou els lliscadors de la secció inferior per extreure'n vistes planes i desa-les amb **"Desa vista com a..."**.

## Generar un executable (Windows)

El `.exe` no s'inclou al repositori (pesa desenes de MB un cop empaquetat amb `numpy`/`pillow`/`tkinter`). Es pot generar localment amb [PyInstaller](https://pyinstaller.org/):

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --name "StreetViewPano" panoStreetViewGUI.py
```

O bé, executant el script inclòs `build_exe.bat` (Windows), que instal·la les dependències i fa l'empaquetat automàticament. El resultat queda a `dist\StreetViewPano.exe` i no requereix Python instal·lat al PC on s'executi.

## Notes i avisos

- La cerca del panorama (`panoid`) i la descàrrega de les tessel·les fan servir endpoints interns de Google (no l'API oficial i de pagament), pensats per a ús puntual/personal. No estan documentats ni coberts pels Termes de Servei de Google, i poden deixar de funcionar sense avís si Google en canvia el format.
- La geocodificació amb Nominatim està subjecta a la seva [política d'ús](https://operations.osmfoundation.org/policies/nominatim/) (límit d'una petició per segon, ús no massiu).
- Projecte d'ús personal — sense afiliació amb Google ni amb OpenStreetMap.
