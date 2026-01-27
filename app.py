import streamlit as st
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
import time
import hashlib
import io
import pytz
import uuid
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ==============================================================================
# 1. CONFIGURACIÓN GLOBAL
# ==============================================================================
st.set_page_config(page_title="Control SIM Cloud", page_icon="☁️", layout="wide")

# Constantes
NOMBRE_HOJA = "Base de Datos SIMs"
SCOPE = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
KEY_FILE = 'credenciales.json'

# ==============================================================================
# 2. FUNCIONES DE UTILIDAD (Conexión, Seguridad, Email)
# ==============================================================================

def make_hashes(password):
    return hashlib.sha256(str.encode(password)).hexdigest()

def check_hashes(password, hashed_text):
    if make_hashes(password) == hashed_text:
        return True
    return False

def obtener_hora_actual():
    """Función auxiliar para hora local"""
    zona_seleccionada = st.session_state.get('zona_horaria', 'America/Guatemala')
    try:
        tz = pytz.timezone(zona_seleccionada)
        fecha_ajustada = datetime.now(tz)
        return fecha_ajustada.strftime("%Y-%m-%d %H:%M:%S")
    except:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def refrescar_pagina(segundos=3):
    time.sleep(segundos)
    st.rerun()

def conectar_google():
    """Conexión robusta a Google Sheets compatible con Secrets de Nube"""
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
        if "429" in str(e):
            st.warning("⏳ Esperando a Google (Límite de velocidad)...")
            time.sleep(5)
            st.rerun()
        else:
            st.error(f"Error conectando a Google: {e}")
            st.stop()

# --- FUNCIONES DE EMAIL ---
def enviar_correo_sistema(email_destino, asunto, mensaje_html):
    """Función genérica para enviar correos"""
    try:
        EMAIL_EMISOR = st.secrets["email"]["address"]
        EMAIL_PASS = st.secrets["email"]["password"]
        
        msg = MIMEMultipart()
        msg['From'] = EMAIL_EMISOR
        msg['To'] = email_destino
        msg['Subject'] = asunto

        msg.attach(MIMEText(mensaje_html, 'html')) # Enviamos como HTML para mejor formato

        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(EMAIL_EMISOR, EMAIL_PASS)
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        st.error(f"Error enviando correo: {e}")
        return False

def enviar_link_activacion(email_destino, token, nombre):
    BASE_URL = st.secrets["email"].get("base_url", "http://localhost:8501")
    link = f"{BASE_URL}/?token_reset={token}"
    
    cuerpo = f"""
    <h3>Bienvenido/a {nombre}</h3>
    <p>Se ha creado una cuenta para ti en el Sistema de Control SIM.</p>
    <p>Para definir tu contraseña y acceder, haz clic en el siguiente enlace:</p>
    <p><a href="{link}">Configurar mi contraseña</a></p>
    <br>
    <p>Si el enlace no funciona, copia y pega esto en tu navegador:</p>
    <p>{link}</p>
    """
    return enviar_correo_sistema(email_destino, "🔐 Activa tu cuenta - Control SIM", cuerpo)

def enviar_link_recuperacion(email_destino, token, nombre):
    BASE_URL = st.secrets["email"].get("base_url", "http://localhost:8501")
    link = f"{BASE_URL}/?token_reset={token}"
    
    cuerpo = f"""
    <h3>Hola {nombre}</h3>
    <p>Has solicitado recuperar tu contraseña.</p>
    <p>Haz clic abajo para crear una nueva:</p>
    <p><a href="{link}">Restablecer Contraseña</a></p>
    <p>Si no fuiste tú, ignora este mensaje.</p>
    """
    return enviar_correo_sistema(email_destino, "🔄 Recuperación de Contraseña", cuerpo)

