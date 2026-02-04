import streamlit as st
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime, date
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

# Constantes de Conexión
NOMBRE_HOJA = "Base de Datos SIMs"
SCOPE = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
KEY_FILE = 'credenciales.json'

# Lista Maestra de Países
LISTA_PAISES = ["Guatemala", "El Salvador", "Honduras", "Nicaragua", "Costa Rica", "Panamá", "México", "Colombia","Republica Dominicana","Ecuador"]

# ==============================================================================
# 2. FUNCIONES DE UTILIDAD, SEGURIDAD Y FORMATO
# ==============================================================================

def make_hashes(password):
    return hashlib.sha256(str.encode(password)).hexdigest()

def check_hashes(password, hashed_text):
    if make_hashes(password) == hashed_text: return True
    return False

def obtener_hora_actual():
    zona_seleccionada = st.session_state.get('zona_horaria', 'America/Guatemala')
    try:
        tz = pytz.timezone(zona_seleccionada)
        return datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
    except:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def refrescar_pagina(segundos=3):
    time.sleep(segundos)
    st.rerun()

def conectar_google():
    try:
        if "gcp_service_account" in st.secrets:
            creds_dict = dict(st.session_state.secrets["gcp_service_account"]) if "secrets" in st.session_state else st.secrets["gcp_service_account"]
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, SCOPE)
        else:
            creds = ServiceAccountCredentials.from_json_keyfile_name(KEY_FILE, SCOPE)
        client = gspread.authorize(creds)
        return client.open(NOMBRE_HOJA)
    except Exception as e:
        if "429" in str(e):
            st.warning("⏳ Esperando a Google (Límite de velocidad)...")
            time.sleep(5); st.rerun()
        else:
            st.error(f"Error conectando a Google: {e}"); st.stop()

# --- FUNCIÓN CRÍTICA: LIMPIEZA DE MONEDA ---
def limpiar_moneda(valor):
    """
    Convierte texto a número (Float) manejando formatos latinos.
    Ej: "Q 50,00" -> 50.00 | "1.500,00" -> 1500.00
    """
    if pd.isna(valor) or str(valor).strip() == "": return 0.0
    if isinstance(valor, (int, float)): return float(valor)
    
    # Quitar símbolos
    valor = str(valor).strip().upper().replace("Q", "").replace("$", "").replace(" ", "")
    
    # Lógica de puntos y comas
    if "." in valor and "," in valor: 
        valor = valor.replace(".", "").replace(",", ".") # 1.500,00 -> 1500.00
    elif "," in valor:
        valor = valor.replace(",", ".") # 50,00 -> 50.00
    
    try: return float(valor)
    except: return 0.0

# --- EMAILS ---
def enviar_correo_sistema(email_destino, asunto, mensaje_html):
    try:
        EMAIL_EMISOR = st.secrets["email"]["address"]
        EMAIL_PASS = st.secrets["email"]["password"]
        msg = MIMEMultipart()
        msg['From'] = EMAIL_EMISOR
        msg['To'] = email_destino
        msg['Subject'] = asunto
        msg.attach(MIMEText(mensaje_html, 'html'))
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
    cuerpo = f"""<h3>Bienvenido {nombre}</h3><p>Activa tu cuenta aquí: <a href="{link}">{link}</a></p>"""
    return enviar_correo_sistema(email_destino, "🔐 Activa tu cuenta", cuerpo)

def enviar_link_recuperacion(email_destino, token, nombre):
    BASE_URL = st.secrets["email"].get("base_url", "http://localhost:8501")
    link = f"{BASE_URL}/?token_reset={token}"
    cuerpo = f"""<h3>Hola {nombre}</h3><p>Restablece tu clave aquí: <a href="{link}">{link}</a></p>"""
    return enviar_correo_sistema(email_destino, "🔄 Recuperación Clave", cuerpo)

def gestionar_reset_password():
    token_url = st.query_params.get("token_reset", None)
    if token_url:
        st.info("🔄 Gestión de Credenciales")
        ws = conectar_google().worksheet("usuarios")
        df = pd.DataFrame(ws.get_all_records())
        df['token'] = df['token'].astype(str)
        user = df[df['token'] == token_url]
        if not user.empty:
            st.write(f"Hola **{user.iloc[0]['nombre']}**, define tu contraseña.")
            with st.form("reset"):
                p1 = st.text_input("Nueva Contraseña", type="password")
                p2 = st.text_input("Confirmar", type="password")
                if st.form_submit_button("Guardar"):
                    if p1 == p2 and len(p1) > 4:
                        cell = ws.find(token_url)
                        ws.update_cell(cell.row, 2, make_hashes(p1))
                        ws.update_cell(cell.row, 5, "")
                        st.success("Actualizado."); st.query_params.clear(); time.sleep(2); st.rerun()
                    else: st.error("Error en contraseñas.")
        else: st.error("Token inválido.")
        return True
    return False

