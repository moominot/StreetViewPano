#!/usr/bin/env python3
"""
Street View Panorama Downloader — GUI (sense clau API)
Descarrega panorames equirectangulars de Google Street View a partir
d'una adreça, i permet extreure'n vistes planes (rectilínies) orientades
com vulguis, per fer-les servir com a context/textura (p. ex. a Enscape).

Geocodificació: Nominatim (OpenStreetMap) — gratuïta, sense clau.
Cerca de panorama i tessel·les: endpoints interns de Google (sense clau).

Requereix: requests, pillow, numpy
    pip install requests pillow numpy
"""

import json
import os
import re
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from io import BytesIO

import numpy as np
import requests
from PIL import Image, ImageTk

CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".panoStreetView_config.json")

NOMINATIM_HEADERS = {
    "User-Agent": "panoStreetViewGUI/1.0 (ús personal, arquitectura)"
}

STREETVIEW_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://www.google.com/maps/",
}

ZOOM_INFO = {
    0: "512×512 (mínima)",
    1: "1024×512",
    2: "2048×1024",
    3: "4096×2048",
    4: "8192×4096 (recomanat)",
    5: "16384×8192 (no sempre disponible)",
}


# ---------------------------------------------------------------------------
# Lògica: geocodificació, cerca de panorama, descàrrega
# ---------------------------------------------------------------------------

# Biaix cap a les Illes Balears (lon_min,lat_max,lon_max,lat_min) — no restringeix,
# només prioritza aquesta zona si el terme de cerca és ambigu.
VIEWBOX_BALEARS = "1.15,40.15,4.40,38.60"


def geocodifica_adreca_multi(adreca):
    """Retorna una llista de candidats (dict amb lat/lon/display_name), sense triar-ne cap."""
    url = "https://nominatim.openstreetmap.org/search"
    params = {
        "q": adreca,
        "format": "json",
        "limit": 8,
        "addressdetails": 1,
        "countrycodes": "es",
        "viewbox": VIEWBOX_BALEARS,
        "bounded": 0,
    }
    r = requests.get(url, params=params, headers=NOMINATIM_HEADERS, timeout=15)
    resultats = r.json()
    if not resultats:
        raise RuntimeError(
            "No s'ha trobat cap resultat per aquesta adreça. Prova a simplificar-la "
            "(p. ex. sense el número, o afegint el municipi)."
        )
    return resultats


def panoid_de_coordenades(lat, lng):
    url = "https://maps.googleapis.com/maps/api/js/GeoPhotoService.SingleImageSearch"
    params = {
        "pb": f"!1m5!1sapiv3!5sUS!11m2!1m1!1b0!2m4!1m2!3d{lat}!4d{lng}!2d50!3m10"
              "!2m2!1sen!2sUS!9m1!1e2!11m4!1m3!1e2!2b1!3e2!4m10!1e1!1e2!1e3!1e4"
              "!1e8!1e6!5m1!1e2!6m1!1e2",
        "callback": "_xdc_",
    }
    r = requests.get(url, params=params, headers=STREETVIEW_HEADERS, timeout=15)
    match = re.search(r'"([a-zA-Z0-9_-]{22})"', r.text)
    if not match:
        raise RuntimeError("No s'ha trobat cap panorama Street View en aquest punt.")
    return match.group(1)


def extreu_info_de_url_maps(url):
    """
    Segueix un enllaç de Google Maps (curt tipus maps.app.goo.gl/... o complet)
    fins a la URL final i n'extreu el panoid i, si hi és, les coordenades.
    Google incrusta el panoid directament al redirect (!1s...!2e0), sense
    necessitat de JavaScript, així que un simple GET amb redirects ja funciona.
    """
    r = requests.get(url, headers=STREETVIEW_HEADERS, allow_redirects=True, timeout=15)
    final_url = r.url

    pano_match = re.search(r"!1s([a-zA-Z0-9_-]{20,30})!2e", final_url)
    if not pano_match:
        pano_match = re.search(r"panoid%3D([a-zA-Z0-9_-]{20,30})", final_url)
    if not pano_match:
        pano_match = re.search(r"[?&]panoid=([a-zA-Z0-9_-]{20,30})", final_url)
    pano_id = pano_match.group(1) if pano_match else None

    coord_match = re.search(r"@(-?\d+\.\d+),(-?\d+\.\d+)", final_url)
    lat = float(coord_match.group(1)) if coord_match else None
    lng = float(coord_match.group(2)) if coord_match else None

    return pano_id, lat, lng


