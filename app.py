import streamlit as st
import os
import sys

# --- 🛠️ PATCH PRIORITAIRE (A NE PAS BOUGER) ---
import PIL.Image
if not hasattr(PIL.Image, 'ANTIALIAS'):
    try:
        PIL.Image.ANTIALIAS = PIL.Image.Resampling.LANCZOS
    except AttributeError:
        PIL.Image.ANTIALIAS = PIL.Image.LANCZOS

import tempfile
import re
import io
import contextlib
import textwrap
import urllib.parse
import gc
from datetime import datetime
from moviepy.editor import *
from moviepy.audio.fx.all import audio_loop
from PIL import Image, ImageOps, ImageFont, ImageDraw
import numpy as np
import random
from proglog import ProgressBarLogger
import traceback

# --- CONFIGURATION ---
if sys.platform == 'win32':
    import asyncio
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except:
        pass

# --- DONNÉES AGENCES ---
AGENCES_DATA = {
    "Charenton": {
        "adresse": "92 bis rue de Paris, 94220 Charenton-le-Pont",
        "img": "agence-cht.jpg"
    },
    "Alfortville": {
        "adresse": "125 rue Paul Vaillant Couturier, 94140 Alfortville",
        "img": "agence-alf.jpg"
    },
    "Maison Alfort": {
        "adresse": "8 avenue de la République, 94700 Maison-Alfort",
        "img": "agence-maf.jpg"
    }
}

# --- CONSTANTES (MODE ECO 720p) ---
DUREE_TOTALE_VIDEO = 32.0   
DUREE_INTRO = 5.0           
DUREE_OUTRO = 5.0           
COULEUR_AGENCE_RGB = (0, 136, 144) 

# FORMAT 720p
FORMAT_VIDEO = (720, 1280)  
TAILLE_CARRE = int(190 * (720/1080)) 

PATH_LOGO_FIXE = os.path.join("images", "logo.png")
DOSSIER_OUTPUT = "videos"

# Choix police
if sys.platform == 'win32':
    FONT_NAME = "arial.ttf" 
else:
    FONT_NAME = "DejaVuSans.ttf" 

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
    .social-btn { text-align: center; font-size: 0.9em; margin-top: 5px; }
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

# --- FONCTION TEXTE PIL (SANS OMBRE) ---
def creer_texte_pil(texte, fontsize, color, font_path, size=None, duration=1.0, align='center', wrap_width=30):
    ratio = 720 / 1080
    fontsize = int(fontsize * ratio)
    
    w, h = (size if size else (FORMAT_VIDEO[0], int(fontsize * 1.5)))
    img = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype(font_path, fontsize)
    except OSError:
        try:
            font = ImageFont.truetype("DejaVuSans.ttf", fontsize)
        except:
            font = ImageFont.load_default()
            
    lines = []
    if size is not None:
        raw_lines = texte.split('\n')
        for line in raw_lines:
            lines.extend(textwrap.wrap(line, width=wrap_width))
    else:
        lines = texte.split('\n')

    total_text_height = sum([draw.textbbox((0, 0), line, font=font)[3] for line in lines])
    current_y = (h - total_text_height) // 2
    
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        x = (w - text_w) // 2
        
        # Dessin simple sans ombre
        draw.text((x, current_y), line, font=font, fill=color)
        current_y += text_h + 10

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

# --- FORMAT TEL & PRIX---#
def formater_telephone(numero):
    """Transforme 0612345678 en 06 12 34 56 78"""
    if not numero: return ""
    # On ne garde que les chiffres
    clean = re.sub(r'\D', '', str(numero))
    # Si c'est un numéro à 10 chiffres (standard français)
    if len(clean) == 10:
        return " ".join([clean[i:i+2] for i in range(0, 10, 2)])
    return numero # Sinon on retourne tel quel
    
def formater_prix(prix):
    """Transforme 380000 en 380 000"""
    if not prix: return ""
    try:
        # On enlève les espaces ou symboles parasites pour avoir un nombre pur
        clean = re.sub(r'\D', '', str(prix)) 
        # On formate avec une virgule, puis on remplace la virgule par un espace
        return "{:,}".format(int(clean)).replace(",", " ")
    except ValueError:
    return prix # Si ce n'est pas un nombre (ex: "Nous consulter"), on renvoie tel quel