# ==============================================================================
# 3. LÓGICA DE NEGOCIO (DATOS, SIMS, CLIENTES)
# ==============================================================================

@st.cache_data(ttl=10)
def leer_datos(pestaña):
    """
    FUNCIÓN BLINDADA V6 (LECTURA ROBUSTA):
    Usa get_all_values() para leer la hoja completa como matriz de texto.
    Esto soluciona el problema de filas ocultas por espacios vacíos intermedios.
    """
    try:
        sheet = conectar_google()
        worksheet = sheet.worksheet(pestaña)
        
        # Leemos TODO como texto plano (lista de listas)
        raw_data = worksheet.get_all_values()
        
        # Validaciones básicas
        if not raw_data or len(raw_data) < 2:
            if pestaña == "sims":
                return pd.DataFrame(columns=['iccid', 'numero_linea', 'cliente', 'placa', 'imei', 'tipo_plan', 'pais', 'costo_q', 'costo_d', 'estado', 'fecha_registro'])
            if pestaña == "clientes":
                return pd.DataFrame(columns=['nombre'])
            return pd.DataFrame()

        # La primera fila son los encabezados
        headers = raw_data[0]
        rows = raw_data[1:]
        
        df = pd.DataFrame(rows, columns=headers)

        # 1. Limpieza General (Trim de espacios)
        df = df.astype(str).apply(lambda x: x.str.strip())

        # 2. Limpieza de ICCID
        if 'iccid' in df.columns:
            df['iccid'] = df['iccid'].str.replace(r'\.0$', '', regex=True)
            # Filtramos filas basura que no tengan ICCID
            df = df[df['iccid'] != '']

        # 3. Limpieza de País
        if 'pais' in df.columns:
            df['pais'] = df['pais'].str.title()

        # 4. LIMPIEZA DE COSTOS (Conversión a Float)
        if 'costo_q' in df.columns:
            df['costo_q'] = df['costo_q'].apply(limpiar_moneda)
        if 'costo_d' in df.columns:
            df['costo_d'] = df['costo_d'].apply(limpiar_moneda)

        return df

    except Exception as e:
        print(f"Error leyendo {pestaña}: {e}") 
        return pd.DataFrame()

def limpiar_cache(): st.cache_data.clear()

def escribir_fila(pestaña, fila):
    conectar_google().worksheet(pestaña).append_row(fila)
    limpiar_cache()

def escribir_lote(pestaña, filas):
    if filas: conectar_google().worksheet(pestaña).append_rows(filas)
    limpiar_cache()

# --- LÓGICA CLIENTES ---
def obtener_lista_clientes():
    df = leer_datos("clientes")
    if not df.empty and 'nombre' in df.columns:
        lista = sorted(df['nombre'].astype(str).unique().tolist())
        return [c for c in lista if c.strip() != ""]
    return []

def crear_nuevo_cliente(nombre_cliente):
    df = leer_datos("clientes")
    if not df.empty and 'nombre' in df.columns:
        if nombre_cliente in df['nombre'].values: return False, "Cliente ya existe."
    escribir_fila("clientes", [nombre_cliente])
    return True, "Cliente creado."

# --- ACTUALIZACIÓN Y TRANSACCIONES ---
def actualizar_sim_completa(iccid, datos_dict):
    ws = conectar_google().worksheet("sims")
    try:
        cell = ws.find(str(iccid))
        r = cell.row
        vals = [
            {'range': f'B{r}', 'values': [[datos_dict['numero_linea']]]},
            {'range': f'C{r}', 'values': [[datos_dict['cliente']]]},
            {'range': f'D{r}', 'values': [[datos_dict['placa']]]},
            {'range': f'E{r}', 'values': [[datos_dict['imei']]]},
            {'range': f'F{r}', 'values': [[datos_dict['tipo_plan']]]},
            {'range': f'G{r}', 'values': [[datos_dict['pais']]]},
            {'range': f'H{r}', 'values': [[datos_dict['costo_q']]]},
            {'range': f'I{r}', 'values': [[datos_dict['costo_d']]]},
            {'range': f'J{r}', 'values': [[datos_dict['estado']]]}
        ]
        ws.batch_update(vals)
        limpiar_cache(); return True
    except: return False

def actualizar_celda_sim(iccid, col, val):
    try:
        ws = conectar_google().worksheet("sims")
        cell = ws.find(str(iccid))
        h = ws.find(col)
        ws.update_cell(cell.row, h.col, val)
        limpiar_cache(); return True
    except: return False