def gestionar_reset_password():
    token_url = st.query_params.get("token_reset", None)
    if token_url:
        st.info("🔄 Gestión de Credenciales")
        sheet = conectar_google()
        ws = sheet.worksheet("usuarios")
        data = ws.get_all_records()
        df = pd.DataFrame(data)
        
        # Filtramos como string para evitar errores
        df['token'] = df['token'].astype(str)
        usuario_encontrado = df[df['token'] == token_url]
        
        if not usuario_encontrado.empty:
            user_row = usuario_encontrado.iloc[0]
            st.write(f"Hola **{user_row['nombre']}**, por favor define tu nueva contraseña.")
            
            with st.form("reset_pass"):
                p1 = st.text_input("Nueva Contraseña", type="password")
                p2 = st.text_input("Confirmar Contraseña", type="password")
                if st.form_submit_button("Guardar y Acceder"):
                    if p1 == p2 and len(p1) > 4:
                        cell = ws.find(token_url)
                        # Actualizar pass y borrar token
                        # COLUMNAS: email(1), password(2), rol(3), nombre(4), token(5)
                        ws.update_cell(cell.row, 2, make_hashes(p1)) 
                        ws.update_cell(cell.row, 5, "") 
                        st.success("¡Contraseña actualizada! Redirigiendo...")
                        st.query_params.clear()
                        time.sleep(2)
                        st.rerun()
                    else:
                        st.error("Las contraseñas no coinciden o son muy cortas (min 5).")
        else:
            st.error("Este enlace ya fue usado o ha expirado.")
            if st.button("Ir al Inicio de Sesión"):
                st.query_params.clear(); st.rerun()
        return True
    return False

# ==============================================================================
# 3. LÓGICA DE NEGOCIO SIMS
# ==============================================================================

@st.cache_data(ttl=10)
def leer_datos(pestaña):
    sheet = conectar_google()
    worksheet = sheet.worksheet(pestaña)
    data = worksheet.get_all_records()
    if not data:
        if pestaña == "sims":
            return pd.DataFrame(columns=['iccid', 'numero_linea', 'cliente', 'placa', 'imei', 'tipo_plan', 'pais', 'costo_q', 'costo_d', 'estado', 'fecha_registro'])
        return pd.DataFrame()
    return pd.DataFrame(data)

def limpiar_cache():
    st.cache_data.clear()

def escribir_fila(pestaña, fila_lista):
    sheet = conectar_google()
    worksheet = sheet.worksheet(pestaña)
    worksheet.append_row(fila_lista)
    limpiar_cache()
    return True

def escribir_lote(pestaña, lista_de_filas):
    if not lista_de_filas: return True
    sheet = conectar_google()
    worksheet = sheet.worksheet(pestaña)
    worksheet.append_rows(lista_de_filas)
    return True

def actualizar_sim_completa(iccid, datos_dict):
    sheet = conectar_google()
    worksheet = sheet.worksheet("sims")
    try:
        cell = worksheet.find(str(iccid))
        row_num = cell.row
        updates = [
            {'range': f'B{row_num}', 'values': [[datos_dict['numero_linea']]]},
            {'range': f'C{row_num}', 'values': [[datos_dict['cliente']]]},
            {'range': f'D{row_num}', 'values': [[datos_dict['placa']]]},
            {'range': f'E{row_num}', 'values': [[datos_dict['imei']]]},
            {'range': f'F{row_num}', 'values': [[datos_dict['tipo_plan']]]},
            {'range': f'G{row_num}', 'values': [[datos_dict['pais']]]},
            {'range': f'H{row_num}', 'values': [[datos_dict['costo_q']]]},
            {'range': f'I{row_num}', 'values': [[datos_dict['costo_d']]]},
            {'range': f'J{row_num}', 'values': [[datos_dict['estado']]]},
        ]
        worksheet.batch_update(updates)
        limpiar_cache()
        return True
    except Exception as e:
        st.error(f"Error actualizando: {e}")
        return False

def actualizar_celda_sim(iccid, columna_nombre, nuevo_valor):
    sheet = conectar_google()
    worksheet = sheet.worksheet("sims")
    try:
        cell = worksheet.find(str(iccid))
        header = worksheet.find(columna_nombre)
        worksheet.update_cell(cell.row, header.col, nuevo_valor)
        limpiar_cache()
        return True
    except:
        return False

# --- LÓGICA FINANCIERA Y CARGA MASIVA ---
def limpiar_moneda(valor):
    if isinstance(valor, (int, float)): return float(valor)
    valor = str(valor).strip().replace("Q", "").replace("$", "")
    if "," in valor and "." in valor: valor = valor.replace(",", "")
    elif "," in valor: valor = valor.replace(",", ".")
    try: return float(valor)
    except: return 0.0

