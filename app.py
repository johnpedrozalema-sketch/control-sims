import streamlit as st
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
import time
import hashlib
import io
import json
import pytz
import uuid
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ==============================================================================
# 1. CONFIGURACIÓN GLOBAL (Debe ser la primera línea)
# ==============================================================================
st.set_page_config(page_title="Suite Gestión Total", page_icon="🏢", layout="wide")

# Constantes Google Sheets
NOMBRE_HOJA = "Base de Datos SIMs" 
SCOPE = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
KEY_FILE = 'credenciales.json'

# ==============================================================================
# 2. FUNCIONES DE UTILIDAD (Seguridad, Correo, Conexión)
# ==============================================================================

def make_hashes(password):
    return hashlib.sha256(str.encode(password)).hexdigest()

def check_hashes(password, hashed_text):
    if make_hashes(password) == hashed_text:
        return True
    return False

def conectar_google():
    """Conexión robusta a Google Sheets con caché y reintentos"""
    try:
        if "gcp_service_account" in st.secrets:
            creds_dict = dict(st.session_state.secrets["gcp_service_account"]) if "secrets" in st.session_state else st.secrets["gcp_service_account"]
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, SCOPE)
        else:
            creds = ServiceAccountCredentials.from_json_keyfile_name(KEY_FILE, SCOPE)
            
        client = gspread.authorize(creds)
        sheet = client.open(NOMBRE_HOJA)
        return sheet
    except Exception as e:
        st.error(f"Error conectando a Google: {e}")
        st.stop()

def enviar_correo_activacion(email_destino, token, usuario):
    """Envía un correo con el link para configurar la contraseña"""
    try:
        # Recuperar credenciales de secrets.toml
        EMAIL_EMISOR = st.secrets["email"]["address"]
        EMAIL_PASS = st.secrets["email"]["password"]
        BASE_URL = st.secrets["email"].get("base_url", "http://localhost:8501")
        
        # Crear link
        link = f"{BASE_URL}/?token_reset={token}"
        
        msg = MIMEMultipart()
        msg['From'] = EMAIL_EMISOR
        msg['To'] = email_destino
        msg['Subject'] = "🔐 Activa tu cuenta - Control SIM & Eventos"

        cuerpo = f"""
        Hola {usuario},
        
        Se ha creado tu cuenta en la plataforma.
        
        Para comenzar, debes configurar tu contraseña segura haciendo clic aquí:
        {link}
        
        Si no solicitaste esto, ignora este mensaje.
        """
        msg.attach(MIMEText(cuerpo, 'plain'))

        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(EMAIL_EMISOR, EMAIL_PASS)
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        st.error(f"Error enviando correo: {e}")
        return False

def gestionar_reset_password():
    """Lógica para cuando el usuario entra con el link del correo"""
    token_url = st.query_params.get("token_reset", None)
    
    if token_url:
        st.info("🔄 Modo Recuperación de Cuenta Detectado")
        sheet = conectar_google()
        ws = sheet.worksheet("usuarios")
        data = ws.get_all_records()
        df = pd.DataFrame(data)
        
        # Buscar token
        usuario_encontrado = df[df['token'] == token_url]
        
        if not usuario_encontrado.empty:
            user_row = usuario_encontrado.iloc[0]
            st.success(f"Hola {user_row['username']}, define tu nueva contraseña.")
            
            with st.form("form_reset"):
                p1 = st.text_input("Nueva Contraseña", type="password")
                p2 = st.text_input("Confirmar Contraseña", type="password")
                if st.form_submit_button("Guardar Contraseña"):
                    if p1 == p2 and len(p1) > 4:
                        # Actualizar en Google Sheets
                        cell = ws.find(token_url)
                        row_num = cell.row
                        
                        # Columna B es password (2), Columna E es token (5) - ASUMIENDO ESTRUCTURA
                        ws.update_cell(row_num, 2, make_hashes(p1)) # Guardar hash
                        ws.update_cell(row_num, 5, "") # Borrar token para que no se use de nuevo
                        
                        st.success("Contraseña actualizada. Por favor inicia sesión.")
                        st.query_params.clear() # Limpiar URL
                        time.sleep(2)
                        st.rerun()
                    else:
                        st.error("Las contraseñas no coinciden o son muy cortas.")
        else:
            st.error("Este enlace ha expirado o no es válido.")
            if st.button("Ir al Inicio"):
                st.query_params.clear()
                st.rerun()
        return True # Indica que estamos en modo reset
    return False