def procesar_actualizacion_masiva(df_updates, usuario, solo_vacios=True):
    df_db = leer_datos("sims")
    if df_db.empty: return 0, "DB vacía"
    df_db['iccid'] = df_db['iccid'].astype(str)
    df_updates['iccid'] = df_updates['iccid'].astype(str).str.replace(".0", "", regex=False)
    
    ws = conectar_google().worksheet("sims")
    mapa_cols = {'numero_linea':2, 'cliente':3, 'placa':4, 'imei':5, 'tipo_plan':6, 'pais':7, 'costo_q':8, 'costo_d':9}
    iccid_a_fila = {str(ic): i+2 for i, ic in enumerate(df_db['iccid'])}
    
    batch_ops = []
    log_h = []
    hoy = obtener_hora_actual()
    count = 0

    for _, row in df_updates.iterrows():
        ic = str(row['iccid']).strip()
        if ic in iccid_a_fila:
            r_idx = iccid_a_fila[ic]
            orig_row = df_db[df_db['iccid'] == ic].iloc[0]
            cambios_txt = []
            for col, c_idx in mapa_cols.items():
                if col in row:
                    new_v = str(row[col]).strip()
                    old_v = str(orig_row[col]).strip()
                    if new_v and new_v.lower() != 'nan':
                        aplicar = False
                        if solo_vacios:
                            if not old_v: aplicar = True
                        else:
                            if new_v != old_v: aplicar = True
                        if aplicar:
                            batch_ops.append({'range': gspread.utils.rowcol_to_a1(r_idx, c_idx), 'values': [[new_v]]})
                            cambios_txt.append(col)
            if cambios_txt:
                count += 1
                log_h.append([ic, "Edición Masiva", f"Campos: {','.join(cambios_txt)}", usuario, hoy])

    if batch_ops:
        try:
            ws.batch_update(batch_ops)
            escribir_lote("historial", log_h)
            return count, f"Actualizadas {count} SIMs."
        except Exception as e: return 0, str(e)
    return 0, "Sin cambios."

def registrar_sim(datos, usuario):
    df = leer_datos("sims")
    if 'iccid' in df.columns and str(datos['iccid']) in df['iccid'].astype(str).values: return False
    l = str(datos['numero_linea']) if datos['numero_linea'] else ""
    c = str(datos['cliente']) if datos['cliente'] else ""
    e = "Activa" if l and c else "Botiquin"
    f = obtener_hora_actual()
    fila = [str(datos['iccid']), l, c, str(datos['placa']), str(datos['imei']),
            str(datos['tipo_plan']), str(datos['pais']), limpiar_moneda(datos['costo_q']), limpiar_moneda(datos['costo_d']), e, f]
    escribir_fila("sims", fila)
    escribir_fila("historial", [str(datos['iccid']), "Creacion", f"Estado: {e}", usuario, f])
    return True

def procesar_carga_masiva_turbo(df_limpio, usuario):
    df_limpio = df_limpio.fillna("")
    df_limpio['iccid'] = df_limpio['iccid'].astype(str).str.replace(".0", "", regex=False)
    df_db = leer_datos("sims")
    existentes = set(df_db['iccid'].astype(str).tolist()) if not df_db.empty else set()
    nuevas, hist = [], []
    hoy = obtener_hora_actual()
    c, d = 0, 0
    for _, row in df_limpio.iterrows():
        ic = str(row['iccid']).strip()
        if not ic or ic in existentes: d += 1; continue
        l, cli = str(row['numero_linea']).replace(".0",""), str(row['cliente'])
        est = "Activa" if l and cli else "Botiquin"
        nuevas.append([ic, l, cli, str(row['placa']), str(row['imei']), str(row['tipo_plan']), str(row['pais']), limpiar_moneda(row['costo_q']), limpiar_moneda(row['costo_d']), est, hoy])
        hist.append([ic, "Creacion Masiva", f"Estado: {est}", usuario, hoy])
        existentes.add(ic)
        c += 1
    if nuevas:
        escribir_lote("sims", nuevas)
        escribir_lote("historial", hist)
    return c, d

def actualizar_datos_sim(iccid, datos, usuario):
    linea = str(datos['numero_linea'])
    cliente = str(datos['cliente'])
    nuevo_estado = "Activa" if linea and cliente else "Botiquin"
    datos_full = datos.copy()
    datos_full['estado'] = nuevo_estado
    if actualizar_sim_completa(iccid, datos_full):
        f = obtener_hora_actual()
        escribir_fila("historial", [iccid, "Actualizacion", f"Estado: {nuevo_estado}", usuario, f])
        return True
    return False