def registrar_sim(datos, usuario):
    df = leer_datos("sims")
    if 'iccid' in df.columns:
        df['iccid'] = df['iccid'].astype(str)
        if str(datos['iccid']) in df['iccid'].values:
            return False
    linea = str(datos['numero_linea']) if datos['numero_linea'] and str(datos['numero_linea']).lower() != 'nan' else ""
    cliente = str(datos['cliente']) if datos['cliente'] and str(datos['cliente']).lower() != 'nan' else ""
    estado = "Activa" if linea and cliente else "Botiquin"
    fecha = obtener_hora_actual()
    fila = [str(datos['iccid']), linea, cliente, str(datos['placa']), str(datos['imei']),
            str(datos['tipo_plan']), str(datos['pais']), datos['costo_q'], datos['costo_d'],
            estado, fecha]
    escribir_fila("sims", fila)
    escribir_fila("historial", [str(datos['iccid']), "Creacion", f"SIM creada como {estado}", usuario, fecha])
    return True

def procesar_carga_masiva_turbo(df_limpio, usuario):
    df_limpio = df_limpio.fillna("")
    df_limpio['iccid'] = df_limpio['iccid'].astype(str).str.replace(".0", "", regex=False)
    df_db = leer_datos("sims")
    iccids_existentes = set()
    if 'iccid' in df_db.columns:
        iccids_existentes = set(df_db['iccid'].astype(str).tolist())
    nuevas_filas_sims = []
    nuevas_filas_historial = []
    fecha_hoy = obtener_hora_actual()
    correctos = 0
    duplicados = 0
    for index, row in df_limpio.iterrows():
        iccid_val = str(row['iccid']).strip()
        if not iccid_val or iccid_val.lower() == 'nan': continue
        if iccid_val in iccids_existentes:
            duplicados += 1; continue
        n_linea = str(row['numero_linea']).replace(".0", "")
        n_cliente = str(row['cliente'])
        estado = "Activa" if n_linea and n_cliente and n_linea.lower()!='nan' else "Botiquin"
        cq = limpiar_moneda(row['costo_q'])
        cd = limpiar_moneda(row['costo_d'])
        fila_sim = [iccid_val, n_linea, n_cliente, str(row['placa']), str(row['imei']), 
                    str(row['tipo_plan']), str(row['pais']), cq, cd, estado, fecha_hoy]
        fila_hist = [iccid_val, "Creacion Masiva", f"Carga Excel. Estado: {estado}", usuario, fecha_hoy]
        nuevas_filas_sims.append(fila_sim)
        nuevas_filas_historial.append(fila_hist)
        iccids_existentes.add(iccid_val)
        correctos += 1
    if nuevas_filas_sims:
        escribir_lote("sims", nuevas_filas_sims)
        escribir_lote("historial", nuevas_filas_historial)
        limpiar_cache()
    return correctos, duplicados

def actualizar_datos_sim(iccid, datos, usuario):
    linea = str(datos['numero_linea'])
    cliente = str(datos['cliente'])
    nuevo_estado = "Activa" if linea and cliente else "Botiquin"
    datos_full = datos.copy()
    datos_full['estado'] = nuevo_estado
    if actualizar_sim_completa(iccid, datos_full):
        fecha = obtener_hora_actual()
        escribir_fila("historial", [iccid, "Actualizacion", f"Estado: {nuevo_estado}", usuario, fecha])
        return True
    return False