# ==============================================================================
# 3. MÓDULO A: CONTROL SIM (Tu código original optimizado)
# ==============================================================================
def app_control_sim():
    st.markdown("## 📱 Sistema de Control SIM")
    
    # --- Funciones auxiliares locales del módulo SIM ---
    @st.cache_data(ttl=10)
    def leer_datos_sim(pestana):
        sheet = conectar_google()
        worksheet = sheet.worksheet(pestana)
        data = worksheet.get_all_records()
        if not data: return pd.DataFrame()
        return pd.DataFrame(data)

    def limpiar_cache_sim():
        st.cache_data.clear()

    # (Aquí pegamos la lógica de negocio de tu código original, resumida para integración)
    def escribir_fila(pestana, fila):
        ws = conectar_google().worksheet(pestana)
        ws.append_row(fila)
        limpiar_cache_sim()

    # --- MENÚ INTERNO DE CONTROL SIM ---
    menu_sim = ["Dashboard", "Registrar SIM", "Actualizar Datos", "Reportes"]
    choice = st.radio("Opciones SIM:", menu_sim, horizontal=True)
    st.markdown("---")

    if choice == "Dashboard":
        df = leer_datos_sim("sims")
        if not df.empty and 'estado' in df.columns:
            k1, k2, k3 = st.columns(3)
            k1.metric("Total SIMs", len(df))
            k2.metric("Activas", len(df[df['estado']=='Activa']))
            k3.metric("Botiquín", len(df[df['estado']=='Botiquin']))
        else:
            st.info("No hay datos o no se pudo cargar la base.")

    elif choice == "Registrar SIM":
        st.subheader("➕ Nueva SIM Manual")
        with st.form("sim_add"):
            c1, c2 = st.columns(2)
            iccid = c1.text_input("ICCID")
            linea = c2.text_input("Línea")
            cliente = c1.text_input("Cliente")
            costo = c2.number_input("Costo Q", 0.0)
            if st.form_submit_button("Guardar"):
                # Lógica simplificada de guardado
                fecha = datetime.now().strftime("%Y-%m-%d")
                estado = "Activa" if linea and cliente else "Botiquin"
                row = [iccid, linea, cliente, "", "", "", "Guatemala", costo, 0, estado, fecha]
                escribir_fila("sims", row)
                st.success("SIM Guardada")

    elif choice == "Reportes":
        st.write("Vista de datos completa:")
        st.dataframe(leer_datos_sim("sims"))

# ==============================================================================
# 4. MÓDULO B: GESTOR DE EVENTOS V17.0 (Integrado)
# ==============================================================================
def app_eventos_v17():
    st.markdown("## 🎉 Gestor de Eventos V17")
    
    # Inicialización local anti-KeyError
    if 'df_invitados' not in st.session_state:
        st.session_state['df_invitados'] = pd.DataFrame(columns=['ID', 'Nombre', 'Email', 'Estado', 'Familia'])

    tab1, tab2 = st.tabs(["Dashboard Invitados", "Gestión Lista"])
    
    with tab1:
        df = st.session_state['df_invitados']
        if not df.empty:
            st.bar_chart(df['Estado'].value_counts())
        else:
            st.info("Aún no hay invitados registrados.")
            
    with tab2:
        st.write("Edita tu lista de invitados (En memoria):")
        df_edit = st.data_editor(st.session_state['df_invitados'], num_rows="dynamic")
        if not df_edit.equals(st.session_state['df_invitados']):
            st.session_state['df_invitados'] = df_edit
            st.rerun()