def traslado_sim(ic_old, ic_new, usuario):
    df = leer_datos("sims")
    df['iccid'] = df['iccid'].astype(str)
    r_old = df[df['iccid'] == str(ic_old)]
    r_new = df[df['iccid'] == str(ic_new)]
    if r_old.empty or r_new.empty: return False, "ICCID no existe"
    if r_new.iloc[0]['estado'] != 'Botiquin': return False, "Nueva SIM no está libre"
    dat = r_old.iloc[0]
    new_d = {
        'numero_linea': dat['numero_linea'], 'cliente': dat['cliente'],
        'placa': dat['placa'], 'imei': dat['imei'], 'tipo_plan': dat['tipo_plan'],
        'pais': dat['pais'], 'costo_q': dat['costo_q'], 'costo_d': dat['costo_d'],
        'estado': 'Activa'
    }
    actualizar_sim_completa(ic_new, new_d)
    actualizar_celda_sim(ic_old, "estado", "Retirada")
    actualizar_celda_sim(ic_old, "numero_linea", "SIM RETIRADA")
    f = obtener_hora_actual()
    escribir_fila("historial", [ic_new, "Traslado In", f"Desde {ic_old}", usuario, f])
    escribir_fila("historial", [ic_old, "Traslado Out", f"Hacia {ic_new}", usuario, f])
    return True, "Exitoso"

def cancelar_servicio(iccid, usuario, motivo):
    if actualizar_celda_sim(iccid, "estado", "Cancelada"):
        escribir_fila("historial", [iccid, "Cancelacion", f"Motivo: {motivo}", usuario, obtener_hora_actual()])
        return True
    return False

def eliminar_usuario_db(email):
    try:
        ws = conectar_google().worksheet("usuarios")
        c = ws.find(email)
        ws.delete_rows(c.row)
        return True
    except: return False

# ==============================================================================
# 4. MÓDULOS UI (INTERFACES)
# ==============================================================================

def app_gestion_clientes():
    st.subheader("🏢 Directorio de Clientes")
    t1, t2 = st.tabs(["Nuevo Cliente", "Listado"])
    with t1:
        with st.form("add_c"):
            nm = st.text_input("Nombre Empresa")
            if st.form_submit_button("Guardar"):
                if nm: 
                    ok, m = crear_nuevo_cliente(nm.strip())
                    if ok: st.success(m); refrescar_pagina(1)
                    else: st.warning(m)
    with t2:
        st.dataframe(leer_datos("clientes"), use_container_width=True)

def app_gestion_usuarios():
    st.header("👤 Administración de Usuarios")
    st.markdown("""<style>.stSelectbox {margin-bottom: 20px;} div[data-testid="stForm"] {border: 1px solid #e0e0e0; padding: 20px; border-radius: 10px;}</style>""", unsafe_allow_html=True)
    tab_crear, tab_gestionar = st.tabs(["➕ Crear Nuevo", "⚙️ Gestionar Existentes"])
    
    with tab_crear:
        st.info("💡 Se enviará correo de activación.")
        with st.form("form_crear_usuario", clear_on_submit=True):
            col_a, col_b = st.columns(2)
            with col_a:
                new_email = st.text_input("📧 Correo")
                new_nombre = st.text_input("👤 Nombre")
            with col_b:
                new_rol = st.radio("Nivel", ["general", "admin"], horizontal=True)
                new_paises = st.multiselect("🌍 Países", LISTA_PAISES, default=["Guatemala"])
            if st.form_submit_button("✨ Crear Usuario", type="primary"):
                ws = conectar_google().worksheet("usuarios")
                if new_email and new_nombre and new_paises:
                    if new_email in ws.col_values(1): st.error("Existe.")
                    else:
                        token = str(uuid.uuid4())
                        ws.append_row([new_email, "PENDIENTE", new_rol, new_nombre, token, ",".join(new_paises)])
                        if enviar_link_activacion(new_email, token, new_nombre): st.success("✅ Creado.")
                        else: st.warning("Creado, error en correo.")
                else: st.error("Faltan datos.")

    with tab_gestionar:
        ws = conectar_google().worksheet("usuarios")
        data = ws.get_all_records()
        df = pd.DataFrame(data)
        if not df.empty:
            st.markdown("#### 🔍 Buscar Usuario")
            opciones = [f"{row['nombre']} ({row['email']})" for i, row in df.iterrows()]
            seleccion = st.selectbox("Seleccione:", opciones, index=None, placeholder="Buscar...")
            if seleccion:
                email_sel = seleccion.split("(")[-1].replace(")", "")
                user_data = df[df['email'] == email_sel].iloc[0]
                st.divider(); st.subheader(f"✏️ Editando: {user_data['nombre']}")
                with st.container(border=True):
                    with st.form("form_edicion"):
                        c1, c2 = st.columns(2)
                        with c1:
                            st.text_input("Correo", value=user_data['email'], disabled=True)
                            edit_nombre = st.text_input("Nombre", value=user_data['nombre'])
                        with c2:
                            idx_rol = 0 if user_data['rol'] == 'general' else 1
                            edit_rol = st.radio("Rol", ["general", "admin"], index=idx_rol, horizontal=True)
                            curr_p = [p.strip() for p in str(user_data['paises']).split(",") if p.strip() in LISTA_PAISES]
                            edit_paises = st.multiselect("Países", LISTA_PAISES, default=curr_p)
                        if st.form_submit_button("💾 Guardar Cambios", type="primary"):
                            try:
                                cell = ws.find(email_sel); r = cell.row
                                updates = [
                                    {'range': f'C{r}', 'values': [[edit_rol]]},
                                    {'range': f'D{r}', 'values': [[edit_nombre]]},
                                    {'range': f'F{r}', 'values': [[",".join(edit_paises)]]}
                                ]
                                ws.batch_update(updates)
                                st.success("✅ Actualizado."); time.sleep(1.5); st.rerun()
                            except Exception as e: st.error(f"Error: {e}")
                st.markdown("### 🚫 Zona de Riesgo")
                with st.expander("Eliminar cuenta"):
                    if email_sel == st.session_state.usuario: st.error("No puedes borrarte a ti mismo.")
                    else:
                        chk = st.checkbox("Confirmar eliminación.")
                        if st.button("🔥 Eliminar") and chk:
                            eliminar_usuario_db(email_sel); st.success("Eliminado."); time.sleep(1.5); st.rerun()

