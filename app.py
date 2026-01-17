import streamlit as st
import os
import tempfile
import re
import io
import sys
import contextlib
import shutil
from datetime import datetime
from moviepy.editor import *
from moviepy.audio.fx.all import audio_loop
from PIL import Image, ImageOps
import numpy as np
import random
from proglog import ProgressBarLogger

# --- CONFIGURATION HYBRIDE (WINDOWS / CLOUD) ---
# Si on est sur Windows, on applique les correctifs
if sys.platform == 'win32':
    import asyncio
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except:
        pass
    
    # Détection Magick local (pour votre PC)
    from moviepy.config import change_settings
    local_magick = os.path.join(os.getcwd(), "magick", "magick.exe")
    if os.path.exists(local_magick):
        change_settings({"IMAGEMAGICK_BINARY": local_magick})

# Sur Linux (Cloud), on laisse faire la configuration par défaut (packages.txt)

# --- CONSTANTES ---
DUREE_TOTALE_VIDEO = 32.0   
DUREE_INTRO = 3.0           
DUREE_OUTRO = 5.0           
DUREE_TRANSITION = 0.3      
COULEUR_AGENCE_RGB = (0, 136, 144) 
POLICE_TEXTE = "Arial"      
FORMAT_VIDEO = (1080, 1920) 
TAILLE_CARRE = 190 
PATH_LOGO_FIXE = os.path.join("images", "logo.png")
DOSSIER_OUTPUT = "videos"

if not os.path.exists(DOSSIER_OUTPUT):
    os.makedirs(DOSSIER_OUTPUT)

if 'photo_list' not in st.session_state:
    st.session_state.photo_list = []

def reset_formulaire():
    st.session_state.photo_list = []
    keys_to_reset = ["p_pre", "p_nom", "p_tel", "p_mail", "p_adr", "v_titre", "v_prix", "v_ville", "v_desc"]
    for key in keys_to_reset:
        if key in st.session_state: st.session_state[key] = ""
    st.rerun()

# --- CSS & DIALOGS ---
st.markdown("""
    <style>
    div[data-testid="stDialog"] div[role="dialog"] { max-width: 90vw !important; max-height: 90vh !important; }
    div[data-testid="stDialog"] video { max-height: 70vh !important; width: auto !important; margin: 0 auto; display: block; }
    </style>
    """, unsafe_allow_html=True)

@st.dialog("Aperçu Vidéo", width="large")
def play_video_popup(video_path):
    st.video(video_path)
    if st.button("Fermer", use_container_width=True): st.rerun()

class StreamlitMoviePyLogger(ProgressBarLogger):
    def __init__(self, progress_bar, status_text):
        super().__init__()
        self.progress_bar, self.status_text = progress_bar, status_text
    def callback(self, **changes):
        if 'bars' in self.state and 't' in self.state['bars']:
            curr, total = self.state['bars']['t']['index'], self.state['bars']['t']['total']
            if total > 0:
                progression = max(0.0, min(float(curr) / total, 1.0))
                self.progress_bar.progress(progression)
                self.status_text.text(f"Génération : {int(progression * 100)}%")

# --- FONCTIONS DE RENDU ---
def creer_outro_manuelle(prenom, nom, tel, email, adresse, photo_user):
    fond = ColorClip(size=FORMAT_VIDEO, color=COULEUR_AGENCE_RGB).set_duration(DUREE_OUTRO)
    elements = [fond]
    if photo_user:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
            img = ImageOps.exif_transpose(Image.open(photo_user)).convert("RGB")
            img.thumbnail((500, 500), Image.Resampling.LANCZOS)
            img.save(tmp.name)
            elements.append(ImageClip(tmp.name).set_duration(DUREE_OUTRO).set_position(('center', 250)))
    elements.append(TextClip(f"{prenom} {nom}".upper(), fontsize=80, color='white', font=POLICE_TEXTE).set_position(('center', 850)).set_duration(DUREE_OUTRO))
    elements.append(TextClip(f"📞 {tel}\n\n✉️ {email}\n\n📍 {adresse}", fontsize=45, color='white', font=POLICE_TEXTE, method='caption', size=(900, None)).set_position(('center', 1050)).set_duration(DUREE_OUTRO))
    if os.path.exists(PATH_LOGO_FIXE):
        elements.append(ImageClip(PATH_LOGO_FIXE).resize(width=320).set_position(('center', 1600)).set_duration(DUREE_OUTRO))
    return CompositeVideoClip(elements).fadein(0.5)