# --- GENERATION VIDEO ---
def generer_video(photos_list, titre, desc, prix, ville, musique, p_nom, p_prenom, p_tel, p_email, p_adr, p_photo, agence_nom, ui_status, ui_progress, ui_console):
    output_log = io.StringIO()
    with contextlib.redirect_stdout(output_log), contextlib.redirect_stderr(output_log):
        all_clips = []
        desc = desc[:230] 
        photos_list = photos_list[:10]
        
        nb_photos = len(photos_list)
        t_slides = DUREE_TOTALE_VIDEO - DUREE_INTRO - DUREE_OUTRO
        d_photo = t_slides / nb_photos 

        # Durée totale réelle de la partie SLIDES (pour le calque vert)
        duree_totale_slides = nb_photos * d_photo

        # --- GEOMETRIE EMPILEMENT ---
        H_VIDEO = FORMAT_VIDEO[1] # 1280
        h_footer = 60 # Hauteur bandeau noir bas
        
        # Le carré rebondit SUR le footer noir
        Y_MAX_CARRE = H_VIDEO - h_footer - TAILLE_CARRE
        
        # Le bandeau vert est posé SUR la ligne de rebond du carré
        h_bandeau_vert = int(180 * 0.66) 
        y_bandeau_vert = Y_MAX_CARRE - h_bandeau_vert

        ui_status.text("Phase 1 : Montage...")
        
        # --- INTRO ---
        t1 = creer_texte_pil(titre.upper(), 60, 'white', FONT_NAME, size=(int(1060*0.66), int(272*0.66)), duration=DUREE_INTRO, wrap_width=30).set_position(('center', int(480*0.66)))
        t2 = creer_texte_pil(desc, 40, 'white', FONT_NAME, size=(int(1060*0.66), int(550*0.66)), duration=DUREE_INTRO, wrap_width=50).set_position(('center', int(800*0.66)))
        intro_bg = ColorClip(size=FORMAT_VIDEO, color=COULEUR_AGENCE_RGB).set_duration(DUREE_INTRO)
        
        intro_elements = [intro_bg, t1, t2]

        # AJOUT IMAGE AGENCE SUR INTRO
        nom_img_agence = AGENCES_DATA[agence_nom]["img"]
        path_img_agence = os.path.join("images", nom_img_agence)
        
        if os.path.exists(path_img_agence):
            img_ag = ImageClip(path_img_agence).set_duration(DUREE_INTRO)
            img_ag = img_ag.resize(width=int(400 * 0.66)) 
            img_ag = img_ag.set_position(('center', int(1350 * 0.66)))
            intro_elements.append(img_ag)
            
        all_clips.append(CompositeVideoClip(intro_elements).set_duration(DUREE_INTRO).fadein(1.0))

        # --- SLIDES (PHOTOS UNIQUEMENT) ---
        for i, p in enumerate(photos_list):
            gc.collect()
            img_pil = ImageOps.exif_transpose(Image.open(p)).convert("RGB")
            with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
                img_pil.save(tmp.name, quality=95)
                slide = creer_slide_ken_burns_flou(tmp.name, d_photo)
            
            all_clips.append(slide)
            ui_console.code(output_log.getvalue())

        # OUTRO
        fond_outro = ColorClip(size=FORMAT_VIDEO, color=COULEUR_AGENCE_RGB).set_duration(DUREE_OUTRO)
        elems_outro = [fond_outro]
        if p_photo:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
                img = ImageOps.exif_transpose(Image.open(p_photo)).convert("RGB")
                img.thumbnail((400, 400), Image.Resampling.LANCZOS)
                img.save(tmp.name)
                elems_outro.append(ImageClip(tmp.name).set_duration(DUREE_OUTRO).set_position(('center', int(250*0.66))))
        
        t_nom = creer_texte_pil(f"{p_prenom} {p_nom}".upper(), 80, 'white', FONT_NAME, size=(int(1000*0.66), int(150*0.66)), duration=DUREE_OUTRO).set_position(('center', int(850*0.66)))
        elems_outro.append(t_nom)
        
        # Infos Texte
        # On nettoie le numéro avant de l'afficher
        p_tel_clean = formater_telephone(p_tel)
        infos_str = f"Tél : {p_tel_clean}\n\nEmail : {p_email}\n\nAgence : {p_adr}"
        t_infos = creer_texte_pil(infos_str, 45, 'white', FONT_NAME, size=(int(900*0.66), int(500*0.66)), duration=DUREE_OUTRO, wrap_width=35).set_position(('center', int(1050*0.66)))
        elems_outro.append(t_infos)
        
        if os.path.exists(PATH_LOGO_FIXE):
            elems_outro.append(ImageClip(PATH_LOGO_FIXE).resize(width=int(320*0.66)).set_position(('center', int(1600*0.66))).set_duration(DUREE_OUTRO))
        
        all_clips.append(CompositeVideoClip(elems_outro).fadein(0.5))
        
        # --- CALQUE 1 : LA VIDÉO DE FOND ---
        video_base = concatenate_videoclips(all_clips, method="chain")
        
        # --- CALQUE 2 : LE CARRÉ ANIMÉ ---
        W_VIDEO = FORMAT_VIDEO[0] 
        def pos_carre(t):
            TL, BL, BR, TR = (0, 0), (0, Y_MAX_CARRE), (W_VIDEO-TAILLE_CARRE, Y_MAX_CARRE), (W_VIDEO-TAILLE_CARRE, 0)
            if t < 3: return TL
            elif t < 8: return (0, Y_MAX_CARRE * ((t - 3) / 5))
            elif t < 11: return BL
            elif t < 16: return ((W_VIDEO-TAILLE_CARRE) * ((t - 11) / 5), Y_MAX_CARRE)
            elif t < 19: return BR
            elif t < 24: return (W_VIDEO-TAILLE_CARRE, Y_MAX_CARRE * (1 - ((t - 19) / 5)))
            elif t < 27: return TR
            else: return ((W_VIDEO-TAILLE_CARRE) * (1 - ((t - 27) / 5)), 0)
        
        carre_anime = ColorClip(size=(TAILLE_CARRE, TAILLE_CARRE), color=COULEUR_AGENCE_RGB).set_duration(DUREE_TOTALE_VIDEO).set_position(pos_carre)
        
        # --- CALQUE 3 : LE BANDEAU VERT (PRIX/VILLE) ---
        prix_clean = formater_prix(prix)
        txt_content = f"{titre.upper()}\n{prix_clean} € | {ville.upper()}"
        # On définit wrap_width=50 pour autoriser un titre plus long sur une ligne
        txt_img = creer_texte_pil(txt_content, 40, 'white', FONT_NAME, size=(FORMAT_VIDEO[0], h_bandeau_vert), duration=duree_totale_slides, wrap_width=50)
        
        # [CORRECTIF] : On force la durée sur le fond de couleur aussi
        bg_bandeau_vert = ColorClip(size=(FORMAT_VIDEO[0], h_bandeau_vert), color=COULEUR_AGENCE_RGB).set_duration(duree_totale_slides)

        bandeau_vert_clip = CompositeVideoClip(
            [bg_bandeau_vert, txt_img.set_position("center")], 
            size=(FORMAT_VIDEO[0], h_bandeau_vert)
        )
        bandeau_vert_clip = bandeau_vert_clip.set_position(('center', y_bandeau_vert)).set_start(DUREE_INTRO)
        
        # --- CALQUE 4 : BANDEAU BAS NOIR (TOUJOURS AU DESSUS) ---
        txt_footer_content = "Transaction - Location - Gestion - Syndic - 01 41 79 04 75"
        bg_footer = ColorClip(size=(FORMAT_VIDEO[0], h_footer), color=(0,0,0)).set_opacity(1.0).set_duration(DUREE_TOTALE_VIDEO)
        txt_footer = creer_texte_pil(txt_footer_content, 30, 'white', FONT_NAME, size=(FORMAT_VIDEO[0], h_footer), duration=DUREE_TOTALE_VIDEO, wrap_width=200)
        
        footer_clip = CompositeVideoClip([bg_footer, txt_footer.set_position("center")], size=(FORMAT_VIDEO[0], h_footer))
        footer_clip = footer_clip.set_position(("center", "bottom")).set_duration(DUREE_TOTALE_VIDEO)

        # --- ASSEMBLAGE FINAL ---
        final_clips = [
            video_base,          # Fond
            carre_anime,         # Milieu (Derrière bandeau vert)
            bandeau_vert_clip,   # Devant (Cache le carré)
            footer_clip          # Devant tout
        ]
        
        if os.path.exists(PATH_LOGO_FIXE):
            final_clips.append(ImageClip(PATH_LOGO_FIXE).set_duration(DUREE_TOTALE_VIDEO).resize(width=int(320*0.66)).set_position(("right", "top")).margin(top=30, right=30, opacity=0))
        
        # [CORRECTIF CRUCIAL] : On force la durée totale sur le composite final
        final_v = CompositeVideoClip(final_clips, size=FORMAT_VIDEO).set_duration(DUREE_TOTALE_VIDEO)
        
        if musique != "Aucune":
            aud = AudioFileClip(os.path.join("musique", musique))
            final_v = final_v.set_audio(audio_loop(aud, duration=DUREE_TOTALE_VIDEO).subclip(0, DUREE_TOTALE_VIDEO).audio_fadeout(2))

        st_logger = StreamlitMoviePyLogger(ui_progress, ui_status)
        nom_f = "".join(re.sub(r'[^\w\s-]', '', f"{titre}_{prix}_{ville}").strip().lower().split()) + ".mp4"
        chemin_final = os.path.join(DOSSIER_OUTPUT, nom_f)
        
        final_v.write_videofile(chemin_final, fps=24, codec="libx264", audio_codec="aac", preset="ultrafast", logger=st_logger, threads=1)
        final_v.close()
        del final_v
        del all_clips
        gc.collect()
        return chemin_final