def app_control_sim():
    # --- SEGURIDAD Y FILTRO PENDIENTES ---
    paises_user = st.session_state.get('paises_asignados', [])
    if not paises_user:
        if st.session_state.rol == 'admin': paises_user = [] 
        else: paises_user = ["SIN_ACCESO"] 

    st.sidebar.markdown("### 📱 Menú SIMs")
    st.session_state.zona_horaria = st.sidebar.selectbox("Zona Horaria:", ["America/Guatemala", "America/Bogota", "America/Mexico_City", "UTC"])
    st.sidebar.caption(f"Hora: {obtener_hora_actual()}")

    ops = ["Dashboard", "🔍 Consulta SIM", "Reportes"]
    if st.session_state.rol == "admin":
        ops = ["Dashboard", "🔍 Consulta SIM", "Registrar SIM", "Actualizar Datos", "Gestión Clientes", "Traslados", "Cancelar/Gestionar", "Auditoría", "Reportes"]
    
    choice = st.sidebar.radio("Opciones:", ops)
    if 'form_id' not in st.session_state: st.session_state.form_id = 0
    clientes_db = obtener_lista_clientes()

    # --- LÓGICA DE CARGA Y FILTRO (CORREGIDA PARA PENDIENTES) ---
    df_raw = leer_datos("sims")
    
    # 1. Normalizar vacíos a PENDIENTE
    if 'pais' in df_raw.columns:
        df_raw['pais'] = df_raw['pais'].replace("", "PENDIENTE ⚠️")
        df_raw['pais'] = df_raw['pais'].fillna("PENDIENTE ⚠️")

    # 2. Aplicar Filtro: Mis países OR Pendientes
    if paises_user:
        df = df_raw[ (df_raw['pais'].isin(paises_user)) | (df_raw['pais'] == "PENDIENTE ⚠️") ]
    else:
        if st.session_state.rol == 'admin': df = df_raw 
        else: df = pd.DataFrame(columns=df_raw.columns)

    # --- PANTALLAS ---
    if choice == "Dashboard":
        st.title("📊 Tablero de Control")
        
        # SIEMPRE MOSTRAR TARJETAS (Incluso si son 0)
        st.markdown("### Resumen General")
        total_inv = len(df)
        total_activas = len(df[df['estado']=='Activa']) if not df.empty else 0
        total_botiquin = len(df[df['estado']=='Botiquin']) if not df.empty else 0
        total_bajas = len(df[df['estado']=='Cancelada']) if not df.empty else 0
        
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Inventario", total_inv, border=True)
        c2.metric("Activas", total_activas, border=True)
        c3.metric("Botiquín (Stock)", total_botiquin, border=True)
        c4.metric("Bajas / Canceladas", total_bajas, border=True)
        st.markdown("---")
        
        # Validar si hay datos para mostrar gráficos
        if total_inv > 0:
            t1, t2 = st.tabs(["🌍 Análisis Global", "🏢 CRM Cliente"])
            with t1:
                st.subheader("Distribución por País")
                if 'pais' in df.columns:
                    cp = df['pais'].value_counts().reset_index()
                    cp.columns = ["País", "Cantidad"]
                    ga, gb = st.columns([2,1])
                    with ga: st.bar_chart(cp.set_index("País"))
                    with gb: st.dataframe(cp, hide_index=True, use_container_width=True, column_config={"Cantidad": st.column_config.ProgressColumn("Volumen", format="%d", min_value=0, max_value=int(cp['Cantidad'].max()))})
                if st.session_state.rol == 'admin':
                    st.divider(); st.subheader("💰 Finanzas")
                    act = df[df['estado']=='Activa']
                    k1, k2 = st.columns(2)
                    k1.metric("Total Q", f"Q {act['costo_q'].sum():,.2f}", border=True)
                    k2.metric("Total $", f"$ {act['costo_d'].sum():,.2f}", border=True)
            with t2:
                st.subheader("Análisis por Cliente")
                all_cl = sorted(list(set(clientes_db + df['cliente'].unique().tolist())))
                c_sel = st.selectbox("Cliente:", all_cl, index=None, placeholder="Buscar empresa...")
                if c_sel:
                    df_c = df[df['cliente']==c_sel]
                    if not df_c.empty:
                        k1, k2, k3 = st.columns(3)
                        k1.metric("Activas", len(df_c[df_c['estado']=='Activa']), border=True)
                        k2.metric("Botiquín", len(df_c[df_c['estado']=='Botiquin']), border=True)
                        k3.metric("Bajas", len(df_c[df_c['estado']=='Cancelada']), border=True)
                        st.dataframe(df_c[['iccid','numero_linea','placa','estado','pais']], use_container_width=True, hide_index=True)
                    else: st.warning("Sin SIMs asignadas.")
        else:
            st.info("👋 No hay SIMs registradas en tu región o permisos.")

    elif choice == "🔍 Consulta SIM":
        st.subheader("🔍 Buscador Inteligente")
        if not df.empty:
            df['s'] = df['iccid'].astype(str) + " | " + df['cliente'].astype(str) + " (" + df['estado'] + ")"
            sel = st.selectbox("Buscar:", df['s'].tolist(), index=None, placeholder="Escribe ICCID...")
            if sel:
                ic = sel.split(" | ")[0]
                row = df[df['iccid']==ic].iloc[0]
                est = row['estado']
                c1, c2 = st.columns([1,6])
                with c1: st.header("✅" if est=="Activa" else "📦" if est=="Botiquin" else "🚫")
                with c2: st.subheader(f"{est}"); st.write(f"**Línea:** {row['numero_linea']}")
                with st.container(border=True):
                    k1,k2,k3=st.columns(3)
                    k1.write(f"**ICCID:** {ic}")
                    k2.write(f"**Cliente:** {row['cliente']}")
                    k3.write(f"**País:** {row['pais']}")
                    st.divider()
                    k4,k5,k6=st.columns(3)
                    k4.write(f"**Placa:** {row['placa']}")
                    k5.write(f"**IMEI:** {row['imei']}")
                    k6.write(f"**Plan:** {row['tipo_plan']}")
        else: st.warning("No hay datos disponibles para búsqueda.")

    elif choice == "Registrar SIM":
        st.subheader("➕ Registrar")
        t1, t2 = st.tabs(["Manual", "Masiva"])
        with t1:
            kf = str(st.session_state.form_id)
            with st.form("new"):
                c1,c2 = st.columns(2)
                ic = c1.text_input("ICCID*", key=f"i{kf}")
                ln = c2.text_input("Línea", key=f"l{kf}")
                if clientes_db: cl = c1.selectbox("Cliente", clientes_db, index=None, key=f"c{kf}")
                else: cl = c1.text_input("Cliente", key=f"c{kf}")
                pl = c2.text_input("Placa", key=f"p{kf}")
                im = c1.text_input("IMEI", key=f"im{kf}")
                pn = c2.text_input("Plan", key=f"pl{kf}")
                lp = [p for p in LISTA_PAISES if p in paises_user] if paises_user else LISTA_PAISES
                pa = c1.selectbox("País", lp, key=f"pa{kf}")
                cq = c2.number_input("Costo Q", key=f"cq{kf}")
                cd = c1.number_input("Costo $", key=f"cd{kf}")
                if st.form_submit_button("Guardar"):
                    if ic:
                        d = {'iccid': ic, 'numero_linea': ln, 'cliente': cl, 'placa': pl, 'imei': im, 'tipo_plan': pn, 'pais': pa, 'costo_q': cq, 'costo_d': cd}
                        if registrar_sim(d, st.session_state.usuario): st.success("Guardado"); st.session_state.form_id+=1; refrescar_pagina(2)
                        else: st.error("Duplicado")
        with t2:
            upl = st.file_uploader("Excel", type=["xlsx"])
            if upl and st.button("Procesar"):
                c, d = procesar_carga_masiva_turbo(pd.read_excel(upl), st.session_state.usuario)
                st.success(f"Ok: {c} | Dup: {d}")

    elif choice == "Actualizar Datos":
        st.subheader("✏️ Editar")
        if 'ukey' not in st.session_state: st.session_state.ukey = 0
        if 'res' not in st.session_state: st.session_state.res = None
        if st.session_state.res:
            with st.container(border=True):
                st.success("✅ Actualización Exitosa")
                for r in st.session_state.res: st.markdown(f"- {r}")
                if st.button("Aceptar y Nueva Búsqueda", type="primary"): 
                    st.session_state.res=None; st.session_state.ukey+=1; st.rerun()
        else:
            t1, t2 = st.tabs(["Unitario", "Masivo"])
            with t1:
                if not df.empty:
                    df['d'] = df['iccid'].astype(str) + " | " + df['cliente'].astype(str)
                    sel = st.selectbox("Buscar:", df['d'].tolist(), index=None, placeholder="Escribe...", key=f"sb_{st.session_state.ukey}")
                    if sel:
                        ic = sel.split(" | ")[0]
                        cur = df[df['iccid']==ic].iloc[0]
                        st.info(f"Editando: **{ic}**")
                        with st.form("ed"):
                            c1,c2 = st.columns(2)
                            nl = c1.text_input("Línea", value=cur['numero_linea'])
                            idx_c = None
                            if str(cur['cliente']) in clientes_db: idx_c = clientes_db.index(str(cur['cliente']))
                            if clientes_db: nc = c2.selectbox("Cliente", clientes_db, index=idx_c)
                            else: nc = c2.text_input("Cliente", value=cur['cliente'])
                            
                            np = c1.text_input("Placa", value=cur['placa'])
                            nimei = c2.text_input("IMEI", value=cur['imei'])
                            npl = c1.text_input("Plan", value=cur['tipo_plan'])
                            
                            lpe = [p for p in LISTA_PAISES if p in paises_user] if paises_user else LISTA_PAISES
                            idx_p = lpe.index(cur['pais']) if cur['pais'] in lpe else 0
                            npa = c1.selectbox("País", lpe, index=idx_p, help="Si decía 'PENDIENTE', asigna ahora el país correcto.")
                            
                            ncq = c2.number_input("Costo Q", value=float(cur['costo_q']))
                            ncd = c1.number_input("Costo $", value=float(cur['costo_d']))
                            
                            if st.form_submit_button("Actualizar"):
                                ch = []
                                if str(nc)!=str(cur['cliente']): ch.append(f"Cliente: {cur['cliente']} -> {nc}")
                                if str(npa)!=str(cur['pais']): ch.append(f"País: {cur['pais']} -> {npa}")
                                d = {'numero_linea': nl, 'cliente': nc, 'placa': np, 'imei': nimei, 'tipo_plan': npl, 'pais': npa, 'costo_q': ncq, 'costo_d': ncd}
                                if actualizar_datos_sim(ic, d, st.session_state.usuario):
                                    st.session_state.res = ch if ch else ["Datos Guardados"]; st.rerun()
            with t2:
                st.info("Sube Excel con columna 'iccid' y columnas a editar.")
                upl = st.file_uploader("Update Excel", type=["xlsx"])
                mod = st.radio("Modo", ["Rellenar Vacíos", "Sobrescribir Todo"])
                if upl and st.button("Ejecutar Masiva"):
                    cant, msg = procesar_actualizacion_masiva(pd.read_excel(upl), st.session_state.usuario, "Rellenar" in mod)
                    if cant: st.balloons(); st.success(msg)
                    else: st.warning(msg)

    elif choice == "Gestión Clientes": app_gestion_clientes()

    elif choice == "Traslados":
        st.subheader("🔄 Traslados")
        if not df.empty:
            orig = st.selectbox("Vieja (Activa)", df[~df['estado'].isin(['Retirada','Cancelada'])]['iccid'].tolist())
            dest = st.selectbox("Nueva (Botiquín)", df[df['estado']=='Botiquin']['iccid'].tolist())
            if st.button("Trasladar"):
                ok, m = traslado_sim(orig, dest, st.session_state.usuario)
                if ok: st.success(m); refrescar_pagina(2)
                else: st.error(m)

    elif choice == "Cancelar/Gestionar":
        st.subheader("⚠️ Baja de Servicio")
        if not df.empty:
            sel = st.selectbox("SIM:", df['iccid'].tolist())
            mt = st.text_input("Motivo")
            if st.button("Confirmar Baja"): cancelar_servicio(sel, st.session_state.usuario, mt); st.success("Ok"); refrescar_pagina(2)

    elif choice == "Auditoría":
        st.subheader("🕵️ Auditoría")
        df_h = leer_datos("historial")
        if not df_h.empty:
            df_h['fd'] = pd.to_datetime(df_h['Fecha'], errors='coerce')
            c1,c2,c3 = st.columns(3)
            u = c1.multiselect("User", sorted(df_h['Usuario'].astype(str).unique()))
            a = c2.multiselect("Action", sorted(df_h['Acción'].astype(str).unique()))
            d = c3.date_input("Fecha", [])
            if u: df_h = df_h[df_h['Usuario'].isin(u)]
            if a: df_h = df_h[df_h['Acción'].isin(a)]
            if len(d)>0: df_h = df_h[(df_h['fd'].dt.date >= d[0]) & (df_h['fd'].dt.date <= (d[1] if len(d)>1 else d[0]))]
            st.dataframe(df_h.drop(columns=['fd']), use_container_width=True)

    elif choice == "Reportes":
        st.subheader("📑 Reportes")
        if not df.empty:
            p = st.sidebar.multiselect("Filtrar reporte por:", df['pais'].unique())
            if p: df = df[df['pais'].isin(p)]
            df_exp = df.copy()
            if st.session_state.rol != 'admin': df_exp = df_exp.drop(columns=['costo_q','costo_d'], errors='ignore')
            else:
                 try:
                    df_exp['costo_q'] = df_exp['costo_q'].apply(lambda x: f"Q {float(x):,.2f}")
                    df_exp['costo_d'] = df_exp['costo_d'].apply(lambda x: f"$ {float(x):,.2f}")
                 except: pass
            b = io.BytesIO()
            with pd.ExcelWriter(b, engine='openpyxl') as w: df_exp.to_excel(w, index=False)
            st.download_button("📥 Descargar Excel", b.getvalue(), "reporte.xlsx")
            st.dataframe(df_exp)

