import os
import io
import json
import time
import base64
from datetime import datetime
from typing import List, Dict, Any, Optional

import numpy as np
import pandas as pd
import streamlit as st
from streamlit_drawable_canvas import st_canvas
from PIL import Image as PILImage
from fpdf import FPDF

# ==============================================================================
# CONFIGURATION GÉNÉRALE DE LA SÉCURITÉ ET DU SERVEUR LOCAL GMAO
# ==============================================================================
APP_TITLE = "GMAO - Société Sleem Hayet | Serveur Technique Local"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

LOCAL_BACKUP_FILE = os.path.join(BASE_DIR, "gmao_local_data.json")
LOCAL_STOCK_FILE = os.path.join(BASE_DIR, "gmao_stock_data.json")
LOCAL_MVT_FILE = os.path.join(BASE_DIR, "gmao_stock_mouvements.json")
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads_pieces")
SIGN_DIR = os.path.join(BASE_DIR, "uploads_signatures")

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(SIGN_DIR, exist_ok=True)

# Mots de passe distincts par rôle
PIN_DEMANDEUR = "hayet2025"    # Réservé aux Demandeurs
PIN_TECHNICIEN = "tec1996"     # Réservé aux Techniciens
PIN_DIRECTEUR = "hayet007"     # Réservé au Responsable / Directeur Technique
PIN_MAGASIN = "MAG2025"        # Réservé au Responsable Magasin

CURRENCIES = ["TND", "EUR", "USD"]

st.set_page_config(
    page_title=APP_TITLE,
    page_icon="⚙️",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# ==============================================================================
# COUCHE D'ACCÈS AUX DONNÉES LOCALES SUR PC (INTERVENTIONS & MAGASIN)
# ==============================================================================
def _read_json_list(filepath: str) -> List[Dict[str, Any]]:
    if not os.path.exists(filepath):
        return []
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read().strip()
        if not content:
            return []
        data = json.loads(content)
        return data if isinstance(data, list) else []


def _write_json_list(filepath: str, data: List[Dict[str, Any]]) -> None:
    tmp_path = f"{filepath}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, filepath)


class GMAODataRepository:
    """Stockage local exclusif sur le PC pour les bons d'intervention."""

    def __init__(self):
        self.is_cloud = False

    def get_all(self) -> List[Dict[str, Any]]:
        data = _read_json_list(LOCAL_BACKUP_FILE)
        return sorted(data, key=lambda x: x.get("createdAt", 0), reverse=True)

    def create(self, record: Dict[str, Any]) -> bool:
        data = self.get_all()
        record["firebaseId"] = f"local_{int(time.time() * 1000)}"
        data.insert(0, record)
        _write_json_list(LOCAL_BACKUP_FILE, data)
        return True

    def update(self, record_id: str, updates: Dict[str, Any]) -> bool:
        data = self.get_all()
        for item in data:
            if item.get("firebaseId") == record_id:
                item.update(updates)
                break
        _write_json_list(LOCAL_BACKUP_FILE, data)
        return True

    def delete(self, record_id: str) -> bool:
        data = [i for i in self.get_all() if i.get("firebaseId") != record_id]
        _write_json_list(LOCAL_BACKUP_FILE, data)
        return True


class SparePartsRepository:
    """Stockage local exclusif sur le PC pour le magasin et les mouvements."""

    def __init__(self):
        self.is_cloud = False

    def get_all_parts(self) -> List[Dict[str, Any]]:
        data = _read_json_list(LOCAL_STOCK_FILE)
        return sorted(data, key=lambda x: x.get("designation", "").lower())

    def add_part(self, part: Dict[str, Any]) -> bool:
        data = self.get_all_parts()
        part["id"] = f"part_{int(time.time() * 1000)}"
        data.append(part)
        _write_json_list(LOCAL_STOCK_FILE, data)
        return True

    def update_part(self, part_id: str, updates: Dict[str, Any]) -> bool:
        data = self.get_all_parts()
        for item in data:
            if item.get("id") == part_id:
                item.update(updates)
                break
        _write_json_list(LOCAL_STOCK_FILE, data)
        return True

    def delete_part(self, part_id: str) -> bool:
        data = [p for p in self.get_all_parts() if p.get("id") != part_id]
        _write_json_list(LOCAL_STOCK_FILE, data)
        return True

    def get_movements(self) -> List[Dict[str, Any]]:
        data = _read_json_list(LOCAL_MVT_FILE)
        return sorted(data, key=lambda x: x.get("createdAt", 0), reverse=True)

    def log_movement(self, movement: Dict[str, Any]) -> None:
        movement["createdAt"] = int(time.time() * 1000)
        movement["dateHeure"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        mvts = self.get_movements()
        mvts.insert(0, movement)
        _write_json_list(LOCAL_MVT_FILE, mvts)

    def adjust_stock(
        self,
        part_id: str,
        delta_qty: int,
        mvt_type: str,
        motif: str,
        acteur: str
    ) -> bool:
        parts = self.get_all_parts()
        target = next((p for p in parts if p.get("id") == part_id), None)
        if not target:
            return False
        old_qty = int(target.get("quantite", 0))
        if delta_qty < 0 and abs(delta_qty) > old_qty:
            return False
        new_qty = max(0, old_qty + delta_qty)
        pu = target.get("prixUnitaire")
        new_total = round(new_qty * float(pu), 2) if pu not in (None, "") else None
        updates: Dict[str, Any] = {"quantite": new_qty, "updatedAt": int(time.time() * 1000)}
        if new_total is not None:
            updates["prixTotal"] = new_total
        if self.update_part(part_id, updates):
            self.log_movement({
                "partId": part_id,
                "reference": target.get("reference", "-"),
                "designation": target.get("designation", ""),
                "type": mvt_type,
                "quantite": abs(delta_qty),
                "stockAvant": old_qty,
                "stockApres": new_qty,
                "motif": motif,
                "acteur": acteur,
            })
            return True
        return False


# ==============================================================================
# FONCTIONS PHOTOS PIÈCES, SIGNATURES MANUSCRITES, EXPORT PDF BON, INVENTAIRE & KPI
# ==============================================================================
def save_uploaded_part_photo(uploaded_file, ref_code: str) -> Dict[str, str]:
    """Enregistre la photo facultative d'une pièce en local sur le PC."""
    if uploaded_file is None:
        return {"path": "", "base64": ""}

    raw_bytes = uploaded_file.getvalue()
    if not raw_bytes:
        return {"path": "", "base64": ""}

    ext = uploaded_file.name.split(".")[-1].lower() if "." in uploaded_file.name else "jpg"
    if ext not in ("png", "jpg", "jpeg", "webp"):
        ext = "jpg"

    safe_ref = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in (ref_code or "piece"))
    filename = f"piece_{safe_ref}_{int(time.time() * 1000)}.{ext}"
    filepath = os.path.join(UPLOAD_DIR, filename)

    with open(filepath, "wb") as f:
        f.write(raw_bytes)

    b64_str = base64.b64encode(raw_bytes).decode("utf-8")
    return {"path": filepath, "base64": b64_str}


def render_part_image(path_val: str, b64_val: str, caption: str = "", width: int = 160) -> None:
    """Affiche la photo d'une pièce depuis le disque du PC."""
    if path_val and os.path.exists(path_val):
        st.image(path_val, width=width, caption=caption or None)
    elif b64_val:
        try:
            st.image(io.BytesIO(base64.b64decode(b64_val)), width=width, caption=caption or None)
        except Exception:
            st.caption("Photo indisponible")
    else:
        st.caption("Aucune photo")


def canvas_has_drawing(canvas_result) -> bool:
    """Vérifie qu'un tracé réel existe sur le canvas (pas un cadre blanc vide)."""
    if canvas_result is None:
        return False
    try:
        img_data = canvas_result.image_data
    except RuntimeError:
        return False
    if img_data is None:
        return False
    arr = np.array(img_data)
    if arr.size == 0:
        return False
    if arr.shape[-1] == 4:
        rgb = arr[:, :, :3]
        alpha = arr[:, :, 3]
        non_white = np.any(rgb < 250, axis=-1) & (alpha > 0)
        return bool(np.any(non_white))
    return bool(np.any(arr < 250))


def save_canvas_signature(canvas_result, prefix: str, ref_code: str) -> Dict[str, str]:
    """Enregistre la signature en PNG local sur le PC."""
    if not canvas_has_drawing(canvas_result):
        return {"path": "", "base64": ""}

    img = PILImage.fromarray(canvas_result.image_data.astype("uint8"), mode="RGBA")
    bg = PILImage.new("RGBA", img.size, (255, 255, 255, 255))
    img_flat = PILImage.alpha_composite(bg, img).convert("RGB")

    filename = f"{prefix}_{ref_code}_{int(time.time())}.png"
    filepath = os.path.join(SIGN_DIR, filename)
    img_flat.save(filepath, format="PNG")

    buf = io.BytesIO()
    img_flat.save(buf, format="PNG")
    b64_str = base64.b64encode(buf.getvalue()).decode("utf-8")

    return {"path": filepath, "base64": b64_str}


def render_signature_image(path_val: str, b64_val: str, caption: str, width: int = 190) -> None:
    """Affiche l'image de signature depuis le disque du PC."""
    if path_val and os.path.exists(path_val):
        st.image(path_val, width=width, caption=caption)
    elif b64_val:
        try:
            img_bytes = base64.b64decode(b64_val)
            st.image(io.BytesIO(img_bytes), width=width, caption=caption)
        except Exception:
            st.warning("⚠️ Signature corrompue ou illisible.")
    else:
        st.info("En attente de signature")


def _decode_b64_to_temp_png(b64_val: str, prefix: str) -> Optional[str]:
    """Écrit une image Base64 dans un fichier PNG local et retourne son chemin."""
    if not b64_val:
        return None
    try:
        raw = base64.b64decode(b64_val.strip())
        img = PILImage.open(io.BytesIO(raw)).convert("RGB")
        temp_path = os.path.join(SIGN_DIR, f"tmp_{prefix}_{int(time.time() * 1000)}.png")
        img.save(temp_path, format="PNG")
        return temp_path
    except Exception:
        return None


def _resolve_signature_file(path_val: str, b64_val: str, prefix: str) -> Optional[str]:
    if path_val and os.path.exists(path_val):
        return path_val
    return _decode_b64_to_temp_png(b64_val, prefix)


def build_bon_intervention_pdf(fiche: Dict[str, Any]) -> bytes:
    """Génère le PDF officiel du Bon d'Intervention avec photos et signatures finales."""
    pdf = FPDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=12)

    def clean_txt(val: Any) -> str:
        txt = str(val or "")
        return txt.encode("latin-1", errors="replace").decode("latin-1")

    pdf.set_font("helvetica", "B", 15)
    pdf.set_text_color(30, 58, 138)
    pdf.cell(0, 8, "SOCIETE SLEEM HAYET", 0, 1, "C")
    pdf.set_font("helvetica", "B", 10)
    pdf.set_text_color(71, 85, 105)
    pdf.cell(0, 5, "SERVICE TECHNIQUE & MAINTENANCE INDUSTRIELLE", 0, 1, "C")
    pdf.ln(2)

    pdf.set_fill_color(241, 245, 249)
    pdf.set_draw_color(203, 213, 225)
    pdf.set_text_color(15, 23, 42)
    pdf.set_font("helvetica", "B", 10)
    badge_txt = clean_txt(
        f"Bon d'Intervention N° : {fiche.get('numero', '')} | Date : {fiche.get('date', '')} a {fiche.get('heure', '')}"
    )
    pdf.cell(0, 8, badge_txt, 1, 1, "C", fill=True)
    pdf.ln(4)

    pdf.set_font("helvetica", "B", 11)
    pdf.set_text_color(30, 58, 138)
    pdf.cell(95, 6, "1. Demandeur", 0, 0, "L")
    pdf.cell(95, 6, "2. Equipement", 0, 1, "L")

    pdf.set_font("helvetica", "", 9.5)
    pdf.set_text_color(15, 23, 42)
    y_top = pdf.get_y()
    pdf.multi_cell(
        95, 5,
        clean_txt(
            f"Nom : {fiche.get('nom', '')} ({fiche.get('poste', '')})\n"
            f"Service : {fiche.get('service', '')}\n"
            f"Telephone : {fiche.get('tel') or '-'}"
        ),
        border=1
    )
    y_after_left = pdf.get_y()

    pdf.set_xy(105, y_top)
    pdf.multi_cell(
        95, 5,
        clean_txt(
            f"Machine : {fiche.get('machine', '')} ({fiche.get('codeMachine') or 'Sans code'})\n"
            f"Ligne / Zone : {fiche.get('ligne') or '-'}\n"
            f"Localisation : {fiche.get('localisation') or '-'}"
        ),
        border=1
    )
    pdf.set_y(max(y_after_left, pdf.get_y()) + 4)

    pdf.set_font("helvetica", "B", 11)
    pdf.set_text_color(30, 58, 138)
    pdf.cell(0, 6, "3 & 4. Anomalie & Gravite", 0, 1, "L")
    pdf.set_font("helvetica", "", 9.5)
    pdf.set_text_color(15, 23, 42)
    natures_txt = ", ".join(fiche.get("natures", [])) or "-"
    pdf.multi_cell(
        0, 5,
        clean_txt(
            f"Nature : {natures_txt}\n"
            f"Impact Production : {fiche.get('impact', '')} | Priorite : {fiche.get('priorite', '')}\n"
            f"Description : {fiche.get('description', '')}"
        ),
        border=1
    )
    pdf.ln(4)

    pdf.set_font("helvetica", "B", 11)
    pdf.set_text_color(30, 58, 138)
    pdf.cell(0, 6, "5. Rapport Technique & 6. Cloture", 0, 1, "L")
    pdf.set_font("helvetica", "", 9.5)
    pdf.set_text_color(15, 23, 42)
    pdf.multi_cell(
        0, 5,
        clean_txt(
            f"Technicien(s) : {fiche.get('techniciens') or 'Non assigne'} | Duree : {fiche.get('duree') or '-'}\n"
            f"Prise en charge : {fiche.get('priseEnCharge') or '-'} | Fin : {fiche.get('dateFin') or '-'}\n"
            f"Etat final de l'equipement : {fiche.get('etatEquipement') or fiche.get('statut', '')}\n"
            f"Diagnostic : {fiche.get('diagnostic') or 'Aucun diagnostic'}\n"
            f"Travaux realises : {fiche.get('travaux') or 'Aucun travail'}\n"
            f"Observations : {fiche.get('observations') or 'Aucune'}"
        ),
        border=1
    )
    pdf.ln(3)

    pieces = fiche.get("pieces", [])
    if pieces:
        pdf.set_font("helvetica", "B", 9)
        pdf.set_fill_color(241, 245, 249)
        pdf.cell(90, 6, "Designation Piece", 1, 0, "L", fill=True)
        pdf.cell(65, 6, "Code Article / Ref.", 1, 0, "L", fill=True)
        pdf.cell(35, 6, "Quantite", 1, 1, "C", fill=True)
        pdf.set_font("helvetica", "", 9)
        for p in pieces:
            pdf.cell(90, 5.5, clean_txt(p.get("designation", "")), 1, 0, "L")
            pdf.cell(65, 5.5, clean_txt(p.get("reference") or "-"), 1, 0, "L")
            pdf.cell(35, 5.5, clean_txt(p.get("quantite", "")), 1, 1, "C")
        pdf.ln(3)

    valid_photos = [p for p in fiche.get("photosPieces", []) if p and os.path.exists(p)]
    if valid_photos:
        if pdf.get_y() > 210:
            pdf.add_page()
        pdf.set_font("helvetica", "B", 11)
        pdf.set_text_color(30, 58, 138)
        pdf.cell(0, 6, "Photos Jointes de l'Intervention", 0, 1, "L")
        pdf.ln(2)

        img_w = 58
        img_h = 42
        gap = 8
        start_x = 10
        cur_x = start_x
        cur_y = pdf.get_y()

        for p_path in valid_photos:
            if cur_x + img_w > 200:
                cur_x = start_x
                cur_y += img_h + 8
                if cur_y + img_h > 275:
                    pdf.add_page()
                    cur_y = pdf.get_y()
            try:
                pdf.image(p_path, x=cur_x, y=cur_y, w=img_w, h=img_h)
                cur_x += img_w + gap
            except Exception:
                pass

        pdf.set_y(cur_y + img_h + 6)

    if pdf.get_y() > 225:
        pdf.add_page()
    pdf.set_font("helvetica", "B", 11)
    pdf.set_text_color(30, 58, 138)
    pdf.cell(0, 6, "7. Signatures Manuscrites Finales", 0, 1, "L")
    pdf.ln(2)

    sig_entries = [
        ("Demandeur", fiche.get("valDemandeur", ""), _resolve_signature_file(
            fiche.get("pathSigDemandeur", ""), fiche.get("b64SigDemandeur", ""), "dem"
        )),
        ("Technicien", fiche.get("valTech", ""), _resolve_signature_file(
            fiche.get("pathSigTech", ""), fiche.get("b64SigTech", ""), "tech"
        )),
        ("Responsable Technique", fiche.get("valResp", ""), _resolve_signature_file(
            fiche.get("pathSigResp", ""), fiche.get("b64SigResp", ""), "resp"
        )),
    ]

    box_w = 61
    box_h = 38
    sig_gap = 3.5
    base_x = 10
    base_y = pdf.get_y()

    for idx, (role_lbl, signer_name, img_file) in enumerate(sig_entries):
        bx = base_x + idx * (box_w + sig_gap)
        pdf.set_draw_color(203, 213, 225)
        pdf.rect(bx, base_y, box_w, box_h)

        pdf.set_xy(bx + 1, base_y + 1.5)
        pdf.set_font("helvetica", "B", 8.5)
        pdf.set_text_color(15, 23, 42)
        pdf.cell(box_w - 2, 4, clean_txt(role_lbl), 0, 1, "C")

        pdf.set_xy(bx + 1, base_y + 5.5)
        pdf.set_font("helvetica", "", 8)
        pdf.set_text_color(71, 85, 105)
        pdf.cell(box_w - 2, 4, clean_txt(f"Nom : {signer_name or '-'}"), 0, 1, "C")

        if img_file and os.path.exists(img_file):
            try:
                pdf.image(img_file, x=bx + 8, y=base_y + 11, w=box_w - 16, h=24)
            except Exception:
                pass
        else:
            pdf.set_xy(bx + 1, base_y + 18)
            pdf.set_font("helvetica", "I", 8)
            pdf.set_text_color(148, 163, 184)
            pdf.cell(box_w - 2, 5, "En attente de signature", 0, 0, "C")

    return bytes(pdf.output())


