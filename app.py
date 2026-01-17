import streamlit as st
import os
import tempfile
import re
import io
import sys
import contextlib
import textwrap
from datetime import datetime
from moviepy.editor import *
from moviepy.audio.fx.all import audio_loop
from PIL import Image, ImageOps, ImageFont, ImageDraw
import numpy as np
import random
from proglog import ProgressBarLogger

# --- CONFIGURATION ---
# Pas besoin de configuration ImageMagick car nous ne l'utilisons plus pour le texte !

# Correctif Windows (toujours utile pour le local)
if sys.platform == 'win32':
    import asyncio
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except:
        pass

# --- CONSTANTES ---
DUREE_TOTALE_VIDEO = 32.0   
DUREE_INTRO = 3.0           
DUREE_OUTRO = 5.0           
DUREE_TRANSITION = 0.3      
COULEUR_AGENCE_RGB = (0, 136, 144) 
FORMAT_VIDEO = (1080, 1920) 
TAILLE_CARRE = 190 
PATH_LOGO_FIXE = os.path.join("images", "logo.png")
DOSSIER_OUTPUT = "videos"

# Choix automatique de la police selon le système (Windows vs Linux/Cloud)
if sys.platform == 'win32':
    FONT_NAME = "arial.ttf" # Windows standard
else:
    FONT_NAME = "DejaVuSans.ttf" # Linux standard (Streamlit Cloud)

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