# --- HELPER RESEAUX SOCIAUX ---
def afficher_kit_social(titre, ville, prix, p_tel):
    st.write("---")
    st.subheader("📱 Kit de Partage Réseaux Sociaux")
    
    texte_post = f"""🔥 NOUVEAUTÉ À SAISIR !
📍 {ville} - {titre}
💎 Prix : {prix} €

Nouveau dans votre agence L'Adresse {ville} ! 
Découvrez cette pépite en vidéo. 🎥

Pour visiter ou pour plus d'infos :
📞 {p_tel}
✉️ MP direct

#immobilier #avendre #{ville.lower().replace(' ', '')} #valdemarne #realestate #nouveauté #video"""
    
    c_txt, c_btn = st.columns([1.5, 1])
    
    with c_txt:
        st.caption("📋 Copiez ce texte pour votre post :")
        st.code(texte_post, language=None)
    
    with c_btn:
        st.caption("🚀 Publier sur :")
        st.link_button("🔵 Ouvrir LinkedIn", "https://www.linkedin.com/feed/", use_container_width=True)
        st.link_button("🔵 Ouvrir Facebook", "https://www.facebook.com/", use_container_width=True)
        st.link_button("🟣 Ouvrir Instagram", "https://www.instagram.com/", use_container_width=True)