def build_inventaire_magasin_pdf(parts_list: List[Dict[str, Any]]) -> bytes:
    """Génère le PDF officiel de l'inventaire du Magasin des Pièces de Rechange."""
    pdf = FPDF(orientation="L", unit="mm", format="A4")
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=12)

    def clean_txt(val: Any) -> str:
        txt = str(val if val not in (None, "") else "-")
        return txt.encode("latin-1", errors="replace").decode("latin-1")

    pdf.set_font("helvetica", "B", 15)
    pdf.set_text_color(30, 58, 138)
    pdf.cell(0, 8, "SOCIETE SLEEM HAYET - MAGASIN PIECES DE RECHANGE", 0, 1, "C")
    pdf.set_font("helvetica", "I", 9.5)
    pdf.set_text_color(71, 85, 105)
    pdf.cell(0, 5, f"Etat Officiel d'Inventaire edite le {datetime.now().strftime('%d/%m/%Y a %H:%M')}", 0, 1, "C")
    pdf.ln(4)

    headers = [
        "Code Article", "Designation", "Machine", "Emplacement",
        "Fournisseur", "Type", "Qte", "Unite", "Seuil", "Devise", "P.U", "Total"
    ]
    widths = [24, 48, 32, 26, 32, 20, 13, 13, 14, 15, 20, 22]

    pdf.set_font("helvetica", "B", 8)
    pdf.set_fill_color(30, 58, 138)
    pdf.set_text_color(255, 255, 255)
    for h, w in zip(headers, widths):
        pdf.cell(w, 7, clean_txt(h), 1, 0, "C", fill=True)
    pdf.ln()

    pdf.set_font("helvetica", "", 7.5)
    pdf.set_text_color(15, 23, 42)
    totals_by_curr: Dict[str, float] = {c: 0.0 for c in CURRENCIES}

    for p in parts_list:
        qte = int(p.get("quantite", 0))
        seuil = int(p.get("seuilMin", 0))
        curr = p.get("devise", "TND") if p.get("devise") in CURRENCIES else "TND"
        pu = p.get("prixUnitaire")
        pt = p.get("prixTotal")
        pu_str = f"{float(pu):.2f}" if pu not in (None, "") else "-"
        pt_str = f"{float(pt):.2f}" if pt not in (None, "") else "-"
        if pt not in (None, ""):
            totals_by_curr[curr] = totals_by_curr.get(curr, 0.0) + float(pt)

        if qte <= seuil:
            pdf.set_fill_color(254, 243, 199)
            fill_row = True
        else:
            fill_row = False

        row_vals = [
            p.get("reference", "-"),
            p.get("designation", ""),
            p.get("machine", "-"),
            p.get("emplacement", "-"),
            p.get("fournisseur", "-"),
            p.get("typePiece", "Original"),
            str(qte),
            p.get("unite", "pcs"),
            str(seuil),
            curr,
            pu_str,
            pt_str,
        ]
        aligns = ["L", "L", "L", "L", "L", "C", "C", "C", "C", "C", "R", "R"]
        for val, w, al in zip(row_vals, widths, aligns):
            pdf.cell(w, 6, clean_txt(val)[:32], 1, 0, al, fill=fill_row)
        pdf.ln()

    pdf.ln(3)
    pdf.set_font("helvetica", "B", 9.5)
    tot_parts_str = " | ".join([f"{val:,.2f} {c}" for c, val in totals_by_curr.items() if val > 0]) or "0.00 TND"
    pdf.cell(0, 6, clean_txt(f"Total References : {len(parts_list)} | Valeur Totale Chiffree : {tot_parts_str}"), 0, 1, "R")

    return bytes(pdf.output())


def generate_di_number(interventions: List[Dict[str, Any]]) -> str:
    year = datetime.now().year
    next_seq = len(interventions) + 1
    return f"DI-{year}-{str(next_seq).zfill(3)}"


def parse_duree_en_heures(duree_str: str) -> float:
    if not duree_str:
        return 0.5
    try:
        h = 0.0
        m = 0.0
        parts = duree_str.split()
        for p in parts:
            if "h" in p:
                h = float(p.replace("h", "").strip())
            elif "min" in p:
                m = float(p.replace("min", "").strip())
        return h + (m / 60.0)
    except Exception:
        return 0.5


class KPIReportPDF(FPDF):
    def header(self):
        self.set_font("helvetica", "B", 16)
        self.set_text_color(30, 58, 138)
        self.cell(0, 10, "SOCIETE SLEEM HAYET - SERVICE TECHNIQUE", 0, 1, "C")
        self.set_font("helvetica", "I", 10)
        self.set_text_color(71, 85, 105)
        self.cell(0, 6, "Rapport Officiel des Indicateurs de Performance (KPI, MTBF, MTTR)", 0, 1, "C")
        self.ln(5)
        self.set_draw_color(30, 58, 138)
        self.set_line_width(0.8)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(8)

    def footer(self):
        self.set_y(-15)
        self.set_font("helvetica", "I", 8)
        self.set_text_color(120, 120, 120)
        self.cell(0, 10, f"Page {self.page_no()} | Date d'edition : {datetime.now().strftime('%d/%m/%Y a %H:%M')}", 0, 0, "C")