# ==============================================================================
# 5. MAIN
# ==============================================================================
def main():
    if gestionar_reset_password(): return
    if 'usuario' not in st.session_state: 
        st.session_state.usuario = None
        st.session_state.paises_asignados = []

    if st.session_state.usuario is None:
        c1,c2,c3 = st.columns([1,2,1])
        with c2:
            st.title("☁️ Control SIM")
            t1, t2 = st.tabs(["Ingresar", "Recuperar"])
            with t1:
                u = st.text_input("Email")
                p = st.text_input("Password", type="password")
                if st.button("Entrar", type="primary"):
                    ws = conectar_google().worksheet("usuarios")
                    df = pd.DataFrame(ws.get_all_records())
                    df['email'] = df['email'].astype(str)
                    usr = df[df['email']==u]
                    if not usr.empty and check_hashes(p, str(usr.iloc[0]['password'])):
                        st.session_state.usuario = u
                        st.session_state.rol = usr.iloc[0]['rol']
                        st.session_state.nombre = usr.iloc[0]['nombre']
                        try: st.session_state.paises_asignados = [x.strip() for x in str(usr.iloc[0]['paises']).split(",") if x.strip()]
                        except: st.session_state.paises_asignados = []
                        st.rerun()
                    else: st.error("Credenciales incorrectas")
            with t2:
                re = st.text_input("Email Recuperación")
                if st.button("Enviar Link"):
                    ws = conectar_google().worksheet("usuarios")
                    c = ws.find(re)
                    if c:
                        tk = str(uuid.uuid4())
                        ws.update_cell(c.row, 5, tk)
                        enviar_link_recuperacion(re, tk, "Usuario")
                        st.success("Enviado")
        return

    st.sidebar.title(f"👤 {st.session_state.nombre}")
    st.sidebar.caption(f"{st.session_state.rol.upper()} | {', '.join(st.session_state.paises_asignados) if st.session_state.paises_asignados else 'GLOBAL'}")
    mod = st.sidebar.selectbox("MÓDULO", ["Control SIM", "Gestión Usuarios"] if st.session_state.rol == "admin" else ["Control SIM"])
    st.sidebar.markdown("---")
    
    if mod == "Control SIM": app_control_sim()
    elif mod == "Gestión Usuarios": app_gestion_usuarios()

    if st.sidebar.button("Cerrar Sesión"):
        st.session_state.usuario = None
        st.rerun()

if __name__ == "__main__":
    main()