def blur_frame_skimage(frame):
    from skimage.filters import gaussian
    return gaussian(frame.astype(float), sigma=40, channel_axis=-1)

def creer_slide_ken_burns_flou(image_path, duree):
    img_raw = ImageClip(image_path)
    ratio_bg = FORMAT_VIDEO[1] / img_raw.h
    bg_clip = (img_raw.resize(ratio_bg).crop(width=FORMAT_VIDEO[0], height=FORMAT_VIDEO[1], x_center=img_raw.w*ratio_bg/2, y_center=FORMAT_VIDEO[1]/2).fl_image(blur_frame_skimage).set_opacity(0.7).set_duration(duree))
    amp_x, amp_y = FORMAT_VIDEO[0] * 0.15, FORMAT_VIDEO[1] * 0.15
    dir_x, dir_y = random.choice([-1, 1]), random.choice([-1, 1])
    fg_zoom = img_raw.resize(lambda t: 1.1 + 0.40 * (t/duree))
    pos_func = lambda t: (
        (FORMAT_VIDEO[0]/2 - (img_raw.w * (1.1 + 0.40 * (t/duree))) / 2) + (dir_x * amp_x * (t/duree - 0.5)),
        (FORMAT_VIDEO[1]/2 - (img_raw.h * (1.1 + 0.40 * (t/duree))) / 2) + (dir_y * amp_y * (t/duree - 0.5))
    )
    return CompositeVideoClip([ColorClip(size=FORMAT_VIDEO, color=(15,15,15)).set_duration(duree), bg_clip.set_position("center"), fg_zoom.set_position(pos_func)], size=FORMAT_VIDEO).set_duration(duree)

