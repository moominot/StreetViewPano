// ==UserScript==
// @name         StreetViewPano — panorama 360 i vista plana
// @namespace    streetviewpano
// @version      1.0
// @description  Sobre Google Maps Street View: descarrega el panorama 360 sencer i/o una vista plana orientada (per a Enscape, etc.)
// @author       Toni
// @match        https://www.google.com/maps/*
// @match        https://www.google.es/maps/*
// @grant        GM_xmlhttpRequest
// @connect      streetviewpixels-pa.googleapis.com
// @run-at       document-idle
// ==/UserScript==

(function () {
    "use strict";

    const TILE_SIZE = 512;
    const FINESTRA_MS = 2500;   // finestra temporal per decidir quin panoid "guanya"
    let panoramaActual = null;   // { canvas, panoid, zoom }
    let panoidMostrat = null;
    let esdevenimentsTiles = [];  // { panoid, temps } — historial curt de tessel·les vistes

    // -----------------------------------------------------------------
    // Detecció del panoid realment visible en pantalla
    // -----------------------------------------------------------------
    // Dos problemes a evitar:
    //  1) La URL de Maps s'actualitza AMB RETARD respecte al que es veu.
    //  2) El visor precarrega tessel·les dels panorames VEÏNS abans que hi
    //     facis clic, així que "l'última tessel·la carregada" pot no ser
    //     la que tens davant.
    // Solució: mirem quin panoid apareix més vegades dins una finestra
    // curta de temps — el panorama que es renderitza de debò sempre
    // acumula moltes més peticions (cobreix tota l'esfera) que un veí
    // només precarregat (unes poques tessel·les de baixa resolució).

    function iniciaObservadorTiles() {
        try {
            const obs = new PerformanceObserver((list) => {
                const ara = performance.now();
                for (const entry of list.getEntries()) {
                    if (entry.name.includes("streetviewpixels-pa.googleapis.com/v1/tile")) {
                        const m = entry.name.match(/panoid=([a-zA-Z0-9_-]{20,30})/);
                        if (m) esdevenimentsTiles.push({ panoid: m[1], temps: ara });
                    }
                }
                esdevenimentsTiles = esdevenimentsTiles.filter(e => ara - e.temps < FINESTRA_MS);
            });
            obs.observe({ type: "resource", buffered: true });
        } catch (e) {
            console.warn("StreetViewPano: no s'ha pogut observar les tessel·les.", e);
        }
    }

    function panoidMesFrequent() {
        const ara = performance.now();
        const recents = esdevenimentsTiles.filter(e => ara - e.temps < FINESTRA_MS);
        if (recents.length === 0) return null;
        const comptes = {};
        for (const e of recents) comptes[e.panoid] = (comptes[e.panoid] || 0) + 1;
        let millor = null, max = 0;
        for (const [pid, c] of Object.entries(comptes)) {
            if (c > max) { max = c; millor = pid; }
        }
        return millor;
    }

    function llegeixPanoId() {
        // Prioritat: el panoid amb més tessel·les carregades a la finestra recent
        // (el que realment es veu, no un veí precarregat).
        const dominant = panoidMesFrequent();
        if (dominant) return dominant;
        // Reserva: si encara no hi ha prou dades, mira la URL.
        const m = location.href.match(/!1s([a-zA-Z0-9_-]{20,30})!2e/);
        return m ? m[1] : null;

    }

    function llegeixOrientacioDeURL() {
        // Format típic: .../@lat,lng,3a,75y,90h,90t/...
        const m = location.href.match(/@[-\d.]+,[-\d.]+,[\d.]+a,([\d.]+)y,([\d.]+)h,([\d.]+)t/);
        if (!m) return null;
        const fov = parseFloat(m[1]);
        const heading = parseFloat(m[2]);
        const tilt = parseFloat(m[3]);
        // Conversió aproximada: a la URL, t=90 sol correspondre a l'horitzó.
        // Pot necessitar un petit ajust manual amb el lliscador de pitch.
        const pitch = 90 - tilt;
        return { heading, pitch, fov };
    }

    // -----------------------------------------------------------------
    // Descàrrega i muntatge de tessel·les (bypass de CORS via GM_xmlhttpRequest)
    // -----------------------------------------------------------------

    function fetchTile(panoid, x, y, zoom) {
        const url = `https://streetviewpixels-pa.googleapis.com/v1/tile?cb_client=maps_sv.tactile&panoid=${panoid}&x=${x}&y=${y}&zoom=${zoom}&nbt=1&fover=2`;
        return new Promise((resolve, reject) => {
            GM_xmlhttpRequest({
                method: "GET",
                url,
                responseType: "blob",
                onload: (res) => {
                    if (res.status === 200) resolve(res.response);
                    else reject(new Error(`Tessel·la (${x},${y}) error ${res.status}`));
                },
                onerror: () => reject(new Error(`Tessel·la (${x},${y}): error de xarxa`)),
            });
        });
    }

    async function descarregaPanorama(panoid, zoom, onProgress) {
        const cols = 2 ** zoom;
        const rows = zoom > 0 ? 2 ** (zoom - 1) : 1;
        const canvas = document.createElement("canvas");
        canvas.width = cols * TILE_SIZE;
        canvas.height = rows * TILE_SIZE;
        const ctx = canvas.getContext("2d");

        let fet = 0;
        const total = cols * rows;
        for (let y = 0; y < rows; y++) {
            for (let x = 0; x < cols; x++) {
                const blob = await fetchTile(panoid, x, y, zoom);
                const bitmap = await createImageBitmap(blob);
                ctx.drawImage(bitmap, x * TILE_SIZE, y * TILE_SIZE);
                fet++;
                if (onProgress) onProgress(fet, total);
            }
        }
        // Al zoom 0 el servidor només retorna una única tessel·la de 512×512,
        // però el contingut real del panorama (relació 2:1 equirectangular)
        // només n'ocupa la meitat superior — la resta és farciment negre de
        // Google. Ho retallem aquí com a mesura de seguretat general.
        const alcadaCorrecta = Math.floor(canvas.width / 2);
        if (canvas.height > alcadaCorrecta) {
            const retallat = document.createElement("canvas");
            retallat.width = canvas.width;
            retallat.height = alcadaCorrecta;
            retallat.getContext("2d").drawImage(
                canvas, 0, 0, canvas.width, alcadaCorrecta, 0, 0, canvas.width, alcadaCorrecta
            );
            return retallat;
        }
        return canvas;
    }

    function baixaCanvas(canvas, filename, qualitat = 0.92) {
        canvas.toBlob((blob) => {
            const url = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url;
            a.download = filename;
            document.body.appendChild(a);
            a.click();
            a.remove();
            URL.revokeObjectURL(url);
        }, "image/jpeg", qualitat);
    }

    // -----------------------------------------------------------------
    // Extracció de vista plana (equirectangular -> perspectiva)
    // -----------------------------------------------------------------

    function extreuVistaPlana(panoramaCanvas, heading, pitch, fov, outW = 1280, outH = 960) {
        const w = panoramaCanvas.width;
        const h = panoramaCanvas.height;
        const srcData = panoramaCanvas.getContext("2d").getImageData(0, 0, w, h).data;

        const outCanvas = document.createElement("canvas");
        outCanvas.width = outW;
        outCanvas.height = outH;
        const outCtx = outCanvas.getContext("2d");
        const outImg = outCtx.createImageData(outW, outH);
        const outData = outImg.data;

        const fovRad = (fov * Math.PI) / 180;
        const aspect = outW / outH;
        const zc = 1 / Math.tan(fovRad / 2);
        const pitchRad = (pitch * Math.PI) / 180;
        const yawRad = (heading * Math.PI) / 180;
        const cosP = Math.cos(pitchRad), sinP = Math.sin(pitchRad);
        const cosY = Math.cos(yawRad), sinY = Math.sin(yawRad);

        for (let j = 0; j < outH; j++) {
            const yv = 1 - (2 * j) / (outH - 1);
            for (let i = 0; i < outW; i++) {
                const xv = -1 + (2 * i) / (outW - 1);
                let dx = xv * aspect, dy = yv, dz = zc;
                const norm = Math.sqrt(dx * dx + dy * dy + dz * dz);
                dx /= norm; dy /= norm; dz /= norm;

                const dy2 = dy * cosP - dz * sinP;
                const dz2 = dy * sinP + dz * cosP;
                const dx2 = dx;

                const dx3 = dx2 * cosY + dz2 * sinY;
                const dz3 = -dx2 * sinY + dz2 * cosY;
                const dy3 = dy2;

                const theta = Math.atan2(dx3, dz3);
                const phi = Math.asin(Math.max(-1, Math.min(1, dy3)));

                // u=0 (vora esquerra) correspon a heading=0° al mosaic original de
                // Street View — NO el centre. Per això aquí NO s'afegeix +0.5.
                let uFrac = theta / (2 * Math.PI);
                if (uFrac < 0) uFrac += 1;
                let u = Math.floor(uFrac * w);
                let v = Math.floor((0.5 - phi / Math.PI) * h);
                u = Math.max(0, Math.min(w - 1, u));
                v = Math.max(0, Math.min(h - 1, v));

                const srcIdx = (v * w + u) * 4;
                const dstIdx = (j * outW + i) * 4;
                outData[dstIdx] = srcData[srcIdx];
                outData[dstIdx + 1] = srcData[srcIdx + 1];
                outData[dstIdx + 2] = srcData[srcIdx + 2];
                outData[dstIdx + 3] = 255;
            }
        }
        outCtx.putImageData(outImg, 0, 0);
        return outCanvas;
    }

    // -----------------------------------------------------------------
    // Interfície flotant
    // -----------------------------------------------------------------

    function creaPanell() {
        const panell = document.createElement("div");
        panell.id = "svpano-panell";
        panell.innerHTML = `
            <style>
                #svpano-panell {
                    position: fixed; top: 70px; right: 10px; z-index: 999999;
                    background: #fff; border: 1px solid #ccc; border-radius: 8px;
                    box-shadow: 0 2px 10px rgba(0,0,0,.25);
                    padding: 10px; width: 250px; font: 12px/1.4 Arial, sans-serif; color: #222;
                }
                #svpano-panell h3 { margin: 0 0 6px; font-size: 13px; }
                #svpano-panell label { display: block; margin-top: 6px; font-size: 11px; color: #555; }
                #svpano-panell input[type=number] { width: 60px; }
                #svpano-panell input[type=range] { width: 100%; }
                #svpano-panell button {
                    margin-top: 8px; width: 100%; padding: 5px; cursor: pointer;
                }
                #svpano-panell .fila { display: flex; gap: 6px; align-items: center; }
                #svpano-panell #svpano-log { font-size: 10px; color: #666; margin-top: 6px; word-break: break-word; }
                #svpano-panell #svpano-toggle {
                    position: absolute; top: -10px; right: -10px; width: 20px; height: 20px;
                    border-radius: 50%; background: #444; color: #fff; border: none; cursor: pointer;
                    font-size: 12px; line-height: 20px; padding: 0;
                }
                #svpano-panell.collapsed #svpano-body { display: none; }
            </style>
            <button id="svpano-toggle" title="Amaga/mostra">–</button>
            <h3>StreetViewPano</h3>
            <div id="svpano-body">
                <div id="svpano-panoid">Panoid: —</div>

                <label>Zoom (resolució del 360)
                    <select id="svpano-zoom">
                        <option value="0">0 — 512×256</option>
                        <option value="1">1 — 1024×512</option>
                        <option value="2">2 — 2048×1024</option>
                        <option value="3">3 — 4096×2048</option>
                        <option value="4" selected>4 — 8192×4096</option>
                        <option value="5">5 — 16384×8192</option>
                    </select>
                </label>
                <button id="svpano-desc360">Descarrega panorama 360</button>

                <hr>

                <label>Heading <span id="svpano-heading-v">0</span>°
                    <input type="range" id="svpano-heading" min="0" max="360" value="0">
                </label>
                <label>Pitch <span id="svpano-pitch-v">0</span>°
                    <input type="range" id="svpano-pitch" min="-90" max="90" value="0">
                </label>
                <label>FOV <span id="svpano-fov-v">90</span>°
                    <input type="range" id="svpano-fov" min="30" max="120" value="90">
                </label>
                <label>Ajust calibratge heading
                    <input type="number" id="svpano-ajust" value="-20" step="1" style="width:55px">
                    ° <span style="font-size:10px;color:#888">(corregeix el desfasament de Google)</span>
                </label>
                <button id="svpano-llegeix">Llegeix orientació de la pantalla</button>
                <button id="svpano-descplana">Descarrega vista plana</button>

                <div id="svpano-log">Llest.</div>
            </div>
        `;
        document.body.appendChild(panell);

        const log = (t) => (panell.querySelector("#svpano-log").textContent = t);

        panell.querySelector("#svpano-toggle").addEventListener("click", () => {
            panell.classList.toggle("collapsed");
        });

        // lliscadors: actualitzen l'etiqueta numèrica
        [["heading", "svpano-heading"], ["pitch", "svpano-pitch"], ["fov", "svpano-fov"]].forEach(
            ([_, id]) => {
                const input = panell.querySelector(`#${id}`);
                const etiqueta = panell.querySelector(`#${id}-v`);
                input.addEventListener("input", () => (etiqueta.textContent = input.value));
            }
        );

        panell.querySelector("#svpano-desc360").addEventListener("click", async () => {
            const panoid = llegeixPanoId();
            if (!panoid) { log("No s'ha trobat cap panoid. Obre una vista de Street View."); return; }
            const zoom = parseInt(panell.querySelector("#svpano-zoom").value, 10);
            log(`Descarregant panorama (zoom ${zoom})...`);
            try {
                const canvas = await descarregaPanorama(panoid, zoom, (fet, total) => {
                    log(`Tessel·les: ${fet}/${total}`);
                });
                panoramaActual = { canvas, panoid, zoom };
                baixaCanvas(canvas, `panorama_${panoid}_z${zoom}.jpg`);
                log(`Fet! panorama_${panoid}_z${zoom}.jpg`);
            } catch (e) {
                log(`Error: ${e.message}`);
            }
        });

        panell.querySelector("#svpano-llegeix").addEventListener("click", () => {
            const info = llegeixOrientacioDeURL();
            if (!info) { log("No s'ha pogut llegir l'orientació de la URL."); return; }
            const ajust = parseFloat(panell.querySelector("#svpano-ajust").value) || 0;
            const headingAjustat = ((info.heading + ajust) % 360 + 360) % 360;
            panell.querySelector("#svpano-heading").value = headingAjustat;
            panell.querySelector("#svpano-heading-v").textContent = headingAjustat.toFixed(0);
            panell.querySelector("#svpano-pitch").value = info.pitch;
            panell.querySelector("#svpano-pitch-v").textContent = info.pitch.toFixed(0);
            panell.querySelector("#svpano-fov").value = info.fov;
            panell.querySelector("#svpano-fov-v").textContent = info.fov.toFixed(0);
            log(`Orientació llegida (heading amb ajust de ${ajust}°, pitch aproximat).`);
        });

        panell.querySelector("#svpano-descplana").addEventListener("click", async () => {
            const panoid = llegeixPanoId();
            if (!panoid) { log("No s'ha trobat cap panoid. Obre una vista de Street View."); return; }

            const heading = parseFloat(panell.querySelector("#svpano-heading").value);
            const pitch = parseFloat(panell.querySelector("#svpano-pitch").value);
            const fov = parseFloat(panell.querySelector("#svpano-fov").value);
            const zoomDesitjat = parseInt(panell.querySelector("#svpano-zoom").value, 10);

            try {
                // Si encara no tenim el panorama d'aquest punt, o el que tenim és
                // d'una resolució inferior a la seleccionada al desplegable de
                // zoom, el baixem automàticament abans d'extreure la vista —
                // així la qualitat final coincideix amb la de l'app Python.
                if (!panoramaActual || panoramaActual.panoid !== panoid || panoramaActual.zoom < zoomDesitjat) {
                    log(`Baixant primer la resolució definitiva (zoom ${zoomDesitjat}) per a una millor qualitat...`);
                    const canvas = await descarregaPanorama(panoid, zoomDesitjat, (fet, total) => {
                        log(`Tessel·les: ${fet}/${total}`);
                    });
                    panoramaActual = { canvas, panoid, zoom: zoomDesitjat };
                }
                log("Extraient vista plana...");
                const vista = extreuVistaPlana(panoramaActual.canvas, heading, pitch, fov);
                baixaCanvas(vista, `vista_${panoid}_h${Math.round(heading)}.jpg`);
                log(`Fet! vista_${panoid}_h${Math.round(heading)}.jpg`);
            } catch (e) {
                log(`Error: ${e.message}`);
            }
        });

        // refresc periòdic del panoid mostrat (SPA: la URL canvia sense recarregar)
        setInterval(() => {
            const pid = llegeixPanoId();
            if (pid !== panoidMostrat) {
                panoidMostrat = pid;
                panell.querySelector("#svpano-panoid").textContent = "Panoid: " + (pid || "—");
            }
        }, 1000);
    }

    // Comença a observar les tessel·les des del primer moment, encara que
    // el panell trigui una mica més a aparèixer.
    iniciaObservadorTiles();

    // Assegura que el body ja existeix abans d'injectar el panell
    if (document.body) {
        creaPanell();
    } else {
        window.addEventListener("DOMContentLoaded", creaPanell);
    }
})();