# --- INTERFACE ---
st.set_page_config(page_title="Studio Immo v11.8", page_icon="🏢", layout="wide")

col_t, col_r = st.columns([4, 1])
col_t.title("🏢 Studio Immo v11.8")
if col_r.button("🔄 Reset Global", use_container_width=True): reset_formulaire()

col_form, col_list = st.columns([1.6, 0.8])

with col_form:
    with st.expander("👤 Identité", expanded=True):
        c1, c2, c3 = st.columns(3)
        p_pre, p_nom, p_tel = c1.text_input("Prénom", value="", key="p_pre"), c2.text_input("Nom", value="", key="p_nom"), c3.text_input("📞 Tél", value="06", key="p_tel")
        
        ca, cb = st.columns(2)
        p_email = ca.text_input("✉️ Email", value="@ladresse.com", key="p_mail")
        
        choix_agence = cb.selectbox("📍 Choisir l'Agence", list(AGENCES_DATA.keys()))
        adresse_auto = AGENCES_DATA[choix_agence]["adresse"]
        p_adr = st.text_input("Adresse Agence (Auto)", value=adresse_auto, disabled=True)
        
        p_photo = st.file_uploader("🖼️ Photo Profil", type=['jpg', 'png'])

    with st.expander("🏠 Bien", expanded=False):
        c_t, c_p, c_v = st.columns(3)
        titre, prix, ville = c_t.text_input("Titre", value="", key="v_titre"), c_p.text_input("Prix (€)", value="", key="v_prix"), c_v.text_input("Ville", value="", key="v_ville")
        musique_choisie = st.selectbox("🎵 Musique", ["Aucune"] + ([f for f in os.listdir("musique") if f.endswith('.mp3')] if os.path.exists("musique") else []))
        desc = st.text_area("Description Intro (Max 255 car.)", key="v_desc", max_chars=255)

    with st.expander("📸 Galerie (Max 10 photos)", expanded=False):
        col_up, col_cl = st.columns([3, 1])
        up_files = col_up.file_uploader("Photos", accept_multiple_files=True, type=['jpg', 'jpeg', 'png'], label_visibility="collapsed")
        if col_cl.button("🗑️ Vider", use_container_width=True): st.session_state.photo_list = []; st.rerun()
        
        if up_files:
            for f in up_files:
                if f.name not in [p.name for p in st.session_state.photo_list]:
                    st.session_state.photo_list.append(f)
            
            if len(st.session_state.photo_list) > 10:
                st.warning("⚠️ Limite de 10 photos atteinte. Les suivantes ont été ignorées.")
                st.session_state.photo_list = st.session_state.photo_list[:10]
        
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
                chemin = generer_video(st.session_state.photo_list, titre, desc, prix, ville, musique_choisie, p_nom, p_pre, p_tel, p_email, p_adr, p_photo, choix_agence, ui_s, ui_p, ui_c)
                st.success("Vidéo terminée !")
                st.session_state['last_video_path'] = chemin
                st.session_state['last_video_data'] = (titre, ville, prix, p_tel)
                st.rerun()
            except Exception as e:
                st.error(f"Une erreur est survenue : {e}")
                st.code(traceback.format_exc())
            
    if 'last_video_path' in st.session_state and os.path.exists(st.session_state['last_video_path']):
        v_titre, v_ville, v_prix, v_tel = st.session_state.get('last_video_data', ("Bien", "Ville", "0", "06.."))
        afficher_kit_social(v_titre, v_ville, v_prix, v_tel)

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