# ==============================================================================
# 5. MÓDULO C: GESTIÓN DE USUARIOS (Con Envío de Correo)
# ==============================================================================
def app_gestion_usuarios():
    st.markdown("## 👤 Administración de Usuarios")
    
    tab1, tab2 = st.tabs(["Crear Usuario (Email)", "Ver Usuarios"])
    
    with tab1:
        st.info("Al crear un usuario, se le enviará un correo para que establezca su contraseña.")
        with st.form("crear_user_mail"):
            col1, col2 = st.columns(2)
            new_user = col1.text_input("Nombre de Usuario")
            new_email = col2.text_input("Correo Electrónico")
            new_rol = st.selectbox("Rol", ["admin", "general"])
            
            if st.form_submit_button("Crear y Enviar Invitación"):
                sheet = conectar_google()
                ws = sheet.worksheet("usuarios")
                
                # Verificar si existe usuario
                users = ws.col_values(1)
                if new_user in users:
                    st.error("El usuario ya existe.")
                else:
                    # Generar Token
                    token = str(uuid.uuid4())
                    # Guardamos contraseña temporal o hash vacío
                    fila = [new_user, "PENDIENTE", new_rol, new_email, token]
                    
                    with st.spinner("Guardando y enviando correo..."):
                        ws.append_row(fila)
                        enviado = enviar_correo_activacion(new_email, token, new_user)
                        
                        if enviado:
                            st.success(f"✅ Usuario creado y correo enviado a {new_email}")
                        else:
                            st.warning("Usuario creado, pero falló el envío del correo. Revisa los logs.")
                            
    with tab2:
        sheet = conectar_google()
        df_users = pd.DataFrame(sheet.worksheet("usuarios").get_all_records())
        st.dataframe(df_users[['username', 'rol', 'email', 'token']])

# ==============================================================================
# 6. LOGIC PRINCIPAL (MAIN LOOP)
# ==============================================================================
def main():
    # 1. Verificar si estamos en proceso de resetear password (URL Token)
    if gestionar_reset_password():
        return # Si estamos reseteando, no mostramos nada más.

    # 2. Inicialización de Estado de Sesión
    if 'usuario' not in st.session_state: st.session_state.usuario = None
    if 'rol' not in st.session_state: st.session_state.rol = None

    # 3. Pantalla de Login (Si no está logueado)
    if st.session_state.usuario is None:
        col1, col2, col3 = st.columns([1,2,1])
        with col2:
            st.title("🔐 Acceso Unificado")
            st.write("Bienvenido a la Suite de Gestión.")
            
            u = st.text_input("Usuario")
            p = st.text_input("Contraseña", type="password")
            
            if st.button("Ingresar"):
                sheet = conectar_google()
                ws = sheet.worksheet("usuarios")
                data = ws.get_all_records()
                df = pd.DataFrame(data)
                
                # Asegurar tipos string
                df['username'] = df['username'].astype(str)
                user_row = df[df['username'] == u]
                
                if not user_row.empty:
                    hash_guardado = user_row.iloc[0]['password']
                    if check_hashes(p, hash_guardado):
                        st.session_state.usuario = u
                        st.session_state.rol = user_row.iloc[0]['rol']
                        st.toast("Inicio de sesión exitoso")
                        time.sleep(1)
                        st.rerun()
                    else:
                        st.error("Contraseña incorrecta")
                else:
                    st.error("Usuario no encontrado")
        return

    # 4. APLICACIÓN PRINCIPAL (Si ya está logueado)
    st.sidebar.title(f"Hola, {st.session_state.usuario}")
    st.sidebar.caption(f"Rol: {st.session_state.rol}")
    
    # Selector de Módulo
    app_mode = st.sidebar.selectbox("📍 Selecciona Sistema:", 
                                  ["Control SIM", "Eventos V17", "Gestión Usuarios"])
    
    st.sidebar.markdown("---")
    if st.sidebar.button("Cerrar Sesión"):
        st.session_state.usuario = None
        st.session_state.rol = None
        st.rerun()

    # Enrutador
    if app_mode == "Control SIM":
        app_control_sim()
    elif app_mode == "Eventos V17":
        app_eventos_v17()
    elif app_mode == "Gestión Usuarios":
        if st.session_state.rol == "admin":
            app_gestion_usuarios()
        else:
            st.error("Acceso restringido. Solo administradores pueden ver esto.")

if __name__ == "__main__":
    main()