def descarrega_panorama(pano_id, zoom, progress_cb=None):
    """Descarrega totes les tessel·les d'un zoom donat i retorna una imatge PIL."""
    base = "https://streetviewpixels-pa.googleapis.com/v1/tile"
    cols = 2 ** zoom
    rows = 2 ** (zoom - 1) if zoom > 0 else 1
    tile_size = 512
    total = cols * rows
    fet = 0

    panorama = Image.new("RGB", (cols * tile_size, rows * tile_size))

    for y in range(rows):
        for x in range(cols):
            params = {
                "cb_client": "maps_sv.tactile",
                "panoid": pano_id,
                "x": x, "y": y, "zoom": zoom,
                "nbt": 1, "fover": 2,
            }
            r = requests.get(base, params=params, headers=STREETVIEW_HEADERS, timeout=15)
            if r.status_code == 200:
                tile = Image.open(BytesIO(r.content))
                panorama.paste(tile, (x * tile_size, y * tile_size))
            fet += 1
            if progress_cb:
                progress_cb(fet, total)

    return panorama


# ---------------------------------------------------------------------------
# Extracció de vista plana (equirectangular -> perspectiva)
# ---------------------------------------------------------------------------

def extreu_vista_plana(panorama_img, heading, pitch, fov, out_w=1024, out_h=768):
    """
    Extreu una imatge rectilínia (com una foto normal) del panorama equirectangular,
    donada una orientació (heading, en graus, 0=nord/frontal), una inclinació
    (pitch, graus, + amunt) i un camp de visió (fov, graus, similar a una focal).
    """
    equi = np.asarray(panorama_img)
    h, w = equi.shape[:2]

    fov_rad = np.radians(fov)
    aspect = out_w / out_h

    x = np.linspace(-1, 1, out_w)
    y = np.linspace(1, -1, out_h)
    xv, yv = np.meshgrid(x, y)

    z = 1.0 / np.tan(fov_rad / 2)
    dx, dy, dz = xv * aspect, yv, np.full_like(xv, z)
    norm = np.sqrt(dx ** 2 + dy ** 2 + dz ** 2)
    dx, dy, dz = dx / norm, dy / norm, dz / norm

    pitch_rad = np.radians(pitch)
    dy2 = dy * np.cos(pitch_rad) - dz * np.sin(pitch_rad)
    dz2 = dy * np.sin(pitch_rad) + dz * np.cos(pitch_rad)
    dx2 = dx

    yaw_rad = np.radians(heading)
    dx3 = dx2 * np.cos(yaw_rad) + dz2 * np.sin(yaw_rad)
    dz3 = -dx2 * np.sin(yaw_rad) + dz2 * np.cos(yaw_rad)
    dy3 = dy2

    theta = np.arctan2(dx3, dz3)
    phi = np.arcsin(np.clip(dy3, -1, 1))

    # u=0 (vora esquerra) correspon a heading=0° al mosaic original de
    # Street View — NO el centre. Per això aquí NO s'afegeix +0.5 (np.mod
    # ja s'encarrega d'embolicar els valors negatius de theta cap a [0,1)).
    u = (np.mod(theta / (2 * np.pi), 1.0) * w).astype(np.int32)
    v = ((0.5 - phi / np.pi) * h).astype(np.int32)
    u = np.clip(u, 0, w - 1)
    v = np.clip(v, 0, h - 1)

    return Image.fromarray(equi[v, u])


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

class PanoApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Street View → Panorama i vistes planes (per a Enscape)")
        self.geometry("1200x820")
        self.minsize(880, 650)
        self.resizable(True, True)

        self.adreca_var = tk.StringVar()
        self.zoom_var = tk.IntVar(value=4)
        self.carpeta_var = tk.StringVar(value=os.getcwd())

        self.heading_var = tk.DoubleVar(value=0)
        self.pitch_var = tk.DoubleVar(value=0)
        self.fov_var = tk.DoubleVar(value=90)

        self.pano_id = None
        self.panorama_complet = None   # PIL Image d'alta resolució, un cop descarregat
        self.preview_img = None
        self.vista_img = None
        self.vista_extreta = None      # última imatge plana extreta (PIL), per desar
        self._preview_source = None    # imatge completa mostrada al panorama (per reescalar en redimensionar)
        self._vista_source = None      # imatge completa mostrada a la vista plana (per reescalar en redimensionar)
        self._extracting = False
        self._debounce_id = None
        self._debounce_preview_id = None
        self._debounce_vista_resize_id = None
        self._resolucio_definitiva = False  # True un cop s'ha baixat el zoom definitiu (no la previsualització)

        self._carrega_config()
        self._build_ui()

    # -- UI -----------------------------------------------------------------

    def _build_ui(self):
        pad = {"padx": 8, "pady": 6}

        # -- Partició redimensionable: columna estreta (controls) | columna ampla (imatges) --
        self.paned = ttk.PanedWindow(self, orient="horizontal")
        self.paned.pack(fill="both", expand=True, padx=8, pady=8)

        # Amplada mínima explícita: sense això, la columna esquerra pot quedar
        # esquitxada a zero abans que el PanedWindow calculi les mides finals.
        col_esquerra = ttk.Frame(self.paned, width=320)
        col_esquerra.pack_propagate(False)
        col_dreta = ttk.Frame(self.paned)
        self.paned.add(col_esquerra, weight=0)
        self.paned.add(col_dreta, weight=1)

        # ===================== COLUMNA ESQUERRA: controls =====================

        # -- Adreça i descàrrega --
        frm_adr = ttk.LabelFrame(col_esquerra, text="Adreça, coordenades o enllaç")
        frm_adr.pack(fill="x", **pad)
        entry_adr = ttk.Entry(frm_adr, textvariable=self.adreca_var)
        entry_adr.pack(fill="x", padx=8, pady=(8, 0))
        entry_adr.bind("<Return>", lambda e: self._iniciar_descarrega())
        entry_adr.focus_set()
        ttk.Label(
            frm_adr,
            text="p. ex. «Carrer Lluna 14», «39.6511, 2.7241» o un enllaç de Maps",
            foreground="#777", wraplength=280,
        ).pack(anchor="w", padx=8, pady=(2, 8))

        frm_opts = ttk.LabelFrame(col_esquerra, text="Opcions")
        frm_opts.pack(fill="x", **pad)

        ttk.Label(frm_opts, text="Resolució definitiva:").pack(anchor="w", padx=8, pady=(8, 0))
        zoom_combo = ttk.Combobox(
            frm_opts, state="readonly",
            values=[f"{z} — {ZOOM_INFO[z]}" for z in ZOOM_INFO],
        )
        zoom_combo.current(4)
        zoom_combo.pack(fill="x", padx=8, pady=(2, 8))
        zoom_combo.bind("<<ComboboxSelected>>",
                         lambda e: self.zoom_var.set(int(zoom_combo.get().split(" ")[0])))

        ttk.Label(frm_opts, text="Carpeta de sortida:").pack(anchor="w", padx=8)
        frm_carpeta = ttk.Frame(frm_opts)
        frm_carpeta.pack(fill="x", padx=8, pady=(2, 8))
        ttk.Entry(frm_carpeta, textvariable=self.carpeta_var).pack(
            side="left", fill="x", expand=True)
        ttk.Button(frm_carpeta, text="Tria...", command=self._tria_carpeta).pack(
            side="left", padx=(4, 0))

        frm_botons = ttk.Frame(col_esquerra)
        frm_botons.pack(fill="x", **pad)
        self.btn_descarrega = ttk.Button(
            frm_botons, text="Cercar i previsualitzar", command=self._iniciar_descarrega)
        self.btn_descarrega.pack(fill="x", pady=(0, 4))

        self.btn_desc_definitiva = ttk.Button(
            frm_botons, text="Descarrega resolució desitjada",
            command=self._iniciar_descarrega_definitiva, state="disabled")
        self.btn_desc_definitiva.pack(fill="x", pady=(0, 4))

        self.btn_carrega_img = ttk.Button(
            frm_botons, text="Carrega imatge de panorama...", command=self._carrega_imatge)
        self.btn_carrega_img.pack(fill="x")

        self.progress = ttk.Progressbar(col_esquerra, mode="determinate")
        self.progress.pack(fill="x", padx=8, pady=(8, 4))

        self.log_var = tk.StringVar(value="Llest.")
        ttk.Label(col_esquerra, textvariable=self.log_var, foreground="#444",
                  wraplength=280, justify="left").pack(anchor="w", padx=10, pady=(0, 8))

        ttk.Separator(col_esquerra, orient="horizontal").pack(fill="x", padx=8, pady=4)

        # -- Extractor de vista plana --
        frm_vista = ttk.LabelFrame(col_esquerra, text="Vista plana (s'actualitza en moure)")
        frm_vista.pack(fill="x", **pad)
        frm_vista.columnconfigure(0, weight=1)

        self._crea_slider(frm_vista, "Heading (0-360°):", self.heading_var, 0, 360, 0)
        self._crea_slider(frm_vista, "Pitch (-90 a 90°):", self.pitch_var, -90, 90, 1)
        self._crea_slider(frm_vista, "FOV (30-120°):", self.fov_var, 30, 120, 2)

        frm_btn_vista = ttk.Frame(frm_vista)
        frm_btn_vista.grid(row=6, column=0, columnspan=2, pady=8, sticky="ew")
        self.btn_extreu = ttk.Button(frm_btn_vista, text="Actualitza vista",
                                      command=lambda: self._extreu_vista(auto=False), state="disabled")
        self.btn_extreu.pack(fill="x", pady=(0, 4))
        self.btn_desa_vista = ttk.Button(frm_btn_vista, text="Desa vista com a...",
                                          command=self._desa_vista, state="disabled")
        self.btn_desa_vista.pack(fill="x")

        # ===================== COLUMNA DRETA: imatges =====================
        # PanedWindow vertical: separació arrossegable entre les dues imatges.

        paned_dreta = ttk.PanedWindow(col_dreta, orient="vertical")
        paned_dreta.pack(fill="both", expand=True)

        frm_preview = ttk.LabelFrame(paned_dreta, text="Previsualització del panorama")
        self.lbl_preview = ttk.Label(frm_preview, text="(encara no hi ha imatge)", anchor="center")
        self.lbl_preview.pack(fill="both", expand=True, padx=8, pady=8)
        self.lbl_preview.bind("<Configure>", self._on_preview_configure)
        paned_dreta.add(frm_preview, weight=1)

        frm_vista_img = ttk.LabelFrame(paned_dreta, text="Vista plana extreta")
        self.lbl_vista = ttk.Label(frm_vista_img, text="(encara no hi ha vista extreta)", anchor="center")
        self.lbl_vista.pack(fill="both", expand=True, padx=8, pady=8)
        self.lbl_vista.bind("<Configure>", self._on_vista_configure)
        paned_dreta.add(frm_vista_img, weight=1)

        # Reparteix l'espai vertical a mitges un cop la finestra té la mida real
        # (evita que una de les dues quedi esquitxada al primer dibuixat).
        def _centra_sash_dreta():
            paned_dreta.update_idletasks()
            alt = paned_dreta.winfo_height()
            if alt > 20:
                paned_dreta.sashpos(0, alt // 2)
        self.after_idle(_centra_sash_dreta)

    def _crea_slider(self, parent, etiqueta, var, mín, màx, fila):
        fila_label = fila * 2
        fila_escala = fila_label + 1
        ttk.Label(parent, text=etiqueta, anchor="w").grid(
            row=fila_label, column=0, columnspan=2, sticky="w", padx=8, pady=(6, 0))
        escala = ttk.Scale(parent, from_=mín, to=màx, variable=var, orient="horizontal",
                            command=lambda v: self._on_slider_change())
        escala.grid(row=fila_escala, column=0, sticky="ew", padx=8)
        valor_lbl = ttk.Label(parent, width=5)
        valor_lbl.grid(row=fila_escala, column=1, sticky="w", padx=(0, 8))

        def actualitza(*_):
            valor_lbl.config(text=f"{var.get():.0f}")
        var.trace_add("write", actualitza)
        actualitza()

    def _tria_carpeta(self):
        carpeta = filedialog.askdirectory(initialdir=self.carpeta_var.get())
        if carpeta:
            self.carpeta_var.set(carpeta)

    # -- persistència de la darrera carpeta usada ------------------------------

    def _carrega_config(self):
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("carpeta") and os.path.isdir(data["carpeta"]):
                    self.carpeta_var.set(data["carpeta"])
            except Exception:
                pass

    def _desa_config(self):
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump({"carpeta": self.carpeta_var.get()}, f)
        except Exception:
            pass

    # -- flux principal: geocodifica -> preview zoom 0 -> descàrrega definitiva --

    def _carrega_imatge(self):
        """Carrega un panorama ja descarregat prèviament (p. ex. pel userscript del navegador)."""
        ruta = filedialog.askopenfilename(
            initialdir=self.carpeta_var.get(),
            title="Selecciona una imatge de panorama",
            filetypes=[
                ("Imatges", "*.jpg *.jpeg *.png *.webp"),
                ("Tots els fitxers", "*.*"),
            ],
        )
        if not ruta:
            return
        try:
            img = Image.open(ruta).convert("RGB")
        except Exception as e:
            messagebox.showerror("Error carregant la imatge", str(e))
            return

        self.panorama_complet = img
        # Deduïm un panoid "fictici" a partir del nom de fitxer, només per als noms de sortida
        self.pano_id = os.path.splitext(os.path.basename(ruta))[0]
        self._mostra_preview(img, f"carregada des de {os.path.basename(ruta)}")
        self._log(f"Imatge carregada: {os.path.basename(ruta)} ({img.width}×{img.height})")
        self.btn_extreu.config(state="normal")

    def _iniciar_descarrega(self):
        adreca = self.adreca_var.get().strip()
        if not adreca:
            messagebox.showwarning("Falta l'adreça", "Introdueix una adreça.")
            return

        self._desa_config()
        self.btn_descarrega.config(state="disabled")
        self.btn_desc_definitiva.config(state="disabled")
        self.btn_extreu.config(state="disabled")
        self.btn_desa_vista.config(state="disabled")
        self.progress.config(value=0, maximum=100)

        # Si és un enllaç de Google Maps, en seguim els redirects i n'extraiem el panoid.
        if re.match(r"^https?://\S+$", adreca):
            self._log("Enllaç detectat — resolent...")
            fil = threading.Thread(target=self._fase1_url, args=(adreca,), daemon=True)
            fil.start()
            return

        # Si el text té forma de coordenades "lat,lng", ens saltem la geocodificació.
        match_coords = re.match(
            r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$", adreca
        )
        if match_coords:
            lat, lng = float(match_coords.group(1)), float(match_coords.group(2))
            self._log(f"Coordenades detectades: {lat}, {lng}")
            fil = threading.Thread(target=self._proces_previsualitza, args=(lat, lng), daemon=True)
            fil.start()
            return

        self._log("Geocodificant adreça...")
        fil = threading.Thread(target=self._fase1_geocodifica, args=(adreca,), daemon=True)
        fil.start()

    def _fase1_url(self, url):
        """Segueix l'enllaç de Google Maps i n'extreu el panoid directament, sense
        haver de geocodificar ni cercar el panorama més proper."""
        try:
            pano_id, lat, lng = extreu_info_de_url_maps(url)
        except Exception as e:
            self._log(f"Error: {e}")
            self.after(0, lambda: messagebox.showerror("Error", str(e)))
            self.after(0, lambda: self.btn_descarrega.config(state="normal"))
            return

        if not pano_id:
            msg = ("No s'ha trobat cap panoid en aquest enllaç. Prova d'obrir-lo a Google "
                   "Maps amb la vista de Street View activa i copiar la URL de la barra "
                   "d'adreces en aquell moment.")
            self._log(msg)
            self.after(0, lambda: messagebox.showwarning("Panoid no trobat", msg))
            self.after(0, lambda: self.btn_descarrega.config(state="normal"))
            return

        self._log(f"Panoid extret de l'enllaç: {pano_id}")
        self._proces_previsualitza(lat, lng, pano_id=pano_id)

    def _fase1_geocodifica(self, adreca):
        """Cerca candidats a Nominatim; si n'hi ha més d'un, demana a l'usuari quin és."""
        try:
            candidats = geocodifica_adreca_multi(adreca)
        except Exception as e:
            self._log(f"Error: {e}")
            self.after(0, lambda: messagebox.showerror("Error", str(e)))
            self.after(0, lambda: self.btn_descarrega.config(state="normal"))
            return

        self._log(f"{len(candidats)} candidat(s) trobat(s).")
        self.after(0, lambda: self._mostra_selector(candidats))

    def _mostra_selector(self, candidats):
        if len(candidats) == 1:
            self._confirma_candidat(candidats[0])
            return

        top = tk.Toplevel(self)
        top.title("Quina d'aquestes és l'adreça correcta?")
        top.geometry("560x320")
        top.transient(self)
        top.grab_set()

        ttk.Label(
            top, text="Nominatim ha trobat diversos resultats. Selecciona el correcte:",
            wraplength=540,
        ).pack(padx=10, pady=(10, 4), anchor="w")

        llista = tk.Listbox(top, width=90, height=10)
        llista.pack(fill="both", expand=True, padx=10, pady=6)
        for c in candidats:
            tipus = c.get("type", "")
            llista.insert("end", f"{c.get('display_name', '?')}  [{tipus}]")
        llista.selection_set(0)
        llista.bind("<Double-Button-1>", lambda e: confirma())

        def confirma():
            sel = llista.curselection()
            candidat = candidats[sel[0]] if sel else candidats[0]
            top.destroy()
            self._confirma_candidat(candidat)

        def cancella():
            top.destroy()
            self._log("Cancel·lat per l'usuari.")
            self.btn_descarrega.config(state="normal")

        frm_btn = ttk.Frame(top)
        frm_btn.pack(pady=(0, 10))
        ttk.Button(frm_btn, text="Fes servir aquesta", command=confirma).pack(side="left", padx=4)
        ttk.Button(frm_btn, text="Cancel·la", command=cancella).pack(side="left", padx=4)

        top.protocol("WM_DELETE_WINDOW", cancella)

    def _confirma_candidat(self, candidat):
        lat, lng = float(candidat["lat"]), float(candidat["lon"])
        self._log(f"Adreça seleccionada: {candidat.get('display_name', '')}")
        fil = threading.Thread(target=self._proces_previsualitza, args=(lat, lng), daemon=True)
        fil.start()

    def _proces_previsualitza(self, lat, lng, pano_id=None):
        """Fase 1: (si cal) cerca el panorama més proper, i en baixa la previsualització (zoom 0).
        Si ja es coneix el pano_id (p. ex. extret d'un enllaç), s'estalvia la cerca."""
        try:
            if pano_id:
                self._log(f"Panoid conegut: {pano_id} — descarregant previsualització ràpida...")
            else:
                self._log("Cercant panorama més proper...")
                pano_id = panoid_de_coordenades(lat, lng)
                self._log(f"Panoid: {pano_id} — descarregant previsualització ràpida...")

            self.pano_id = pano_id
            preview = descarrega_panorama(pano_id, zoom=0)
            self.panorama_complet = preview  # de moment, la previsualització fa de "complet"
            self._resolucio_definitiva = False
            self.after(0, lambda: self._mostra_preview(preview, "previsualització ràpida (zoom 0)"))
            self._log("Previsualització llesta. Si és el punt correcte, prem "
                       "«Descarrega resolució desitjada».")
            self.after(0, lambda: self.btn_desc_definitiva.config(state="normal"))
            self.after(0, lambda: self.btn_extreu.config(state="normal"))

        except Exception as e:
            self._log(f"Error: {e}")
            self.after(0, lambda: messagebox.showerror("Error", str(e)))
        finally:
            self.after(0, lambda: self.btn_descarrega.config(state="normal"))

    def _iniciar_descarrega_definitiva(self):
        if not self.pano_id:
            return
        self.btn_desc_definitiva.config(state="disabled")
        self.btn_extreu.config(state="disabled")
        self.btn_desa_vista.config(state="disabled")
        zoom = self.zoom_var.get()
        cols = 2 ** zoom
        rows = 2 ** (zoom - 1) if zoom > 0 else 1
        self.progress.config(maximum=cols * rows, value=0)
        self._log(f"Descarregant resolució definitiva (zoom {zoom})...")

        fil = threading.Thread(target=self._proces_descarrega_definitiva, daemon=True)
        fil.start()

    def _proces_descarrega_definitiva(self):
        """Fase 2: baixa la resolució definitiva del panoid ja confirmat a la previsualització."""
        try:
            pano_id = self.pano_id
            zoom = self.zoom_var.get()

            if zoom > 0:
                def progress_cb(fet, total):
                    self.after(0, lambda: self.progress.config(value=fet))
                    self.after(0, lambda: self.log_var.set(f"Descarregant tessel·les... {fet}/{total}"))

                panorama_final = descarrega_panorama(pano_id, zoom, progress_cb)
            else:
                panorama_final = self.panorama_complet

            self.panorama_complet = panorama_final
            self._resolucio_definitiva = True
            nom_fitxer = f"panorama_{pano_id}_z{zoom}.jpg"
            out_path = os.path.join(self.carpeta_var.get(), nom_fitxer)
            panorama_final.save(out_path, quality=95)

            self.after(0, lambda: self._mostra_preview(panorama_final, "panorama definitiu"))
            self._log(f"Fet! Panorama desat a: {out_path}")

        except Exception as e:
            self._log(f"Error: {e}")
            self.after(0, lambda: messagebox.showerror("Error", str(e)))
        finally:
            self.after(0, lambda: self.btn_desc_definitiva.config(state="normal"))
            self.after(0, lambda: self.btn_extreu.config(state="normal"))

    def _mostra_preview(self, panorama_img, etiqueta=""):
        self._preview_source = panorama_img
        self._redibuixa_preview()
        if etiqueta:
            self._log(f"Previsualització actualitzada ({etiqueta}).")

    def _redibuixa_preview(self):
        """Reescala la imatge original a la mida real disponible del requadre.
        Es crida en mostrar-la per primer cop i cada vegada que l'espai canvia
        de mida (redimensionar finestra o arrossegar la separació)."""
        if self._preview_source is None:
            return
        w = self.lbl_preview.winfo_width()
        h = self.lbl_preview.winfo_height()
        if w < 20 or h < 20:
            return  # encara no hi ha una mida real assignada al widget
        copia = self._preview_source.copy()
        copia.thumbnail((max(w - 4, 10), max(h - 4, 10)))
        self.preview_img = ImageTk.PhotoImage(copia)
        self.lbl_preview.config(image=self.preview_img, text="")

    def _on_preview_configure(self, event):
        if self._debounce_preview_id:
            self.after_cancel(self._debounce_preview_id)
        self._debounce_preview_id = self.after(150, self._redibuixa_preview)

    # -- extracció de vista plana ---------------------------------------------

    def _on_slider_change(self):
        """Es crida contínuament mentre s'arrossega un lliscador; amb debounce
        per no llançar una extracció per cada mil·límetre de moviment."""
        if self.panorama_complet is None:
            return
        if self._debounce_id:
            self.after_cancel(self._debounce_id)
        self._debounce_id = self.after(120, lambda: self._extreu_vista(auto=True))

    def _extreu_vista(self, auto=False):
        if self.panorama_complet is None:
            return
        if self._extracting:
            return  # ja n'hi ha una en curs; la propera pujada de lliscador ja la reprogramarà

        # En mode manual (l'usuari ha premut "Actualitza vista"), si encara no tenim
        # la resolució definitiva descarregada, la baixem primer automàticament
        # perquè la vista extreta surti amb la millor qualitat, no la de la
        # previsualització ràpida (zoom 0).
        if not auto and not self._resolucio_definitiva and self.pano_id:
            self._log("Baixant primer la resolució definitiva per a una millor qualitat...")
            self.btn_extreu.config(state="disabled")
            threading.Thread(target=self._baixa_definitiva_i_extreu, daemon=True).start()
            return

        heading = self.heading_var.get()
        pitch = self.pitch_var.get()
        fov = self.fov_var.get()

        self._extracting = True
        if not auto:
            self._log("Extraient vista plana...")
            self.btn_extreu.config(state="disabled")

        def feina():
            try:
                vista = extreu_vista_plana(self.panorama_complet, heading, pitch, fov,
                                            out_w=1280, out_h=960)
                self.vista_extreta = vista
                self.after(0, lambda: self._mostra_vista(vista))
                if not auto:
                    self._log(f"Vista extreta (heading={heading:.0f}°, pitch={pitch:.0f}°, fov={fov:.0f}°).")
                self.after(0, lambda: self.btn_desa_vista.config(state="normal"))
            except Exception as e:
                self._log(f"Error extraient la vista: {e}")
            finally:
                self._extracting = False
                if not auto:
                    self.after(0, lambda: self.btn_extreu.config(state="normal"))

        threading.Thread(target=feina, daemon=True).start()

    def _baixa_definitiva_i_extreu(self):
        """Baixa la resolució definitiva (mateix codi que el botó dedicat) i,
        un cop llesta, extreu la vista plana automàticament."""
        try:
            pano_id = self.pano_id
            zoom = self.zoom_var.get()
            cols = 2 ** zoom
            rows = 2 ** (zoom - 1) if zoom > 0 else 1
            self.after(0, lambda: self.progress.config(maximum=cols * rows, value=0))

            if zoom > 0:
                def progress_cb(fet, total):
                    self.after(0, lambda: self.progress.config(value=fet))
                    self.after(0, lambda: self.log_var.set(f"Descarregant tessel·les... {fet}/{total}"))

                panorama_final = descarrega_panorama(pano_id, zoom, progress_cb)
            else:
                panorama_final = self.panorama_complet

            self.panorama_complet = panorama_final
            self._resolucio_definitiva = True
            nom_fitxer = f"panorama_{pano_id}_z{zoom}.jpg"
            out_path = os.path.join(self.carpeta_var.get(), nom_fitxer)
            panorama_final.save(out_path, quality=95)
            self.after(0, lambda: self._mostra_preview(panorama_final, "panorama definitiu"))
            self._log(f"Resolució definitiva llesta ({out_path}). Extraient vista...")

        except Exception as e:
            self._log(f"Error baixant la resolució definitiva: {e}")
            self.after(0, lambda: messagebox.showerror("Error", str(e)))
            self.after(0, lambda: self.btn_extreu.config(state="normal"))
            return

        # Ara sí, extreu la vista amb la imatge d'alta resolució ja disponible.
        self.after(0, lambda: self._extreu_vista(auto=False))

    def _mostra_vista(self, vista_img):
        self._vista_source = vista_img
        self._redibuixa_vista()

    def _redibuixa_vista(self):
        if self._vista_source is None:
            return
        w = self.lbl_vista.winfo_width()
        h = self.lbl_vista.winfo_height()
        if w < 20 or h < 20:
            return
        copia = self._vista_source.copy()
        copia.thumbnail((max(w - 4, 10), max(h - 4, 10)))
        self.vista_img = ImageTk.PhotoImage(copia)
        self.lbl_vista.config(image=self.vista_img, text="")

    def _on_vista_configure(self, event):
        if self._debounce_vista_resize_id:
            self.after_cancel(self._debounce_vista_resize_id)
        self._debounce_vista_resize_id = self.after(150, self._redibuixa_vista)

    def _desa_vista(self):
        if self.vista_extreta is None:
            return
        nom_suggerit = f"vista_{self.pano_id}_h{int(self.heading_var.get())}.jpg"
        ruta = filedialog.asksaveasfilename(
            initialdir=self.carpeta_var.get(),
            initialfile=nom_suggerit,
            defaultextension=".jpg",
            filetypes=[("JPEG", "*.jpg"), ("PNG", "*.png"), ("Tots els fitxers", "*.*")],
        )
        if ruta:
            self.vista_extreta.save(ruta, quality=95)
            self._log(f"Vista desada a: {ruta}")

    def _log(self, text):
        self.after(0, lambda: self.log_var.set(text))


if __name__ == "__main__":
    app = PanoApp()
    app.mainloop()