def traslado_sim(iccid_antiguo, iccid_nuevo, usuario):
    df = leer_datos("sims")
    df['iccid'] = df['iccid'].astype(str)
    row_old = df[df['iccid'] == str(iccid_antiguo)]
    if row_old.empty: return False, "ICCID Viejo no existe"
    row_new = df[df['iccid'] == str(iccid_nuevo)]
    if row_new.empty: return False, "ICCID Nuevo no existe"
    if row_new.iloc[0]['estado'] != 'Botiquin': return False, "Nueva SIM no es Botiquín"
    datos_old = row_old.iloc[0]
    datos_new = {
        'numero_linea': datos_old['numero_linea'], 'cliente': datos_old['cliente'],
        'placa': datos_old['placa'], 'imei': datos_old['imei'], 'tipo_plan': datos_old['tipo_plan'],
        'pais': datos_old['pais'], 'costo_q': datos_old['costo_q'], 'costo_d': datos_old['costo_d'],
        'estado': 'Activa'
    }
    actualizar_sim_completa(iccid_nuevo, datos_new)
    actualizar_celda_sim(iccid_antiguo, "estado", "Retirada")
    actualizar_celda_sim(iccid_antiguo, "numero_linea", "SIM RETIRADA")
    fecha = obtener_hora_actual()
    escribir_fila("historial", [iccid_nuevo, "Traslado Entrada", f"De {iccid_antiguo}", usuario, fecha])
    escribir_fila("historial", [iccid_antiguo, "Traslado Salida", f"A {iccid_nuevo}", usuario, fecha])
    return True, "Traslado Exitoso"

def cancelar_servicio(iccid, usuario, motivo):
    if actualizar_celda_sim(iccid, "estado", "Cancelada"):
        fecha = obtener_hora_actual()
        escribir_fila("historial", [iccid, "Cancelacion", f"Motivo: {motivo}", usuario, fecha])
        return True
    return False

# ==============================================================================
# 4. APLICACIÓN PRINCIPAL (UI)
# ==============================================================================

def app_control_sim():
    # --- Sidebar ---
    st.sidebar.markdown("### 📱 Menú SIMs")
    
    # Configuración de Zona Horaria
    zonas_disponibles = ["America/Guatemala", "America/Bogota", "America/Mexico_City", "UTC"]
    st.session_state.zona_horaria = st.sidebar.selectbox("Zona Horaria:", zonas_disponibles, index=0)
    st.sidebar.caption(f"Hora: {obtener_hora_actual()}")
    
    menu_ops = ["Dashboard", "Reportes"]
    if st.session_state.rol == "admin":
        menu_ops = ["Dashboard", "Registrar SIM", "Actualizar Datos", "Traslados", "Cancelar/Gestionar", "Reportes"]
    
    choice = st.sidebar.radio("Opciones:", menu_ops)

    if 'form_id' not in st.session_state: st.session_state.form_id = 0

    # --- PANTALLA DASHBOARD ---
    if choice == "Dashboard":
        st.title("📊 Tablero de Control")
        df = leer_datos("sims")
        if not df.empty and 'estado' in df.columns:
            # Tarjetas de Inventario (Para todos)
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Total Inventario", len(df))
            c2.metric("Activas", len(df[df['estado']=='Activa']))
            c3.metric("Botiquín", len(df[df['estado']=='Botiquin']))
            c4.metric("Canceladas", len(df[df['estado']=='Cancelada']))

            # Financiero (SOLO ADMIN)
            if st.session_state.rol == 'admin':
                st.markdown("---")
                st.subheader("💰 Facturación Mensual Estimada")
                df['costo_q_calc'] = df['costo_q'].apply(limpiar_moneda)
                df['costo_d_calc'] = df['costo_d'].apply(limpiar_moneda)
                df_activas = df[df['estado'] == 'Activa']
                total_q = df_activas['costo_q_calc'].sum()
                total_d = df_activas['costo_d_calc'].sum()
                k1, k2 = st.columns(2)
                k1.metric("Total Quetzales (Q)", f"Q {total_q:,.2f}")
                k2.metric("Total Dólares ($)", f"$ {total_d:,.2f}")
        else:
            st.info("Cargando datos...")

    # --- PANTALLA REGISTRAR ---
    elif choice == "Registrar SIM":
        st.subheader("➕ Gestión Inventario")
        tab1, tab2 = st.tabs(["Manual", "Carga Masiva"])
        
        with tab1:
            kf = str(st.session_state.form_id)
            with st.form("new"):
                c1, c2 = st.columns(2)
                iccid = c1.text_input("ICCID*", key=f"i_{kf}")
                linea = c2.text_input("Línea", key=f"l_{kf}")
                cli = c1.text_input("Cliente", key=f"c_{kf}")
                pla = c2.text_input("Placa", key=f"p_{kf}")
                ime = c1