def generate_pdf_kpi_report(total_int, nb_clot, mttr_val, taux_c, df_machines, df_techs) -> bytes:
    pdf = KPIReportPDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)

    pdf.set_font("helvetica", "B", 12)
    pdf.set_fill_color(241, 245, 249)
    pdf.cell(0, 8, " 1. Indicateurs Globaux de Maintenance", 0, 1, "L", fill=True)
    pdf.set_font("helvetica", "", 10)
    pdf.ln(2)

    pdf.cell(95, 6, f"- Total des interventions enregistrees : {total_int}", 0, 0)
    pdf.cell(95, 6, f"- Interventions cloturees : {nb_clot}", 0, 1)
    pdf.cell(95, 6, f"- MTTR Global Moyen : {mttr_val:.2f} heures", 0, 0)
    pdf.cell(95, 6, f"- Taux de cloture : {taux_c:.1f}%", 0, 1)
    pdf.ln(8)

    pdf.set_font("helvetica", "B", 12)
    pdf.cell(0, 8, " 2. Analyse par Equipement (MTBF, MTTR, Disponibilite)", 0, 1, "L", fill=True)
    pdf.ln(3)

    pdf.set_font("helvetica", "B", 9)
    pdf.set_fill_color(30, 58, 138)
    pdf.set_text_color(255, 255, 255)

    col_widths = [45, 25, 30, 25, 35, 30]
    headers = ["Equipement", "Pannes", "Arret (h)", "MTTR(h)", "MTBF(h)", "Disp(%)"]

    for i, h in enumerate(headers):
        pdf.cell(col_widths[i], 7, h, 1, 0, "C", fill=True)
    pdf.ln()

    pdf.set_font("helvetica", "", 9)
    pdf.set_text_color(0, 0, 0)

    for _, row in df_machines.iterrows():
        pdf.cell(col_widths[0], 6, str(row["Équipement"]), 1, 0, "L")
        pdf.cell(col_widths[1], 6, str(row["Nombre de Pannes"]), 1, 0, "C")
        pdf.cell(col_widths[2], 6, str(row["Temps d'Arrêt Total (h)"]), 1, 0, "C")
        pdf.cell(col_widths[3], 6, str(row["MTTR (h)"]), 1, 0, "C")
        pdf.cell(col_widths[4], 6, str(row["MTBF (h)"]), 1, 0, "C")
        pdf.cell(col_widths[5], 6, f"{row['Disponibilité (%)']}%", 1, 1, "C")

    pdf.ln(8)

    pdf.set_font("helvetica", "B", 12)
    pdf.cell(0, 8, " 3. Performance et Productivite des Techniciens", 0, 1, "L", fill=True)
    pdf.ln(3)

    pdf.set_font("helvetica", "B", 9)
    pdf.set_fill_color(30, 58, 138)
    pdf.set_text_color(255, 255, 255)

    tech_widths = [65, 35, 45, 45]
    tech_headers = ["Technicien", "Cloturees", "Temps Total (h)", "Moy / Interv (h)"]

    for i, h in enumerate(tech_headers):
        pdf.cell(tech_widths[i], 7, h, 1, 0, "C", fill=True)
    pdf.ln()

    pdf.set_font("helvetica", "", 9)
    pdf.set_text_color(0, 0, 0)

    for _, row in df_techs.iterrows():
        pdf.cell(tech_widths[0], 6, str(row["Technicien"]), 1, 0, "L")
        pdf.cell(tech_widths[1], 6, str(row["Interventions Clôturées"]), 1, 0, "C")
        pdf.cell(tech_widths[2], 6, str(row["Temps Total Passé (h)"]), 1, 0, "C")
        pdf.cell(tech_widths[3], 6, str(row["Temps Moyen / Intervention (h)"]), 1, 1, "C")

    return bytes(pdf.output())