def generer_video(photos_list, titre, desc, prix, ville, musique, p_nom, p_prenom, p_tel, p_email, p_adr, p_photo, ui_status, ui_progress, ui_console):
    output_log = io.StringIO()
    with contextlib.redirect_stdout(output_log), contextlib.redirect_stderr(output_log):
        all_clips = []
        nb_photos = len(photos_list)
        t_slides = DUREE_TOTALE_VIDEO - DUREE_INTRO - DUREE_OUTRO
        d_photo = (t_slides - (nb_photos - 1) * DUREE_TRANSITION) / nb_photos

        ui_status.text("Phase 1 : Séquences...")
        t1 = TextClip(titre.upper(), fontsize=75, color='white', font=POLICE_TEXTE, method='label', size=(900, None)).set_position(('center', 450)).set_duration(DUREE_INTRO)
        t2 = TextClip(desc, fontsize=45, color='white', font=POLICE_TEXTE, method='caption', size=(850, None)).set_position(('center', 850)).set_duration(DUREE_INTRO)
        all_clips.append(CompositeVideoClip([ColorClip(size=FORMAT_VIDEO, color=COULEUR_AGENCE_RGB).set_duration(DUREE_INTRO), t1, t2]).set_duration(DUREE_INTRO).fadein(1.0))

        for i, p in enumerate(photos_list):
            img_pil = ImageOps.exif_transpose(Image.open(p)).convert("RGB")
            with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
                img_pil.save(tmp.name, quality=95)
                slide = creer_slide_ken_burns_flou(tmp.name, d_photo)
            bandeau = CompositeVideoClip([ColorClip(size=(FORMAT_VIDEO[0], 180), color=COULEUR_AGENCE_RGB), TextClip(f"{titre.upper()}\n{prix} € | {ville.upper()}", fontsize=40, color='white', font=POLICE_TEXTE).set_position("center")], size=(FORMAT_VIDEO[0], 180)).set_position(('center', 1550)).set_duration(d_photo)
            all_clips.append(CompositeVideoClip([slide, bandeau]).set_duration(d_photo))
            if i < nb_photos - 1: all_clips.append(ColorClip(size=FORMAT_VIDEO, color=COULEUR_AGENCE_RGB).set_duration(DUREE_TRANSITION))
            ui_console.code(output_log.getvalue())

        all_clips.append(creer_outro_manuelle(p_prenom, p_nom, p_tel, p_email, p_adr, p_photo))
        video_base = concatenate_videoclips(all_clips, method="chain")
        
        def pos_carre(t):
            TL, BL, BR, TR = (0, 0), (0, 1730), (890, 1730), (890, 0)
            if t < 3: return TL
            elif t < 8: return (0, 1730 * ((t-3)/5))
            elif t < 11: return BL
            elif t < 16: return (890 * ((t-11)/5), 1730)
            elif t < 19: return BR
            elif t < 24: return (890, 1730 * (1-((t-19)/5)))
            elif t < 27: return TR
            else: return (890 * (1-((t-27)/5)), 0)
        
        carre_anime = ColorClip(size=(TAILLE_CARRE, TAILLE_CARRE), color=COULEUR_AGENCE_RGB).set_duration(DUREE_TOTALE_VIDEO).set_position(pos_carre)
        final_clips = [video_base, carre_anime]
        if os.path.exists(PATH_LOGO_FIXE):
            final_clips.append(ImageClip(PATH_LOGO_FIXE).set_duration(DUREE_TOTALE_VIDEO).resize(width=320).set_position(("right", "top")).margin(top=40, right=40, opacity=0))
        
        final_v = CompositeVideoClip(final_clips, size=FORMAT_VIDEO)
        if musique != "Aucune":
            aud = AudioFileClip(os.path.join("musique", musique))
            final_v = final_v.set_audio(audio_loop(aud, duration=DUREE_TOTALE_VIDEO).subclip(0, DUREE_TOTALE_VIDEO).audio_fadeout(2))

        st_logger = StreamlitMoviePyLogger(ui_progress, ui_status)
        nom_f = "".join(re.sub(r'[^\w\s-]', '', f"{titre}_{prix}_{ville}").strip().lower().split()) + ".mp4"
        chemin_final = os.path.join(DOSSIER_OUTPUT, nom_f)
        final_v.write_videofile(chemin_final, fps=24, codec="libx264", audio_codec="aac", preset="ultrafast", logger=st_logger, threads=4)
        final_v.close()
        return chemin_final

# --- INTERFACE ---
st.set_page_config(page_title="Studio Immo Cloud", page_icon="🏢", layout="wide")

col_t, col_r = st.columns([4, 1])
col_t.title("🏢 Studio Immo Online")
if col_r.button("🔄 Reset Global", use_container_width=True): reset_formulaire()

col_form, col_list = st.columns([1.6, 0.8])