# --- FONCTION MAGIQUE DE REMPLACEMENT TEXTE (PIL) ---
def creer_texte_pil(texte, fontsize, color, font_path, size=None, duration=1.0, align='center', wrap_width=30):
    """
    Crée un ImageClip contenant du texte en utilisant PIL au lieu de ImageMagick.
    Contourne 100% des erreurs de sécurité Linux.
    """
    # 1. Gestion de la taille du canvas
    w, h = (size if size else (FORMAT_VIDEO[0], int(fontsize * 1.5)))
    
    # 2. Création image transparente
    img = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    # 3. Chargement Police
    try:
        font = ImageFont.truetype(font_path, fontsize)
    except OSError:
        # Fallback si police introuvable
        try:
            font = ImageFont.truetype("DejaVuSans.ttf", fontsize) # Linux fallback
        except:
            font = ImageFont.load_default() # Dernier recours
            
    # 4. Gestion du retour à la ligne (wrapping)
    lines = []
    if size is not None: # Si on a une zone contrainte, on wrap
        # On utilise textwrap pour couper intelligemment
        raw_lines = texte.split('\n')
        for line in raw_lines:
            lines.extend(textwrap.wrap(line, width=wrap_width))
    else:
        lines = texte.split('\n')

    # 5. Calcul des positions pour centrer
    # On calcule la hauteur totale du bloc de texte
    total_text_height = sum([draw.textbbox((0, 0), line, font=font)[3] for line in lines])
    current_y = (h - total_text_height) // 2
    
    for line in lines:
        # Centrage horizontal
        bbox = draw.textbbox((0, 0), line, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        
        x = (w - text_w) // 2
        
        # Dessin du texte (avec petit contour noir pour lisibilité si blanc)
        if color == 'white':
            # Ombre portée légère
            draw.text((x+2, current_y+2), line, font=font, fill="black")
            
        draw.text((x, current_y), line, font=font, fill=color)
        current_y += text_h + 10 # Interligne

    # 6. Sauvegarde et Conversion MoviePy
    with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
        img.save(tmp.name)
        clip = ImageClip(tmp.name).set_duration(duration)
        
    return clip

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

# --- FONCTION DE RENDU VIDEO ---
def generer_video(photos_list, titre, desc, prix, ville, musique, p_nom, p_prenom, p_tel, p_email, p_adr, p_photo, ui_status, ui_progress, ui_console):
    output_log = io.StringIO()
    with contextlib.redirect_stdout(output_log), contextlib.redirect_stderr(output_log):
        all_clips = []
        nb_photos = len(photos_list)
        t_slides = DUREE_TOTALE_VIDEO - DUREE_INTRO - DUREE_OUTRO
        d_photo = (t_slides - (nb_photos - 1) * DUREE_TRANSITION) / nb_photos

        ui_status.text("Phase 1 : Séquences (Texte PIL)...")
        
        # INTRO AVEC TEXTE PIL
        t1 = creer_texte_pil(titre.upper(), 75, 'white', FONT_NAME, size=(900, 200), duration=DUREE_INTRO, wrap_width=15).set_position(('center', 450))
        t2 = creer_texte_pil(desc, 45, 'white', FONT_NAME, size=(850, 400), duration=DUREE_INTRO, wrap_width=30).set_position(('center', 850))
        
        intro_bg = ColorClip(size=FORMAT_VIDEO, color=COULEUR_AGENCE_RGB).set_duration(DUREE_INTRO)
        all_clips.append(CompositeVideoClip([intro_bg, t1, t2]).set_duration(DUREE_INTRO).fadein(1.0))

        for i, p in enumerate(photos_list):
            img_pil = ImageOps.exif_transpose(Image.open(p)).convert("RGB")
            with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
                img_pil.save(tmp.name, quality=95)
                slide = creer_slide_ken_burns_flou(tmp.name, d_photo)
            
            # BANDEAU AVEC TEXTE PIL
            txt_content = f"{titre.upper()}\n{prix} € | {ville.upper()}"
            txt_img = creer_texte_pil(txt_content, 40, 'white', FONT_NAME, size=(FORMAT_VIDEO[0], 180), duration=d_photo)
            
            bandeau = CompositeVideoClip([
                ColorClip(size=(FORMAT_VIDEO[0], 180), color=COULEUR_AGENCE_RGB), 
                txt_img.set_position("center")
            ], size=(FORMAT_VIDEO[0], 180)).set_position(('center', 1550)).set_duration(d_photo)
            
            all_clips.append(CompositeVideoClip([slide, bandeau]).set_duration(d_photo))
            if i < nb_photos - 1: all_clips.append(ColorClip(size=FORMAT_VIDEO, color=COULEUR_AGENCE_RGB).set_duration(DUREE_TRANSITION))
            ui_console.code(output_log.getvalue())

        # OUTRO
        fond_outro = ColorClip(size=FORMAT_VIDEO, color=COULEUR_AGENCE_RGB).set_duration(DUREE_OUTRO)
        elems_outro = [fond_outro]
        
        if p_photo:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
                img = ImageOps.exif_transpose(Image.open(p_photo)).convert("RGB")
                img.thumbnail((500, 500), Image.Resampling.LANCZOS)
                img.save(tmp.name)
                elems_outro.append(ImageClip(tmp.name).set_duration(DUREE_OUTRO).set_position(('center', 250)))
        
        # Textes Outro PIL
        t_nom = creer_texte_pil(f"{p_prenom} {p_nom}".upper(), 80, 'white', FONT_NAME, size=(1000, 150), duration=DUREE_OUTRO).set_position(('center', 850))
        elems_outro.append(t_nom)
        
        infos_str = f"📞 {p_tel}\n\n✉️ {p_email}\n\n📍 {p_adr}"
        t_infos = creer_texte_pil(infos_str, 45, 'white', FONT_NAME, size=(900, 500), duration=DUREE_OUTRO, wrap_width=35).set_position(('center', 1050))
        elems_outro.append(t_infos)

        if os.path.exists(PATH_LOGO_FIXE):
            elems_outro.append(ImageClip(PATH_LOGO_FIXE).resize(width=320).set_position(('center', 1600)).set_duration(DUREE_OUTRO))
            
        all_clips.append(CompositeVideoClip(elems_outro).fadein(0.5))
        
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