def render_header() -> None:
    status_badge = (
        '<span style="background:#065f46; color:#a7f3d0; padding:4px 12px; border-radius:999px; font-size:12px;">🖥️ Serveur Local PC (Stockage disque uniquement)</span>'
    )
    st.markdown(
        f"""
        <div style="background: linear-gradient(90deg, #0f172a 0%, #1e3a8a 100%); padding: 18px 24px; border-radius: 10px; color: white; margin-bottom: 18px; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px;">
            <div>
                <h2 style="margin:0; font-size:20px; letter-spacing:0.5px;">SOCIÉTÉ SLEEM HAYET | SERVICE TECHNIQUE</h2>
                <p style="margin:4px 0 0 0; color:#cbd5e1; font-size:13px;">Système de Gestion de Maintenance Assistée par Ordinateur (GMAO) — Serveur Local PC</p>
            </div>
            <div>{status_badge}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ==============================================================================
# INTERFACE UTILISATEUR PRINCIPALE
# ==============================================================================
def main() -> None:
    repo = GMAODataRepository()
    stock_repo = SparePartsRepository()

    interventions = repo.get_all()
    stock_parts = stock_repo.get_all_parts()

    render_header()

    pending_count = sum(
        1 for i in interventions
        if not (i.get("sigDemandeur", False) and i.get("sigTech", False) and i.get("sigResp", False))
        and i.get("statut") == "Clôturée"
    )
    low_stock_count = sum(
        1 for p in stock_parts
        if int(p.get("quantite", 0)) <= int(p.get("seuilMin", 0))
    )
    stock_tab_label = (
        f"5. 📦 Magasin Pièces de Rechange (⚠️ {low_stock_count})"
        if low_stock_count > 0
        else "5. 📦 Magasin Pièces de Rechange"
    )

    tab_demandeur, tab_technicien, tab_historique, tab_stats, tab_magasin = st.tabs([
        "1. Nouvelle Demande (Demandeur)",
        f"2. Espace Technicien & Validation ({pending_count})",
        "3. Historique & Fiche Officielle Imprimable",
        "4. 📊 KPI, MTBF, MTTR & Stats Directeur",
        stock_tab_label,
    ])

    # --------------------------------------------------------------------------
    # ONGLET 1 : DEMANDEUR
    # --------------------------------------------------------------------------
    with tab_demandeur:
        st.subheader("🔐 Authentification Espace Demandeur")
        col_acc1, col_acc2 = st.columns(2)

        services_demandeurs = ["Production", "Technique", "Laboratoire"]
        profil_choisi = col_acc1.selectbox(
            "Sélectionnez votre service demandeur :",
            services_demandeurs,
            key="profil_demandeur_select"
        )
        pin_dem_saisi = col_acc2.text_input(
            "Mot de passe Demandeur :",
            type="password",
            placeholder="Entrez le mot de passe demandeur...",
            key="pin_demandeur_input"
        )

        acces_demandeur_autorise = False
        if pin_dem_saisi == PIN_DEMANDEUR:
            acces_demandeur_autorise = True
            st.success(f"✅ Accès Demandeur autorisé - Service : **{profil_choisi}**")
        elif pin_dem_saisi != "":
            st.error("❌ Mot de passe Demandeur incorrect.")

        st.divider()

        if not acces_demandeur_autorise:
            st.warning("Veuillez saisir le mot de passe Demandeur pour soumettre une demande d'intervention.")
        else:
            next_di = generate_di_number(interventions)
            c_title, c_num = st.columns([3, 1])
            with c_title:
                st.subheader(f"FICHE DE DEMANDE D’INTERVENTION ({profil_choisi})")
                st.caption("Remplissez les informations ci-dessous et signez à la souris.")
            with c_num:
                st.info(f"**N° DE DEMANDE AUTO**\n### `{next_di}`")

            if f"clear_canvas_dem_{next_di}" not in st.session_state:
                st.session_state[f"clear_canvas_dem_{next_di}"] = 0

            with st.form("form_nouvelle_demande", clear_on_submit=True):
                st.markdown("##### 1. Informations du demandeur")
                col1, col2, col3 = st.columns(3)
                d_date = col1.date_input("Date de la demande *", value=datetime.now())
                d_heure = col2.time_input("Heure de la demande *", value=datetime.now().time())
                d_service = col3.selectbox(
                    "Service demandeur *",
                    services_demandeurs,
                    index=services_demandeurs.index(profil_choisi)
                )

                col4, col5, col6 = st.columns(3)
                d_nom = col4.text_input("Nom et prénom *", placeholder="Ex: Hamza Ben Ali")
                d_poste = col5.text_input("Poste / Équipe *", placeholder="Ex: Chef d'équipe A")
                d_tel = col6.text_input("Téléphone / Poste interne", placeholder="Ex: 204 / 98 171 000")

                st.divider()
                st.markdown("##### 2. Identification de l’équipement")
                col7, col8 = st.columns(2)
                d_machine = col7.text_input("Machine / Équipement *", placeholder="Ex: Extrudeuse N°2")
                d_code = col8.text_input("Code machine", placeholder="Ex: EXT-02")

                col9, col10 = st.columns(2)
                d_ligne = col9.text_input("Ligne de production / Zone", placeholder="Ex: Ligne 1 / Salle B")
                d_localisation = col10.text_input("Localisation", placeholder="Ex: Atelier Principal")

                st.divider()
                st.markdown("##### 3. Description de l’anomalie & Photos")
                d_natures = st.multiselect(
                    "Nature de la demande *",
                    ["Mécanique", "Électrique", "Pneumatique", "Hydraulique", "Automatisme", "Entretien / Réglage", "Autre"],
                )
                d_description = st.text_area("Description détaillée du problème *", height=100)

                uploaded_files_demandeur = st.file_uploader(
                    "📸 Joindre des photos de la panne / pièce défectueuse",
                    type=["png", "jpg", "jpeg"],
                    accept_multiple_files=True,
                    key="uploader_demandeur_photos"
                )

                d_impact = st.radio(
                    "Impact sur la production *",
                    ["Arrêt total", "Arrêt partiel", "Fonctionnement dégradé", "Aucun impact"],
                    horizontal=True,
                )

                d_priorite = st.radio(
                    "Priorité de l'intervention *",
                    ["URGENTE", "HAUTE", "NORMALE", "FAIBLE"],
                    index=2,
                    horizontal=True,
                )

                st.markdown("##### 5. Signature Manuscrite du Demandeur (Dessinez avec la souris ci-dessous)")
                canvas_result_dem = st_canvas(
                    fill_color="rgba(255, 165, 0, 0.3)",
                    stroke_width=2,
                    stroke_color="#000000",
                    background_color="#FFFFFF",
                    height=150,
                    width=400,
                    drawing_mode="freedraw",
                    return_image_data=True,
                    key=f"canvas_dem_{next_di}_{st.session_state[f'clear_canvas_dem_{next_di}']}"
                )

                submitted = st.form_submit_button("Envoyer au Service Maintenance", type="primary", use_container_width=True)

            if st.button("🗑️ Effacer la signature", key=f"btn_clear_dem_{next_di}"):
                st.session_state[f"clear_canvas_dem_{next_di}"] += 1
                st.rerun()

            if submitted:
                if not d_nom.strip() or not d_poste.strip() or not d_machine.strip() or not d_description.strip():
                    st.error("Erreur : Veuillez renseigner tous les champs obligatoires (*).")
                else:
                    sig_data = save_canvas_signature(canvas_result_dem, "sig_demandeur", next_di)
                    sig_img_path = sig_data["path"]
                    sig_img_b64 = sig_data["base64"]

                    saved_photos_dem = []
                    if uploaded_files_demandeur:
                        for idx, uf in enumerate(uploaded_files_demandeur):
                            file_extension = uf.name.split(".")[-1]
                            file_name = f"demande_{next_di}_{int(time.time())}_{idx}.{file_extension}"
                            file_path = os.path.join(UPLOAD_DIR, file_name)
                            with open(file_path, "wb") as f:
                                f.write(uf.getbuffer())
                            saved_photos_dem.append(file_path)

                    payload = {
                        "numero": next_di,
                        "date": d_date.strftime("%Y-%m-%d"),
                        "heure": d_heure.strftime("%H:%M"),
                        "service": d_service,
                        "nom": d_nom.strip(),
                        "poste": d_poste.strip(),
                        "tel": d_tel.strip(),
                        "machine": d_machine.strip(),
                        "codeMachine": d_code.strip(),
                        "ligne": d_ligne.strip(),
                        "localisation": d_localisation.strip(),
                        "natures": d_natures,
                        "description": d_description.strip(),
                        "impact": d_impact,
                        "priorite": d_priorite,
                        "statut": "En attente",
                        "techniciens": "",
                        "priseEnCharge": "",
                        "duree": "",
                        "diagnostic": "",
                        "travaux": "",
                        "pieces": [],
                        "photosPieces": saved_photos_dem,
                        "etatEquipement": "",
                        "dateFin": "",
                        "observations": "",
                        "valDemandeur": d_nom.strip(),
                        "sigDemandeur": bool(sig_img_path or sig_img_b64),
                        "pathSigDemandeur": sig_img_path,
                        "b64SigDemandeur": sig_img_b64,
                        "valTech": "",
                        "sigTech": False,
                        "pathSigTech": "",
                        "b64SigTech": "",
                        "valResp": "",
                        "sigResp": False,
                        "pathSigResp": "",
                        "b64SigResp": "",
                        "createdAt": int(time.time() * 1000),
                    }
                    if repo.create(payload):
                        if not (sig_img_path or sig_img_b64):
                            st.warning(f"Demande {next_di} transmise, mais aucune signature n'a été tracée.")
                        else:
                            st.success(f"Demande {next_di} enregistrée sur le serveur local PC !")
                        time.sleep(0.8)
                        st.rerun()

    # --------------------------------------------------------------------------
    # ONGLET 2 : ESPACE TECHNICIEN (SORTIE DE STOCK CONTRÔLÉE <= DISPONIBLE)
    # --------------------------------------------------------------------------
    with tab_technicien:
        st.subheader("🔐 Espace Technique & Triple Validation Officielle")
        st.caption(
            "Sélectionnez votre profil (Technicien ou Responsable Technique) puis saisissez votre mot de passe. "
            "Seul le Responsable Technique peut signer la validation finale et déverrouiller un dossier."
        )

        col_m_s1, col_m_s2 = st.columns(2)
        role_tech_choisi = col_m_s1.selectbox(
            "Sélectionnez votre profil :",
            ["Technicien", "Responsable Technique"],
            key="select_role_espace_tech"
        )
        pin_espace_tech = col_m_s2.text_input(
            f"Mot de passe ({role_tech_choisi}) :",
            type="password",
            placeholder="Entrez votre mot de passe...",
            key="pin_espace_tech_input"
        )

        acces_tech = False
        acces_resp_directeur = False

        if pin_espace_tech != "":
            if role_tech_choisi == "Technicien" and pin_espace_tech == PIN_TECHNICIEN:
                acces_tech = True
                st.success("✅ Accès autorisé — Profil : **Technicien**")
            elif role_tech_choisi == "Responsable Technique" and pin_espace_tech == PIN_DIRECTEUR:
                acces_resp_directeur = True
                st.success("✅ Accès autorisé — Profil : **Responsable Technique**")
            else:
                st.error(f"❌ Mot de passe incorrect pour le profil **{role_tech_choisi}**.")

        if not acces_tech and not acces_resp_directeur:
            st.warning("Veuillez sélectionner votre profil et saisir votre mot de passe pour accéder aux dossiers d'intervention.")
        else:
            col_left, col_right = st.columns([1, 2])

            with col_left:
                st.subheader("Demandes reçues")
                status_filter = st.selectbox(
                    "Filtrer par statut :",
                    ["En attente / En cours", "Toutes", "Clôturées"],
                    key="filter_statut_tech"
                )

                filtered_items = [
                    item for item in interventions
                    if status_filter == "Toutes"
                    or (status_filter == "En attente / En cours" and item.get("statut") != "Clôturée")
                    or (status_filter == "Clôturées" and item.get("statut") == "Clôturée")
                ]

                if not filtered_items:
                    st.info("Aucune demande correspondante.")
                    selected_item = None
                else:
                    labels = {}
                    for i in filtered_items:
                        triple_valide = (
                            bool(i.get("sigDemandeur", False))
                            and bool(i.get("sigTech", False))
                            and bool(i.get("sigResp", False))
                        )
                        double_valide = bool(i.get("sigDemandeur", False)) and bool(i.get("sigTech", False))
                        if triple_valide:
                            badge_statut = "✅ [3/3 Validé]"
                        elif double_valide:
                            badge_statut = "🔏 [Attente Resp. Tech]"
                        elif i.get("statut") == "Clôturée":
                            badge_statut = "⏳ [En attente signature]"
                        else:
                            badge_statut = "🔄 [En cours]"
                        label_text = f"{i.get('numero')} | {i.get('machine')} {badge_statut} [{i.get('priorite')}]"
                        labels[label_text] = i

                    chosen_key = st.radio("Sélectionner un dossier :", list(labels.keys()))
                    selected_item = labels[chosen_key]

            with col_right:
                if selected_item:
                    st.info(
                        f"**Dossier : {selected_item.get('numero')}** — Priorité : **{selected_item.get('priorite')}**\n\n"
                        f"**Équipement :** {selected_item.get('machine')} ({selected_item.get('codeMachine') or 'Sans code'})\n\n"
                        f"**Demandeur :** {selected_item.get('nom')} ({selected_item.get('service')}) | **Impact :** {selected_item.get('impact')}\n\n"
                        f"**Anomalie signalée :** {selected_item.get('description')}"
                    )

                    deja_valide_dem = bool(selected_item.get("sigDemandeur", False))
                    deja_valide_tech = bool(selected_item.get("sigTech", False))
                    is_locked = deja_valide_dem and deja_valide_tech

                    if is_locked:
                        if acces_resp_directeur:
                            st.success("🔓 [Mode Responsable Technique Actif] Vous pouvez signer et modifier ce dossier.")
                        else:
                            st.warning(
                                "🔒 **Rapport technicien verrouillé.** Seul le Responsable Technique "
                                "peut signer la validation finale ou modifier ce dossier."
                            )

                    form_disabled = is_locked and not acces_resp_directeur
                    resp_section_disabled = not acces_resp_directeur

                    photos_existantes = selected_item.get("photosPieces", [])
                    if photos_existantes:
                        st.markdown("##### 📷 Photos initiales envoyées par le demandeur :")
                        cols_p = st.columns(min(len(photos_existantes), 3))
                        for idx_p, p_path in enumerate(photos_existantes):
                            if os.path.exists(p_path):
                                with cols_p[idx_p % len(cols_p)]:
                                    st.image(p_path, caption=f"Photo demandeur {idx_p+1}", use_container_width=True)

                    pieces_state_key = f"pieces_list_{selected_item.get('firebaseId')}"
                    if pieces_state_key not in st.session_state:
                        st.session_state[pieces_state_key] = list(selected_item.get("pieces", []))

                    with st.expander("📦 Gestion des pièces de rechange utilisées (Sortie Magasin & Hors-Stock)", expanded=not form_disabled):
                        mode_piece = st.radio(
                            "Source de la pièce :",
                            ["Prélever depuis le Magasin", "Saisie libre (hors magasin)"],
                            horizontal=True,
                            key=f"mode_piece_{selected_item.get('firebaseId')}",
                            disabled=form_disabled
                        )

                        if mode_piece == "Prélever depuis le Magasin":
                            if not stock_parts:
                                st.info("Le magasin est vide. Ajoutez des articles dans l'onglet 5 ou utilisez la saisie libre.")
                            else:
                                mag_options = {
                                    f"{p.get('designation')} [Code: {p.get('reference') or '-'}] — Dispo: {p.get('quantite', 0)} {p.get('unite', 'pcs')}": p
                                    for p in stock_parts
                                }
                                mp1, mp2, mp3 = st.columns([5, 2, 2])
                                chosen_mag_label = mp1.selectbox(
                                    "Sélectionner une pièce du magasin",
                                    list(mag_options.keys()),
                                    key=f"sel_mag_{selected_item.get('firebaseId')}",
                                    disabled=form_disabled
                                )
                                chosen_part = mag_options[chosen_mag_label]

                                deja_reserve = sum(
                                    int(item_p.get("quantite", 0))
                                    for item_p in st.session_state[pieces_state_key]
                                    if item_p.get("partId") == chosen_part.get("id") and not item_p.get("deducted", False)
                                )
                                max_dispo = max(0, int(chosen_part.get("quantite", 0)) - deja_reserve)

                                if max_dispo < 1:
                                    mp2.warning("Stock épuisé (0)")
                                else:
                                    mag_qte = mp2.number_input(
                                        f"Qté (1 à {max_dispo})",
                                        min_value=1,
                                        max_value=max_dispo,
                                        value=1,
                                        step=1,
                                        key=f"qte_mag_{selected_item.get('firebaseId')}",
                                        disabled=form_disabled
                                    )
                                    if not form_disabled and mp3.button(
                                        "Ajouter",
                                        key=f"btn_add_mag_{selected_item.get('firebaseId')}"
                                    ):
                                        if mag_qte < 1 or mag_qte > max_dispo:
                                            st.error(f"Quantité invalide : doit être entre 1 et {max_dispo}.")
                                        else:
                                            st.session_state[pieces_state_key].append({
                                                "designation": chosen_part.get("designation", ""),
                                                "reference": chosen_part.get("reference", ""),
                                                "quantite": str(int(mag_qte)),
                                                "partId": chosen_part.get("id", ""),
                                                "deducted": False,
                                            })
                                            st.rerun()
                        else:
                            p1, p2, p3, p4 = st.columns([4, 3, 2, 2])
                            new_des = p1.text_input("Désignation", key=f"des_{selected_item.get('firebaseId')}", disabled=form_disabled)
                            new_ref = p2.text_input("Code article / Réf.", key=f"ref_{selected_item.get('firebaseId')}", disabled=form_disabled)
                            new_qte = p3.number_input("Qté", min_value=1, value=1, key=f"qte_{selected_item.get('firebaseId')}", disabled=form_disabled)
                            if not form_disabled and p4.button("Ajouter", key=f"btn_add_{selected_item.get('firebaseId')}"):
                                if new_des.strip():
                                    st.session_state[pieces_state_key].append({
                                        "designation": new_des.strip(),
                                        "reference": new_ref.strip(),
                                        "quantite": str(int(new_qte)),
                                        "partId": "",
                                        "deducted": False,
                                    })
                                    st.rerun()

                        for idx, p in enumerate(st.session_state[pieces_state_key]):
                            row_c1, row_c2 = st.columns([6, 1])
                            badge_mag = " 📦 [Magasin]" if p.get("partId") else ""
                            row_c1.write(
                                f"- **{p.get('designation')}** (Code : {p.get('reference') or '-'}) — Quantité : `{p.get('quantite')}`{badge_mag}"
                            )
                            if not form_disabled and not p.get("deducted", False):
                                if row_c2.button("Suppr.", key=f"del_{selected_item.get('firebaseId')}_{idx}"):
                                    st.session_state[pieces_state_key].pop(idx)
                                    st.rerun()

                    uploaded_files_tech = st.file_uploader(
                        "📸 Ajouter des photos supplémentaires (travaux / pièce réparée)",
                        type=["png", "jpg", "jpeg"],
                        accept_multiple_files=True,
                        key=f"photos_tech_{selected_item.get('firebaseId')}",
                        disabled=form_disabled
                    )

                    saved_photos = list(selected_item.get("photosPieces", []))
                    if uploaded_files_tech and not form_disabled:
                        for idx, uf in enumerate(uploaded_files_tech):
                            file_extension = uf.name.split(".")[-1]
                            file_name = f"tech_{selected_item.get('numero')}_{int(time.time())}_{idx}.{file_extension}"
                            file_path = os.path.join(UPLOAD_DIR, file_name)
                            with open(file_path, "wb") as f:
                                f.write(uf.getbuffer())
                            if file_path not in saved_photos:
                                saved_photos.append(file_path)

                    can_key_tech = f"clear_canvas_tech_{selected_item.get('firebaseId')}"
                    can_key_resp = f"clear_canvas_resp_{selected_item.get('firebaseId')}"
                    if can_key_tech not in st.session_state:
                        st.session_state[can_key_tech] = 0
                    if can_key_resp not in st.session_state:
                        st.session_state[can_key_resp] = 0

                    with st.form(f"form_tech_{selected_item.get('firebaseId')}"):
                        st.markdown("##### 5. Rapport du Service Technique")
                        t1, t2, t3 = st.columns(3)
                        t_tech = t1.text_input("Technicien(s) affecté(s) *", value=selected_item.get("techniciens", ""), disabled=form_disabled)

                        t_prise_date = t2.date_input("Date prise en charge", value=datetime.now().date(), disabled=form_disabled)
                        t_prise_heure = t3.time_input("Heure prise en charge", value=datetime.now().time(), disabled=form_disabled)
                        t_prise_str = f"{t_prise_date.strftime('%Y-%m-%d')} à {t_prise_heure.strftime('%H:%M')}"

                        st.markdown("###### Durée de l'intervention")
                        d_col1, d_col2 = st.columns(2)
                        d_heures = d_col1.number_input("Heures", min_value=0, max_value=72, value=0, step=1, disabled=form_disabled)
                        d_minutes = d_col2.number_input("Minutes", min_value=0, max_value=59, value=30, step=5, disabled=form_disabled)
                        t_duree_str = f"{d_heures}h {d_minutes:02d}min"

                        t_diag = st.text_area("Diagnostic de la panne *", value=selected_item.get("diagnostic", ""), disabled=form_disabled)
                        t_travaux = st.text_area("Travaux réalisés *", value=selected_item.get("travaux", ""), disabled=form_disabled)

                        st.markdown("##### 6. Clôture de l’intervention")
                        etats_possibles = ["Remis en service", "Fonctionnement provisoire", "Non terminée", "Pièces à commander"]
                        etat_actuel = selected_item.get("etatEquipement")
                        default_idx = etats_possibles.index(etat_actuel) if etat_actuel in etats_possibles else 0
                        t_etat = st.radio("État final de l'équipement *", etats_possibles, index=default_idx, horizontal=True, disabled=form_disabled)

                        c_fin1, c_fin2, c_obs = st.columns([1, 1, 2])
                        t_fin_date = c_fin1.date_input("Date de fin", value=datetime.now().date(), disabled=form_disabled)
                        t_fin_heure = c_fin2.time_input("Heure de fin", value=datetime.now().time(), disabled=form_disabled)
                        t_fin_str = f"{t_fin_date.strftime('%Y-%m-%d')} à {t_fin_heure.strftime('%H:%M')}"

                        t_obs = c_obs.text_input("Observations complémentaires", value=selected_item.get("observations", ""), disabled=form_disabled)

                        st.markdown("##### 7. Validation et Signatures Officielles (Demandeur, Technicien & Responsable Technique)")
                        v1, v2, v3 = st.columns(3)
                        with v1:
                            val_dem = st.text_input("Nom du Demandeur (Validation 1)", value=selected_item.get("valDemandeur", ""), disabled=form_disabled)
                            sig_dem = st.checkbox("✅ Validation & Signature Demandeur", value=bool(selected_item.get("sigDemandeur", False)), disabled=form_disabled)
                            render_signature_image(
                                selected_item.get("pathSigDemandeur", ""),
                                selected_item.get("b64SigDemandeur", ""),
                                caption="Signature Demandeur enregistrée",
                                width=170
                            )
                        with v2:
                            val_tech_sign = st.text_input("Nom du Technicien (Validation 2)", value=selected_item.get("valTech", t_tech), disabled=form_disabled)
                            sig_tech = st.checkbox("✅ Validation & Signature Technicien", value=bool(selected_item.get("sigTech", False)), disabled=form_disabled)
                            render_signature_image(
                                selected_item.get("pathSigTech", ""),
                                selected_item.get("b64SigTech", ""),
                                caption="Signature Technicien enregistrée",
                                width=170
                            )
                            st.caption("✍️ Signature Technicien")
                            canvas_result_tech = st_canvas(
                                fill_color="rgba(255, 165, 0, 0.3)",
                                stroke_width=2,
                                stroke_color="#000000",
                                background_color="#FFFFFF",
                                height=110,
                                width=250,
                                drawing_mode="freedraw",
                                disabled=form_disabled,
                                return_image_data=True,
                                key=f"canvas_tech_{selected_item.get('firebaseId')}_{st.session_state[can_key_tech]}"
                            )
                        with v3:
                            val_resp_sign = st.text_input(
                                "Nom du Responsable Technique (Validation 3)",
                                value=selected_item.get("valResp", ""),
                                disabled=resp_section_disabled,
                                help="Réservé exclusivement au Responsable Technique."
                            )
                            sig_resp = st.checkbox(
                                "✅ Validation & Signature Responsable Tech.",
                                value=bool(selected_item.get("sigResp", False)),
                                disabled=resp_section_disabled
                            )
                            render_signature_image(
                                selected_item.get("pathSigResp", ""),
                                selected_item.get("b64SigResp", ""),
                                caption="Signature Responsable Technique",
                                width=170
                            )
                            if resp_section_disabled:
                                st.caption("🔒 Réservé au Responsable Technique")
                                canvas_result_resp = None
                            else:
                                st.caption("✍️ Signature Responsable Technique")
                                canvas_result_resp = st_canvas(
                                    fill_color="rgba(255, 165, 0, 0.3)",
                                    stroke_width=2,
                                    stroke_color="#000000",
                                    background_color="#FFFFFF",
                                    height=110,
                                    width=250,
                                    drawing_mode="freedraw",
                                    disabled=False,
                                    return_image_data=True,
                                    key=f"canvas_resp_{selected_item.get('firebaseId')}_{st.session_state[can_key_resp]}"
                                )

                        submit_report = st.form_submit_button(
                            "Enregistrer et Mettre à jour le Rapport",
                            type="primary",
                            use_container_width=True,
                            disabled=form_disabled
                        )

                        if submit_report:
                            if form_disabled:
                                st.error("❌ Action interdite : Ce dossier est verrouillé.")
                            elif not t_tech.strip() or not t_diag.strip() or not t_travaux.strip():
                                st.error("Veuillez remplir le technicien, le diagnostic et les travaux réalisés (*).")
                            else:
                                stock_error = False
                                final_pieces_list = []
                                fresh_parts = {p["id"]: p for p in stock_repo.get_all_parts()}

                                for p_item in st.session_state[pieces_state_key]:
                                    p_copy = dict(p_item)
                                    part_id = p_copy.get("partId", "")
                                    if part_id and not p_copy.get("deducted", False):
                                        qte_used = int(p_copy.get("quantite", 1))
                                        avail_now = int(fresh_parts.get(part_id, {}).get("quantite", 0))
                                        if qte_used < 1 or qte_used > avail_now:
                                            st.error(
                                                f"❌ Stock insuffisant pour '{p_copy.get('designation')}' "
                                                f"(demandé : {qte_used}, disponible : {avail_now})."
                                            )
                                            stock_error = True
                                            break
                                        ok_deduct = stock_repo.adjust_stock(
                                            part_id=part_id,
                                            delta_qty=-qte_used,
                                            mvt_type="SORTIE (Intervention)",
                                            motif=f"Bon {selected_item.get('numero')} - {selected_item.get('machine')}",
                                            acteur=t_tech.strip()
                                        )
                                        if ok_deduct:
                                            p_copy["deducted"] = True
                                        else:
                                            stock_error = True
                                            break
                                    final_pieces_list.append(p_copy)

                                if not stock_error:
                                    st.session_state[pieces_state_key] = final_pieces_list

                                    sig_tech_data = save_canvas_signature(
                                        canvas_result_tech, "sig_tech", selected_item.get("numero", "DI")
                                    )
                                    new_path_sig_tech = sig_tech_data["path"] or selected_item.get("pathSigTech", "")
                                    new_b64_sig_tech = sig_tech_data["base64"] or selected_item.get("b64SigTech", "")

                                    if acces_resp_directeur:
                                        sig_resp_data = save_canvas_signature(
                                            canvas_result_resp, "sig_resp", selected_item.get("numero", "DI")
                                        )
                                        final_val_resp = val_resp_sign.strip()
                                        final_path_sig_resp = sig_resp_data["path"] or selected_item.get("pathSigResp", "")
                                        final_b64_sig_resp = sig_resp_data["base64"] or selected_item.get("b64SigResp", "")
                                        final_sig_resp = sig_resp or bool(final_path_sig_resp or final_b64_sig_resp)
                                    else:
                                        final_val_resp = selected_item.get("valResp", "")
                                        final_path_sig_resp = selected_item.get("pathSigResp", "")
                                        final_b64_sig_resp = selected_item.get("b64SigResp", "")
                                        final_sig_resp = bool(selected_item.get("sigResp", False))

                                    statut_final = "Clôturée" if t_etat in ["Remis en service", "Fonctionnement provisoire"] else "En cours"
                                    updates = {
                                        "techniciens": t_tech.strip(),
                                        "priseEnCharge": t_prise_str,
                                        "duree": t_duree_str,
                                        "diagnostic": t_diag.strip(),
                                        "travaux": t_travaux.strip(),
                                        "pieces": final_pieces_list,
                                        "photosPieces": saved_photos,
                                        "etatEquipement": t_etat,
                                        "dateFin": t_fin_str,
                                        "observations": t_obs.strip(),
                                        "valDemandeur": val_dem.strip(),
                                        "sigDemandeur": sig_dem,
                                        "valTech": val_tech_sign.strip(),
                                        "sigTech": sig_tech or bool(new_path_sig_tech or new_b64_sig_tech),
                                        "pathSigTech": new_path_sig_tech,
                                        "b64SigTech": new_b64_sig_tech,
                                        "valResp": final_val_resp,
                                        "sigResp": final_sig_resp,
                                        "pathSigResp": final_path_sig_resp,
                                        "b64SigResp": final_b64_sig_resp,
                                        "statut": statut_final,
                                    }
                                    if repo.update(selected_item.get("firebaseId"), updates):
                                        st.success("Rapport et stock enregistrés sur le serveur local PC !")
                                        time.sleep(0.8)
                                        st.rerun()

                    if not form_disabled:
                        b_clr1, b_clr2 = st.columns(2)
                        with b_clr1:
                            if st.button("🗑️ Effacer signature technicien", key=f"btn_clear_tech_{selected_item.get('firebaseId')}"):
                                st.session_state[can_key_tech] += 1
                                st.rerun()
                        with b_clr2:
                            if acces_resp_directeur:
                                if st.button("🗑️ Effacer signature responsable technique", key=f"btn_clear_resp_{selected_item.get('firebaseId')}"):
                                    st.session_state[can_key_resp] += 1
                                    st.rerun()

    # --------------------------------------------------------------------------
    # ONGLET 3 : HISTORIQUE & FICHE OFFICIELLE PRO (EXPORT PDF AVEC PHOTOS)
    # --------------------------------------------------------------------------
    with tab_historique:
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Total Demandes", len(interventions))
        k2.metric("En attente / En cours", sum(1 for i in interventions if i.get("statut") != "Clôturée"))
        k3.metric("Clôturées", sum(1 for i in interventions if i.get("statut") == "Clôturée"))
        k4.metric("Priorité Urgente", sum(1 for i in interventions if i.get("priorite") == "URGENTE"))

        st.divider()
        s_col, exp_col = st.columns([3, 1])
        query = s_col.text_input("Rechercher par N° DI, équipement, demandeur ou service :")

        filtered_hist = [
            i for i in interventions
            if query.lower() in i.get("numero", "").lower()
            or query.lower() in i.get("machine", "").lower()
            or query.lower() in i.get("nom", "").lower()
            or query.lower() in i.get("service", "").lower()
        ]

        if filtered_hist:
            df = pd.DataFrame(filtered_hist)
            display_cols = ["numero", "date", "service", "nom", "machine", "priorite", "techniciens", "statut"]

            df_clean = df[[c for c in display_cols if c in df.columns]].copy()

            def format_validation_status(row):
                if row.get("sigDemandeur") and row.get("sigTech") and row.get("sigResp"):
                    return "✅ Validé (3/3)"
                elif row.get("sigDemandeur") and row.get("sigTech"):
                    return "🔏 Double Validé (Attente Resp.)"
                return "⏳ En attente"

            df_clean["Validation"] = df.apply(format_validation_status, axis=1)

            df_clean = df_clean.rename(columns={
                "numero": "N° DI",
                "date": "Date",
                "service": "Service",
                "nom": "Demandeur",
                "machine": "Équipement",
                "priorite": "Priorité",
                "techniciens": "Technicien",
                "statut": "Statut GMAO",
            })

            def colorer_lignes(row):
                val = row.get("Validation", "")
                if "✅ Validé (3/3)" in val:
                    return ['background-color: #d1fae5; color: #065f46'] * len(row)
                elif "Double Validé" in val:
                    return ['background-color: #dbeafe; color: #1e40af'] * len(row)
                else:
                    return ['background-color: #fef3c7; color: #92400e'] * len(row)

            df_styled = df_clean.style.apply(colorer_lignes, axis=1)
            st.dataframe(df_styled, use_container_width=True, hide_index=True)

            csv_bytes = df_clean.to_csv(index=False, sep=";").encode("utf-8-sig")
            exp_col.download_button(
                label="Exporter en CSV (Excel)",
                data=csv_bytes,
                file_name=f"GMAO_Sleem_Hayet_{datetime.now().strftime('%Y%m%d')}.csv",
                mime="text/csv",
                use_container_width=True,
            )

            st.divider()
            st.subheader("Fiche Officielle d'Intervention")
            fiche_map = {f"{i.get('numero')} - {i.get('machine')} ({i.get('date')})": i for i in filtered_hist}
            selected_fiche_label = st.selectbox("Sélectionner la fiche à visualiser / imprimer :", list(fiche_map.keys()))
            fiche = fiche_map[selected_fiche_label]

            with st.container(border=True):
                st.markdown(
                    f"""
                    <div style="background: #ffffff; border-bottom: 3px solid #1e3a8a; padding-bottom: 15px; margin-bottom: 20px; text-align: center;">
                        <h2 style="margin: 5px 0 0 0; color: #1e3a8a; font-size: 22px; font-weight: 700;">SOCIÉTÉ SLEEM HAYET</h2>
                        <p style="margin: 2px 0 0 0; color: #475569; font-size: 13px; font-weight: 600;">SERVICE TECHNIQUE & MAINTENANCE INDUSTRIELLE</p>
                        <div style="margin-top: 10px; display: inline-block; background: #f1f5f9; padding: 6px 14px; border-radius: 6px; border: 1px solid #cbd5e1; font-size: 13px; font-weight: bold; color: #0f172a;">
                            Bon d'Intervention N° : {fiche.get('numero')} | Date : {fiche.get('date')} à {fiche.get('heure')}
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True
                )

                col_f1, col_f2 = st.columns(2)
                with col_f1:
                    st.markdown("##### 1. Demandeur")
                    st.write(f"- **Nom :** {fiche.get('nom')} ({fiche.get('poste')})")
                    st.write(f"- **Service :** {fiche.get('service')}")
                    st.write(f"- **Téléphone :** {fiche.get('tel') or '-'}")
                with col_f2:
                    st.markdown("##### 2. Équipement")
                    st.write(f"- **Machine :** {fiche.get('machine')} (`{fiche.get('codeMachine') or 'Sans code'}`)")
                    st.write(f"- **Ligne / Zone :** {fiche.get('ligne') or '-'}")
                    st.write(f"- **Localisation :** {fiche.get('localisation') or '-'}")

                st.divider()
                st.markdown("##### 3 & 4. Anomalie & Gravité")
                st.write(f"- **Nature :** {', '.join(fiche.get('natures', [])) or '-'}")
                st.info(f"**Description :** {fiche.get('description')}")
                st.write(f"- **Impact Production :** {fiche.get('impact')} | **Priorité :** {fiche.get('priorite')}")

                st.divider()
                st.markdown("##### 5. Rapport Technique")
                st.write(f"- **Technicien(s) :** {fiche.get('techniciens') or 'Non assigné'}")
                st.write(f"- **Prise en charge :** {fiche.get('priseEnCharge') or '-'}")
                st.write(f"- **Durée :** {fiche.get('duree') or '-'}")
                st.success(f"**Diagnostic :**\n\n{fiche.get('diagnostic') or 'Aucun diagnostic'}")
                st.success(f"**Travaux Réalisés :**\n\n{fiche.get('travaux') or 'Aucun travail'}")

                st.markdown("##### Pièces de rechange")
                if fiche.get("pieces"):
                    df_pieces = pd.DataFrame(fiche.get("pieces"))[["designation", "reference", "quantite"]]
                    df_pieces.columns = ["Désignation", "Code Article / Réf.", "Quantité"]
                    st.dataframe(df_pieces, use_container_width=True, hide_index=True)
                else:
                    st.write("_Aucune pièce de rechange utilisée._")

                st.markdown("##### 📸 Galerie Photos (Demande & Technique)")
                photos_list = fiche.get("photosPieces", [])
                valid_photos = [p for p in photos_list if os.path.exists(p)]

                if valid_photos:
                    cols = st.columns(min(len(valid_photos), 3))
                    for idx, p_path in enumerate(valid_photos):
                        with cols[idx % len(cols)]:
                            st.image(p_path, caption=f"Photo {idx+1}", use_container_width=True)
                else:
                    st.info("Aucune photo jointe pour cette intervention.")

                st.divider()
                st.markdown("##### 6. Clôture de l'intervention")
                st.write(f"- **État de l'équipement :** {fiche.get('etatEquipement') or fiche.get('statut')}")
                st.write(f"- **Date / Heure de fin :** {fiche.get('dateFin') or '-'}")
                st.write(f"- **Observations :** {fiche.get('observations') or 'Aucune'}")

                st.divider()
                st.markdown("##### 7. Signatures Manuscrites Officielles (Triple Validation)")
                v_col1, v_col2, v_col3 = st.columns(3)
                with v_col1:
                    st.markdown(f"**Demandeur : {fiche.get('valDemandeur') or '-'}**")
                    render_signature_image(
                        fiche.get("pathSigDemandeur", ""),
                        fiche.get("b64SigDemandeur", ""),
                        caption="Signature Demandeur",
                        width=190
                    )
                with v_col2:
                    st.markdown(f"**Technicien : {fiche.get('valTech') or '-'}**")
                    render_signature_image(
                        fiche.get("pathSigTech", ""),
                        fiche.get("b64SigTech", ""),
                        caption="Signature Technicien",
                        width=190
                    )
                with v_col3:
                    st.markdown(f"**Responsable Technique : {fiche.get('valResp') or '-'}**")
                    render_signature_image(
                        fiche.get("pathSigResp", ""),
                        fiche.get("b64SigResp", ""),
                        caption="Signature Responsable Technique",
                        width=190
                    )

            st.write("")
            c_print, c_del = st.columns([1, 1])
            with c_print:
                pdf_bon_bytes = build_bon_intervention_pdf(fiche)
                st.download_button(
                    label="🖨️ Télécharger le Bon d'Intervention (PDF avec photos)",
                    data=pdf_bon_bytes,
                    file_name=f"Bon_Intervention_{fiche.get('numero', 'DI')}.pdf",
                    mime="application/pdf",
                    type="primary",
                    use_container_width=True,
                )
            with c_del:
                if st.button("🗑️ Supprimer cette fiche", type="secondary", use_container_width=True):
                    pin_verification = st.text_input("Confirmez avec le mot de passe directeur pour supprimer :", type="password", key="del_pin")
                    if pin_verification == PIN_DIRECTEUR:
                        if repo.delete(fiche.get("firebaseId")):
                            st.warning("Fiche supprimée du serveur local PC.")
                            time.sleep(0.8)
                            st.rerun()

    # --------------------------------------------------------------------------
    # ONGLET 4 : KPI, MTBF, MTTR & STATISTIQUES DIRECTEUR
    # --------------------------------------------------------------------------
    with tab_stats:
        st.subheader("📊 Tableau de Bord & Indicateurs de Performance (KPI)")
        st.caption("Vue réservée au Directeur Technique : Analyse des pannes, MTBF, MTTR, graphiques et export PDF.")

        pin_dir = st.text_input("Mot de passe Directeur Technique requis :", type="password", placeholder="Entrez le mot de passe...", key="pin_directeur_stats")

        acces_directeur_autorise = False
        if pin_dir == PIN_DIRECTEUR:
            acces_directeur_autorise = True
        elif pin_dir != "":
            st.error("❌ Mot de passe Directeur incorrect.")

        if acces_directeur_autorise:
            if not interventions:
                st.info("Aucune donnée d'intervention disponible pour calculer les KPI.")
            else:
                total_interventions = len(interventions)
                cloturees = [i for i in interventions if i.get("statut") == "Clôturée"]
                nb_cloturees = len(cloturees)

                total_heures_reparation = sum([parse_duree_en_heures(i.get("duree", "0h 0min")) for i in cloturees])
                mttr_global = (total_heures_reparation / nb_cloturees) if nb_cloturees > 0 else 0.0
                taux_cloture = (nb_cloturees / total_interventions * 100) if total_interventions > 0 else 0

                col_m1, col_m2, col_m3, col_m4 = st.columns(4)
                col_m1.metric("Total Interventions", total_interventions)
                col_m2.metric("Interventions Clôturées", nb_cloturees)
                col_m3.metric("MTTR Global Moyen", f"{mttr_global:.2f} h")
                col_m4.metric("Taux de Clôture", f"{taux_cloture:.1f}%")

                st.divider()

                machine_data = {}
                TEMPS_TOTAL_PERIODE = 720.0

                for i in interventions:
                    m_name = i.get("machine", "Inconnu")
                    if m_name not in machine_data:
                        machine_data[m_name] = {"pannes": 0, "duree_totale": 0.0}
                    machine_data[m_name]["pannes"] += 1
                    machine_data[m_name]["duree_totale"] += parse_duree_en_heures(i.get("duree", "0h 30min"))

                kpi_machines = []
                for m, stats in machine_data.items():
                    nb_p = stats["pannes"]
                    d_tot = stats["duree_totale"]
                    mttr_m = (d_tot / nb_p) if nb_p > 0 else 0.0
                    mtbf_m = ((TEMPS_TOTAL_PERIODE - d_tot) / nb_p) if nb_p > 0 else TEMPS_TOTAL_PERIODE
                    disp_m = (mtbf_m / (mtbf_m + mttr_m) * 100) if (mtbf_m + mttr_m) > 0 else 100.0

                    kpi_machines.append({
                        "Équipement": m,
                        "Nombre de Pannes": nb_p,
                        "Temps d'Arrêt Total (h)": round(d_tot, 2),
                        "MTTR (h)": round(mttr_m, 2),
                        "MTBF (h)": round(mtbf_m, 2),
                        "Disponibilité (%)": round(disp_m, 2)
                    })

                df_kpi_machine = pd.DataFrame(kpi_machines)

                tech_data = {}
                for i in cloturees:
                    t_names = i.get("techniciens", "Non assigné")
                    for t in t_names.split(","):
                        t_clean = t.strip()
                        if not t_clean:
                            continue
                        if t_clean not in tech_data:
                            tech_data[t_clean] = {"interventions": 0, "heures_totales": 0.0}
                        tech_data[t_clean]["interventions"] += 1
                        tech_data[t_clean]["heures_totales"] += parse_duree_en_heures(i.get("duree", "0h"))

                kpi_techs = []
                for t, t_stats in tech_data.items():
                    nb_int = t_stats["interventions"]
                    h_tot = t_stats["heures_totales"]
                    m_interv = (h_tot / nb_int) if nb_int > 0 else 0.0
                    kpi_techs.append({
                        "Technicien": t,
                        "Interventions Clôturées": nb_int,
                        "Temps Total Passé (h)": round(h_tot, 2),
                        "Temps Moyen / Intervention (h)": round(m_interv, 2)
                    })

                df_kpi_tech = pd.DataFrame(kpi_techs) if kpi_techs else pd.DataFrame(
                    columns=["Technicien", "Interventions Clôturées", "Temps Total Passé (h)", "Temps Moyen / Intervention (h)"]
                )

                st.markdown("### ⚙️ Analyse par Équipement (MTBF, MTTR & Disponibilité)")
                st.dataframe(df_kpi_machine, use_container_width=True, hide_index=True)

                st.markdown("### 👨‍🔧 Performance et Résultats des Techniciens")
                if not df_kpi_tech.empty:
                    st.dataframe(df_kpi_tech, use_container_width=True, hide_index=True)
                else:
                    st.info("Aucune intervention clôturée assignée à un technicien.")

                st.divider()

                st.markdown("### 📈 Visualisations Graphiques")
                col_g1, col_g2 = st.columns(2)

                with col_g1:
                    st.markdown("##### Fréquence des pannes par Équipement")
                    if not df_kpi_machine.empty:
                        st.bar_chart(df_kpi_machine.set_index("Équipement")["Nombre de Pannes"])

                with col_g2:
                    st.markdown("##### Interventions clôturées par Technicien")
                    if not df_kpi_tech.empty:
                        st.bar_chart(df_kpi_tech.set_index("Technicien")["Interventions Clôturées"])
                    else:
                        st.info("Données insuffisantes pour le graphique des techniciens.")

                st.divider()

                st.markdown("### 📥 Exportation du Rapport Directeur")
                pdf_bytes = generate_pdf_kpi_report(
                    total_interventions, nb_cloturees, mttr_global, taux_cloture, df_kpi_machine, df_kpi_tech
                )
                st.download_button(
                    label="📄 Télécharger le Rapport KPI Complet en PDF",
                    data=pdf_bytes,
                    file_name=f"Rapport_KPI_Sleem_Hayet_{datetime.now().strftime('%Y%m%d')}.pdf",
                    mime="application/pdf",
                    type="primary",
                    use_container_width=True
                )

    # --------------------------------------------------------------------------
    # ONGLET 5 : MAGASIN DES PIÈCES DE RECHANGE & GESTION DES STOCKS
    # --------------------------------------------------------------------------
    with tab_magasin:
        st.subheader("📦 Magasin des Pièces de Rechange & Gestion des Stocks")
        st.caption("Sélectionnez votre profil d'accès pour consulter ou gérer le stock du magasin.")

        col_auth_m1, col_auth_m2 = st.columns(2)
        role_magasin = col_auth_m1.selectbox(
            "Profil d'accès :",
            ["Responsable Magasin", "Responsable Technique", "Technicien Maintenance"],
            key="select_role_magasin"
        )
        pin_mag = col_auth_m2.text_input(
            "Mot de passe :",
            type="password",
            placeholder="Saisissez votre mot de passe...",
            key="pin_magasin_input"
        )

        acces_valide = False
        can_edit_magasin = False

        if pin_mag != "":
            if role_magasin == "Responsable Magasin" and pin_mag == PIN_MAGASIN:
                acces_valide = True
                can_edit_magasin = True
            elif role_magasin == "Responsable Technique" and pin_mag == PIN_DIRECTEUR:
                acces_valide = True
                can_edit_magasin = True
            elif role_magasin == "Technicien Maintenance" and pin_mag == PIN_TECHNICIEN:
                acces_valide = True
                can_edit_magasin = False
            else:
                st.error("❌ Mot de passe incorrect pour le profil sélectionné.")

        if not acces_valide:
            st.info("🔒 Sélectionnez votre profil et entrez votre mot de passe pour ouvrir le magasin.")
        else:
            if can_edit_magasin:
                st.success(f"✅ Accès complet autorisé — Profil : **{role_magasin}**")
            else:
                st.info(f"👁️ Accès Consultation uniquement — Profil : **{role_magasin}** (Ajout, modification et suppression désactivés)")

            total_refs = len(stock_parts)
            total_unites = sum(int(p.get("quantite", 0)) for p in stock_parts)
            totals_by_curr: Dict[str, float] = {c: 0.0 for c in CURRENCIES}
            for p in stock_parts:
                if p.get("prixTotal") not in (None, ""):
                    curr = p.get("devise", "TND") if p.get("devise") in CURRENCIES else "TND"
                    totals_by_curr[curr] = totals_by_curr.get(curr, 0.0) + float(p.get("prixTotal"))

            en_alerte = [p for p in stock_parts if int(p.get("quantite", 0)) <= int(p.get("seuilMin", 0))]

            ms1, ms2, ms3 = st.columns(3)
            ms1.metric("Articles Référencés", total_refs)
            ms2.metric("Quantité Totale en Stock", total_unites)
            ms3.metric("Alertes Seuil Min / Rupture", len(en_alerte))

            val_cols = st.columns(3)
            val_cols[0].metric("Valeur Stock (TND)", f"{totals_by_curr['TND']:,.2f} TND")
            val_cols[1].metric("Valeur Stock (EUR)", f"{totals_by_curr['EUR']:,.2f} EUR")
            val_cols[2].metric("Valeur Stock (USD)", f"{totals_by_curr['USD']:,.2f} USD")

            if en_alerte:
                alert_names = ", ".join([f"{p.get('designation')} ({p.get('quantite', 0)})" for p in en_alerte])
                st.warning(f"⚠️ **Alerte Stock Minimum :** {alert_names}")

            st.divider()

            r_col1, r_col2, r_col3, r_col4 = st.columns([4, 2, 1, 1])
            search_part = r_col1.text_input(
                "🔍 Rechercher par Code article, Désignation, Fournisseur, Emplacement ou Machine :",
                placeholder="Tapez un mot-clé...",
                key="search_mag_part"
            )
            type_filter = r_col2.selectbox(
                "Type de pièce :",
                ["Tous", "Original", "Ancien", "Bricolage", "Modifié"],
                key="filter_type_mag"
            )
            curr_filter = r_col3.selectbox(
                "Devise :",
                ["Toutes"] + CURRENCIES,
                key="filter_curr_mag"
            )
            only_alerts = r_col4.checkbox("⚠️ Alertes", key="chk_alerts_mag")

            filtered_parts = []
            q_low = search_part.strip().lower()
            for p in stock_parts:
                p_curr = p.get("devise", "TND") if p.get("devise") in CURRENCIES else "TND"
                match_q = (
                    not q_low
                    or q_low in str(p.get("reference", "")).lower()
                    or q_low in str(p.get("designation", "")).lower()
                    or q_low in str(p.get("fournisseur", "")).lower()
                    or q_low in str(p.get("emplacement", "")).lower()
                    or q_low in str(p.get("machine", "")).lower()
                )
                match_t = (type_filter == "Tous" or p.get("typePiece", "Original") == type_filter)
                match_c = (curr_filter == "Toutes" or p_curr == curr_filter)
                is_low = int(p.get("quantite", 0)) <= int(p.get("seuilMin", 0))
                if match_q and match_t and match_c and (not only_alerts or is_low):
                    filtered_parts.append(p)

            if not filtered_parts:
                st.info("Aucune pièce trouvée avec ces critères de recherche.")
            else:
                rows_stock = []
                for p in filtered_parts:
                    qte = int(p.get("quantite", 0))
                    seuil = int(p.get("seuilMin", 0))
                    curr = p.get("devise", "TND") if p.get("devise") in CURRENCIES else "TND"
                    pu = p.get("prixUnitaire")
                    pt = p.get("prixTotal")
                    has_photo = bool(
                        (p.get("photoPath") and os.path.exists(p.get("photoPath")))
                        or p.get("photoBase64")
                    )
                    if qte == 0:
                        etat_stk = "🔴 Rupture"
                    elif qte <= seuil:
                        etat_stk = "🟠 Stock Bas"
                    else:
                        etat_stk = "🟢 Disponible"

                    rows_stock.append({
                        "Photo": "📷 Oui" if has_photo else "-",
                        "Code Article": p.get("reference") or "-",
                        "Désignation": p.get("designation", ""),
                        "Machine": p.get("machine") or "-",
                        "Emplacement": p.get("emplacement") or "-",
                        "Fournisseur": p.get("fournisseur") or "-",
                        "Type de pièce": p.get("typePiece", "Original"),
                        "Quantité": qte,
                        "Unité": p.get("unite", "pcs"),
                        "Seuil Min": seuil,
                        "Devise": curr,
                        "Prix Unitaire": f"{float(pu):.2f} {curr}" if pu not in (None, "") else "-",
                        "Prix Total": f"{float(pt):.2f} {curr}" if pt not in (None, "") else "-",
                        "État": etat_stk,
                    })

                df_stk = pd.DataFrame(rows_stock)

                def style_stock_rows(row):
                    st_val = row.get("État", "")
                    if "Rupture" in st_val:
                        return ['background-color: #fee2e2; color: #991b1b'] * len(row)
                    elif "Stock Bas" in st_val:
                        return ['background-color: #fef3c7; color: #92400e'] * len(row)
                    return ['background-color: #ecfdf5; color: #065f46'] * len(row)

                st.dataframe(df_stk.style.apply(style_stock_rows, axis=1), use_container_width=True, hide_index=True)

                parts_with_photo = [
                    p for p in filtered_parts
                    if (p.get("photoPath") and os.path.exists(p.get("photoPath"))) or p.get("photoBase64")
                ]
                if parts_with_photo:
                    with st.expander(f"🖼️ Aperçu Photos des Pièces ({len(parts_with_photo)})", expanded=False):
                        gal_cols = st.columns(min(len(parts_with_photo), 4))
                        for idx_g, p_g in enumerate(parts_with_photo):
                            with gal_cols[idx_g % len(gal_cols)]:
                                render_part_image(
                                    p_g.get("photoPath", ""),
                                    p_g.get("photoBase64", ""),
                                    caption=f"{p_g.get('reference') or '-'} | {p_g.get('designation', '')}",
                                    width=200
                                )

            b_imp1, b_imp2 = st.columns(2)
            with b_imp1:
                pdf_inv_bytes = build_inventaire_magasin_pdf(filtered_parts)
                st.download_button(
                    label="🖨️ Imprimer Inventaire (PDF)",
                    data=pdf_inv_bytes,
                    file_name=f"Inventaire_Magasin_Sleem_Hayet_{datetime.now().strftime('%Y%m%d')}.pdf",
                    mime="application/pdf",
                    type="primary",
                    use_container_width=True
                )
            with b_imp2:
                if filtered_parts:
                    csv_stock = df_stk.to_csv(index=False, sep=";").encode("utf-8-sig")
                    st.download_button(
                        label="📥 Exporter Inventaire (CSV / Excel)",
                        data=csv_stock,
                        file_name=f"Inventaire_Magasin_Sleem_Hayet_{datetime.now().strftime('%Y%m%d')}.csv",
                        mime="text/csv",
                        use_container_width=True
                    )

            if can_edit_magasin:
                st.divider()
                tab_op_add, tab_op_edit_del, tab_op_mvt = st.tabs([
                    "➕ Ajouter un Article",
                    "✏️ Modifier / 🗑️ Supprimer / Mouvement Stock",
                    "📜 Journal des Mouvements"
                ])

                with tab_op_add:
                    st.markdown("##### Ajouter une nouvelle pièce de rechange")
                    with st.form("form_nouvelle_piece", clear_on_submit=True):
                        a1, a2, a3 = st.columns(3)
                        p_des = a1.text_input("Désignation *", placeholder="Ex: Roulement SKF 6205-2RS")
                        p_code = a2.text_input("Code article *", placeholder="Ex: ART-6205")
                        p_machine = a3.text_input("Machine *", placeholder="Ex: Extrudeuse N°2")

                        a4, a5, a6 = st.columns(3)
                        p_emp = a4.text_input("Emplacement *", placeholder="Ex: Rayon B - Casier 04")
                        p_fourn = a5.text_input("Fournisseur", placeholder="Ex: Comptoir Industriel")
                        p_type = a6.selectbox(
                            "Type de pièce *",
                            ["Original", "Ancien", "Bricolage", "Modifié"]
                        )

                        a7, a8, a9 = st.columns(3)
                        p_qte = a7.number_input("Quantité *", min_value=0, value=1, step=1)
                        p_unite = a8.selectbox("Unité *", ["pcs", "m", "L", "kg", "boîte", "jeu"])
                        p_seuil = a9.number_input("Seuil d'alerte min *", min_value=0, value=2, step=1)

                        a10, a11, a12 = st.columns([1, 2, 2])
                        p_devise = a10.selectbox("Devise *", CURRENCIES, index=0)
                        p_pu_str = a11.text_input("Prix unitaire (optionnel)", placeholder="Ex: 45.50")
                        p_pt_str = a12.text_input("Prix total (optionnel - auto si vide)", placeholder="Laisser vide pour calcul auto")

                        p_photo_upload = st.file_uploader(
                            "📸 Photo de la pièce (optionnel / non obligatoire)",
                            type=["png", "jpg", "jpeg", "webp"],
                            key="uploader_piece_ajout"
                        )

                        btn_ajouter_art = st.form_submit_button("➕ Ajouter", type="primary", use_container_width=True)

                        if btn_ajouter_art:
                            if not p_des.strip() or not p_code.strip():
                                st.error("Erreur : La désignation et le code article sont obligatoires (*).")
                            else:
                                pu_val: Optional[float] = None
                                pt_val: Optional[float] = None
                                valid_prices = True

                                if p_pu_str.strip():
                                    try:
                                        pu_val = round(float(p_pu_str.replace(",", ".").strip()), 2)
                                    except ValueError:
                                        st.error("Le prix unitaire doit être un nombre valide.")
                                        valid_prices = False

                                if p_pt_str.strip():
                                    try:
                                        pt_val = round(float(p_pt_str.replace(",", ".").strip()), 2)
                                    except ValueError:
                                        st.error("Le prix total doit être un nombre valide.")
                                        valid_prices = False
                                elif pu_val is not None:
                                    pt_val = round(pu_val * int(p_qte), 2)

                                if valid_prices:
                                    photo_info = save_uploaded_part_photo(p_photo_upload, p_code.strip())
                                    new_part_payload = {
                                        "designation": p_des.strip(),
                                        "reference": p_code.strip(),
                                        "machine": p_machine.strip(),
                                        "emplacement": p_emp.strip(),
                                        "fournisseur": p_fourn.strip(),
                                        "typePiece": p_type,
                                        "quantite": int(p_qte),
                                        "unite": p_unite,
                                        "seuilMin": int(p_seuil),
                                        "devise": p_devise,
                                        "prixUnitaire": pu_val,
                                        "prixTotal": pt_val,
                                        "photoPath": photo_info["path"],
                                        "photoBase64": photo_info["base64"],
                                        "createdAt": int(time.time() * 1000),
                                    }
                                    if stock_repo.add_part(new_part_payload):
                                        if int(p_qte) > 0:
                                            stock_repo.log_movement({
                                                "partId": "",
                                                "reference": p_code.strip(),
                                                "designation": p_des.strip(),
                                                "type": "INITIALISATION",
                                                "quantite": int(p_qte),
                                                "stockAvant": 0,
                                                "stockApres": int(p_qte),
                                                "motif": "Création nouvel article",
                                                "acteur": role_magasin,
                                            })
                                        st.success(f"✅ Article '{p_des.strip()}' enregistré sur le serveur local PC !")
                                        time.sleep(0.8)
                                        st.rerun()

                with tab_op_edit_del:
                    if not stock_parts:
                        st.info("Aucun article disponible dans le catalogue.")
                    else:
                        part_selector = {
                            f"{p.get('reference') or '-'} | {p.get('designation')} (Machine: {p.get('machine') or '-'} | Stock: {p.get('quantite', 0)} {p.get('unite', 'pcs')} | {p.get('devise', 'TND')})": p
                            for p in stock_parts
                        }
                        sel_p_label = st.selectbox("Sélectionner un article :", list(part_selector.keys()), key="sel_part_edit_del")
                        target_part = part_selector[sel_p_label]

                        col_ed1, col_ed2 = st.columns([2, 1])

                        with col_ed1:
                            with st.form("form_modifier_piece"):
                                st.markdown("##### ✏️ Modifier l'article sélectionné")
                                m1, m2, m3 = st.columns(3)
                                ed_des = m1.text_input("Désignation *", value=target_part.get("designation", ""))
                                ed_code = m2.text_input("Code article *", value=target_part.get("reference", ""))
                                ed_machine = m3.text_input("Machine", value=target_part.get("machine", ""))

                                m4, m5, m6 = st.columns(3)
                                ed_emp = m4.text_input("Emplacement", value=target_part.get("emplacement", ""))
                                ed_fourn = m5.text_input("Fournisseur", value=target_part.get("fournisseur", ""))
                                types_list = ["Original", "Ancien", "Bricolage", "Modifié"]
                                cur_type = target_part.get("typePiece", "Original")
                                idx_type = types_list.index(cur_type) if cur_type in types_list else 0
                                ed_type = m6.selectbox("Type de pièce", types_list, index=idx_type)

                                m7, m8, m9 = st.columns(3)
                                ed_qte = m7.number_input("Quantité", min_value=0, value=int(target_part.get("quantite", 0)), step=1)
                                unites_list = ["pcs", "m", "L", "kg", "boîte", "jeu"]
                                cur_unite = target_part.get("unite", "pcs")
                                idx_unite = unites_list.index(cur_unite) if cur_unite in unites_list else 0
                                ed_unite = m8.selectbox("Unité", unites_list, index=idx_unite)
                                ed_seuil = m9.number_input("Seuil d'alerte min", min_value=0, value=int(target_part.get("seuilMin", 0)), step=1)

                                m10, m11, m12 = st.columns([1, 2, 2])
                                cur_curr = target_part.get("devise", "TND")
                                idx_curr = CURRENCIES.index(cur_curr) if cur_curr in CURRENCIES else 0
                                ed_devise = m10.selectbox("Devise", CURRENCIES, index=idx_curr)
                                cur_pu = target_part.get("prixUnitaire")
                                cur_pt = target_part.get("prixTotal")
                                ed_pu_str = m11.text_input("Prix unitaire (optionnel)", value=str(cur_pu) if cur_pu not in (None, "") else "")
                                ed_pt_str = m12.text_input("Prix total (optionnel)", value=str(cur_pt) if cur_pt not in (None, "") else "")

                                render_part_image(
                                    target_part.get("photoPath", ""),
                                    target_part.get("photoBase64", ""),
                                    caption="Photo actuelle de la pièce",
                                    width=160
                                )
                                ed_photo_upload = st.file_uploader(
                                    "📸 Remplacer / ajouter une photo (optionnel / non obligatoire)",
                                    type=["png", "jpg", "jpeg", "webp"],
                                    key=f"uploader_piece_modif_{target_part.get('id')}"
                                )
                                ed_remove_photo = st.checkbox("Supprimer la photo actuelle", value=False)

                                btn_modif = st.form_submit_button("✏️ Modifier", type="primary", use_container_width=True)

                                if btn_modif:
                                    if not ed_des.strip() or not ed_code.strip():
                                        st.error("La désignation et le code article sont obligatoires.")
                                    else:
                                        pu_val = None
                                        pt_val = None
                                        ok_p = True
                                        if ed_pu_str.strip():
                                            try:
                                                pu_val = round(float(ed_pu_str.replace(",", ".").strip()), 2)
                                            except ValueError:
                                                st.error("Prix unitaire invalide.")
                                                ok_p = False
                                        if ed_pt_str.strip():
                                            try:
                                                pt_val = round(float(ed_pt_str.replace(",", ".").strip()), 2)
                                            except ValueError:
                                                st.error("Prix total invalide.")
                                                ok_p = False
                                        elif pu_val is not None:
                                            pt_val = round(pu_val * int(ed_qte), 2)

                                        if ok_p:
                                            if ed_photo_upload is not None:
                                                p_info = save_uploaded_part_photo(ed_photo_upload, ed_code.strip())
                                                new_photo_path = p_info["path"]
                                                new_photo_b64 = p_info["base64"]
                                            elif ed_remove_photo:
                                                new_photo_path = ""
                                                new_photo_b64 = ""
                                            else:
                                                new_photo_path = target_part.get("photoPath", "")
                                                new_photo_b64 = target_part.get("photoBase64", "")

                                            if stock_repo.update_part(target_part["id"], {
                                                "designation": ed_des.strip(),
                                                "reference": ed_code.strip(),
                                                "machine": ed_machine.strip(),
                                                "emplacement": ed_emp.strip(),
                                                "fournisseur": ed_fourn.strip(),
                                                "typePiece": ed_type,
                                                "quantite": int(ed_qte),
                                                "unite": ed_unite,
                                                "seuilMin": int(ed_seuil),
                                                "devise": ed_devise,
                                                "prixUnitaire": pu_val,
                                                "prixTotal": pt_val,
                                                "photoPath": new_photo_path,
                                                "photoBase64": new_photo_b64,
                                                "updatedAt": int(time.time() * 1000),
                                            }):
                                                st.success("✅ Article modifié sur le serveur local PC !")
                                                time.sleep(0.7)
                                                st.rerun()

                        with col_ed2:
                            with st.form("form_entree_rapide"):
                                st.markdown("##### 📦 Entrée Stock (Réapprovisionnement)")
                                qte_entree = st.number_input("Quantité reçue (+)", min_value=1, value=1, step=1)
                                motif_entree = st.text_input("N° Bon de livraison / Motif", placeholder="Ex: BL-2026-14")
                                btn_entree = st.form_submit_button("Valider Entrée (+)", use_container_width=True)
                                if btn_entree:
                                    if stock_repo.adjust_stock(
                                        target_part["id"], int(qte_entree), "ENTRÉE", motif_entree.strip() or "Réapprovisionnement", role_magasin
                                    ):
                                        st.success("✅ Stock réapprovisionné sur le PC !")
                                        time.sleep(0.7)
                                        st.rerun()

                            st.divider()
                            st.markdown("##### 🗑️ Suppression")
                            if st.button("🗑️ Supprimer", type="secondary", use_container_width=True, key="btn_delete_part_mag"):
                                if stock_repo.delete_part(target_part["id"]):
                                    st.warning("🗑️ Article supprimé du serveur local PC.")
                                    time.sleep(0.7)
                                    st.rerun()

                with tab_op_mvt:
                    st.markdown("##### Historique des Entrées et Sorties de Stock")
                    mvts = stock_repo.get_movements()
                    if not mvts:
                        st.info("Aucun mouvement enregistré.")
                    else:
                        df_mvts = pd.DataFrame(mvts)
                        cols_mvt = ["dateHeure", "type", "reference", "designation", "quantite", "stockAvant", "stockApres", "motif", "acteur"]
                        df_mvts_clean = df_mvts[[c for c in cols_mvt if c in df_mvts.columns]].rename(columns={
                            "dateHeure": "Date & Heure",
                            "type": "Mouvement",
                            "reference": "Code Article",
                            "designation": "Désignation",
                            "quantite": "Qté",
                            "stockAvant": "Avant",
                            "stockApres": "Après",
                            "motif": "Motif / Bon DI",
                            "acteur": "Intervenant",
                        })
                        st.dataframe(df_mvts_clean, use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main()