with col_form:
    with st.expander("👤 Identité", expanded=True):
        c1, c2, c3 = st.columns(3)
        p_pre, p_nom, p_tel = c1.text_input("Prénom", value="Daniel", key="p_pre"), c2.text_input("Nom", value="JOURNO", key="p_nom"), c3.text_input("📞 Tél", value="06 00 00 00 00", key="p_tel")
        p_email, p_adr = st.text_input("✉️ Email", value="daniel.journo@ladresse.com", key="p_mail"), st.text_input("📍 Agence", value="92 bis rue de Paris, 94220 Charenton", key="p_adr")
        p_photo = st.file_uploader("🖼️ Photo Profil", type=['jpg', 'png'])

    with st.expander("🏠 Bien", expanded=True):
        c_t, c_p, c_v = st.columns(3)
        titre, prix, ville = c_t.text_input("Titre", value="VILLA", key="v_titre"), c_p.text_input("Prix (€)", value="850 000", key="v_prix"), c_v.text_input("Ville", value="Charenton", key="v_ville")
        musique_choisie = st.selectbox("🎵 Musique", ["Aucune"] + ([f for f in os.listdir("musique") if f.endswith('.mp3')] if os.path.exists("musique") else []))
        desc = st.text_area("Description Intro", key="v_desc")

    with st.expander("📸 Galerie", expanded=True):
        col_up, col_cl = st.columns([3, 1])
        up_files = col_up.file_uploader("Photos", accept_multiple_files=True, type=['jpg', 'jpeg', 'png'], label_visibility="collapsed")
        if col_cl.button("🗑️ Vider", use_container_width=True): st.session_state.photo_list = []; st.rerun()
        if up_files:
            for f in up_files:
                if f.name not in [p.name for p in st.session_state.photo_list]: st.session_state.photo_list.append(f)
        if st.session_state.photo_list:
            cols_g = st.columns(4)
            for idx, photo in enumerate(st.session_state.photo_list):
                with cols_g[idx % 4]:
                    st.image(photo, use_container_width=True)
                    b1, b2, b3 = st.columns(3)
                    if b1.button("⬅️", key=f"L_{idx}") and idx > 0:
                        st.session_state.photo_list[idx], st.session_state.photo_list[idx-1] = st.session_state.photo_list[idx-1], st.session_state.photo_list[idx]; st.rerun()
                    if b2.button("❌", key=f"R_{idx}"): st.session_state.photo_list.pop(idx); st.rerun()
                    if b3.button("➡️", key=f"N_{idx}") and idx < len(st.session_state.photo_list) - 1:
                        st.session_state.photo_list[idx], st.session_state.photo_list[idx+1] = st.session_state.photo_list[idx+1], st.session_state.photo_list[idx]; st.rerun()

    if st.button("🎬 GÉNÉRER LA VIDÉO", use_container_width=True, type="primary"):
        if not st.session_state.photo_list: st.error("Ajoutez des photos.")
        else:
            ui_s, ui_p, ui_c = st.empty(), st.progress(0.0), st.expander("Logs").empty()
            try:
                generer_video(st.session_state.photo_list, titre, desc, prix, ville, musique_choisie, p_nom, p_pre, p_tel, p_email, p_adr, p_photo, ui_s, ui_p, ui_c)
                st.success("Vidéo terminée !"); st.rerun()
            except Exception as e: st.error(f"Erreur : {e}")

with col_list:
    st.subheader("📂 Historique")
    if os.path.exists(DOSSIER_OUTPUT):
        fichiers = sorted([f for f in os.listdir(DOSSIER_OUTPUT) if f.endswith(".mp4")], key=lambda x: os.path.getmtime(os.path.join(DOSSIER_OUTPUT, x)), reverse=True)
        if not fichiers: st.info("Aucune vidéo.")
        for f in fichiers:
            p_f = os.path.join(DOSSIER_OUTPUT, f)
            with st.container(border=True):
                st.write(f"**{f}**")
                st.caption(f"📅 {datetime.fromtimestamp(os.path.getmtime(p_f)).strftime('%d/%m/%Y %H:%M')}")
                c_dl, c_pl, c_rm = st.columns(3)
                with open(p_f, "rb") as fi: c_dl.download_button("💾", fi, file_name=f, key=f"dl_{f}")
                if c_pl.button("▶️", key=f"play_{f}"): play_video_popup(p_f)
                if c_rm.button("🗑️", key=f"del_{f}"): os.remove(p_f); st.rerun()