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
LISTA_PAISES = ["Guatemala", "El Salvador", "Honduras", "Nicaragua", "Costa Rica", "Panamá", "México", "Colombia", "Republica Dominicana"]

# ==============================================================================
# 2. FUNCIONES DE UTILIDAD (LOGIN, HASH, FECHA, GOOGLE)
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

# --- FUNCIONES DE EMAIL ---
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
# 3. LÓGICA DE NEGOCIO SIMS & CLIENTES
# ==============================================================================

@st.cache_data(ttl=10)
def leer_datos(pestaña):
    try:
        ws = conectar_google().worksheet(pestaña)
        data = ws.get_all_records()
        df = pd.DataFrame(data) if data else pd.DataFrame()
        
        # --- CORRECCIÓN DE ESPACIOS EN BLANCO ---
        # Esto limpia espacios invisibles en TODAS las columnas de texto
        if not df.empty:
            df = df.applymap(lambda x: x.strip() if isinstance(x, str) else x)
        # ----------------------------------------

        if pestaña == "sims" and df.empty:
            return pd.DataFrame(columns=['iccid', 'numero_linea', 'cliente', 'placa', 'imei', 'tipo_plan', 'pais', 'costo_q', 'costo_d', 'estado', 'fecha_registro'])
        if pestaña == "clientes" and df.empty:
            return pd.DataFrame(columns=['nombre'])
        return df
    except: return pd.DataFrame()

def limpiar_cache(): st.cache_data.clear()

def escribir_fila(pestaña, fila):
    conectar_google().worksheet(pestaña).append_row(fila)
    limpiar_cache()

def escribir_lote(pestaña, filas):
    if filas: conectar_google().worksheet(pestaña).append_rows(filas)
    limpiar_cache()

def limpiar_moneda(valor):
    """Corregido para formato latino: 50,00 -> 50.00"""
    if pd.isna(valor) or str(valor).strip() == "": return 0.0
    if isinstance(valor, (int, float)): return float(valor)
    valor = str(valor).strip().upper().replace("Q", "").replace("$", "").replace(" ", "")
    if "." in valor and "," in valor: # 1.500,00
        valor = valor.replace(".", "").replace(",", ".")
    elif "," in valor: # 50,00
        valor = valor.replace(",", ".")
    try: return float(valor)
    except: return 0.0

# --- LÓGICA DE CLIENTES ---
def obtener_lista_clientes():
    df = leer_datos("clientes")
    if not df.empty and 'nombre' in df.columns:
        lista = sorted(df['nombre'].astype(str).unique().tolist())
        return [c for c in lista if c.strip() != ""]
    return []

def crear_nuevo_cliente(nombre_cliente):
    df = leer_datos("clientes")
    if not df.empty and 'nombre' in df.columns:
        if nombre_cliente in df['nombre'].values: return False, "El cliente ya existe."
    escribir_fila("clientes", [nombre_cliente])
    return True, "Cliente creado."

# --- LÓGICA DE ACTUALIZACIÓN ---
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
    cq = limpiar_moneda(datos['costo_q'])
    cd = limpiar_moneda(datos['costo_d'])
    
    fila = [str(datos['iccid']), l, c, str(datos['placa']), str(datos['imei']),
            str(datos['tipo_plan']), str(datos['pais']), cq, cd, e, f]
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
        vq = limpiar_moneda(row['costo_q'])
        vd = limpiar_moneda(row['costo_d'])
        nuevas.append([ic, l, cli, str(row['placa']), str(row['imei']), str(row['tipo_plan']), str(row['pais']), vq, vd, est, hoy])
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

# --- LOGICA USUARIOS ---
def actualizar_usuario_batch(df_cambios):
    try:
        ws = conectar_google().worksheet("usuarios")
        emails = ws.col_values(1)
        updates = []
        for _, row in df_cambios.iterrows():
            if str(row['email']) in emails:
                rn = emails.index(str(row['email'])) + 1
                updates.append({'range': f'C{rn}', 'values': [[row['rol']]]})
                updates.append({'range': f'D{rn}', 'values': [[row['nombre']]]})
                updates.append({'range': f'F{rn}', 'values': [[row['paises']]]})
        if updates: ws.batch_update(updates); return True
    except: pass
    return False

def eliminar_usuario_db(email):
    try:
        ws = conectar_google().worksheet("usuarios")
        c = ws.find(email)
        ws.delete_rows(c.row)
        return True
    except: return False

# ==============================================================================
# 4. MÓDULOS UI (PANTALLAS)
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

# ==============================================================================
# 5. GESTIÓN USUARIOS (VERSIÓN "PANEL AMIGABLE 360")
# ==============================================================================
def app_gestion_usuarios():
    st.header("👤 Administración de Usuarios")
    
    # Estilo CSS para separar visualmente las zonas
    st.markdown("""
        <style>
        .stSelectbox {margin-bottom: 20px;}
        div[data-testid="stForm"] {border: 1px solid #e0e0e0; padding: 20px; border-radius: 10px;}
        </style>
    """, unsafe_allow_html=True)

    tab_crear, tab_gestionar = st.tabs(["➕ Crear Nuevo Usuario", "⚙️ Gestionar Existentes"])
    
    # --- TAB 1: CREAR (OPTIMIZADO) ---
    with tab_crear:
        st.markdown("#### Alta de Nuevo Colaborador")
        st.info("💡 El sistema enviará un correo automático con el enlace de activación.")
        
        with st.form("form_crear_usuario", clear_on_submit=True):
            col_a, col_b = st.columns(2)
            with col_a:
                new_email = st.text_input("📧 Correo Electrónico (Será su usuario)")
                new_nombre = st.text_input("👤 Nombre Completo")
            with col_b:
                new_rol = st.radio("Nivel de Acceso", ["general", "admin"], horizontal=True, 
                                   captions=["Ver y Reportar", "Control Total"])
                new_paises = st.multiselect("🌍 Países Permitidos", LISTA_PAISES, default=["Guatemala"])
            
            st.markdown("---")
            if st.form_submit_button("✨ Crear Usuario", type="primary"):
                ws = conectar_google().worksheet("usuarios")
                if new_email and new_nombre and new_paises:
                    if new_email in ws.col_values(1):
                        st.error("⚠️ Este correo ya está registrado.")
                    else:
                        token = str(uuid.uuid4())
                        paises_str = ",".join(new_paises)
                        # Orden: email, pass, rol, nombre, token, paises
                        ws.append_row([new_email, "PENDIENTE", new_rol, new_nombre, token, paises_str])
                        
                        with st.spinner("Enviando invitación..."):
                            if enviar_link_activacion(new_email, token, new_nombre):
                                st.balloons()
                                st.success(f"✅ Usuario {new_nombre} creado y notificado exitosamente.")
                            else:
                                st.warning("Usuario creado, pero hubo un error enviando el correo.")
                else:
                    st.error("Por favor completa todos los campos obligatorios.")

    # --- TAB 2: GESTIONAR (LA GRAN MEJORA INTERFAZ 360) ---
    with tab_gestionar:
        ws = conectar_google().worksheet("usuarios")
        data = ws.get_all_records()
        df = pd.DataFrame(data)

        if not df.empty:
            # 1. SELECTOR PRINCIPAL (Buscador)
            st.markdown("#### 🔍 Buscar Usuario a Editar")
            col_search, _ = st.columns([2, 1])
            
            # Creamos una lista amigable: "Nombre (Correo)"
            opciones_usuarios = [f"{row['nombre']} ({row['email']})" for i, row in df.iterrows()]
            seleccion = col_search.selectbox("Seleccione un usuario de la lista:", opciones_usuarios, index=None, placeholder="Escribe para buscar...")

            if seleccion:
                # Extraemos el email del string seleccionado "Nombre (email)"
                email_sel = seleccion.split("(")[-1].replace(")", "")
                user_data = df[df['email'] == email_sel].iloc[0]

                # 2. FICHA DE EDICIÓN (CONTAINER VISUAL)
                st.divider()
                st.subheader(f"✏️ Editando a: {user_data['nombre']}")
                
                with st.container(border=True):
                    # Usamos un FORMULARIO para que sea fácil guardar todo junto
                    with st.form("form_edicion"):
                        c1, c2 = st.columns(2)
                        
                        with c1:
                            st.caption("🔒 Identificación (No editable)")
                            st.text_input("Correo", value=user_data['email'], disabled=True)
                            
                            st.caption("👤 Datos Personales")
                            edit_nombre = st.text_input("Nombre Completo", value=user_data['nombre'])

                        with c2:
                            st.caption("🔑 Nivel de Permisos")
                            # Índice del rol actual
                            idx_rol = 0 if user_data['rol'] == 'general' else 1
                            edit_rol = st.radio("Rol del Sistema", ["general", "admin"], index=idx_rol, horizontal=True)
                            
                            st.caption("🌍 Acceso Geográfico")
                            # Convertimos string "Guatemala,Mexico" a lista
                            paises_actuales = [p.strip() for p in str(user_data['paises']).split(",") if p.strip() in LISTA_PAISES]
                            edit_paises = st.multiselect("Países Asignados", LISTA_PAISES, default=paises_actuales)

                        st.markdown("---")
                        col_save, col_spacer = st.columns([1, 3])
                        if col_save.form_submit_button("💾 Guardar Cambios", type="primary"):
                            # LOGICA DE GUARDADO
                            try:
                                cell = ws.find(email_sel)
                                r = cell.row
                                # Actualizamos columnas C(3)=Rol, D(4)=Nombre, F(6)=Paises
                                updates = [
                                    {'range': f'C{r}', 'values': [[edit_rol]]},
                                    {'range': f'D{r}', 'values': [[edit_nombre]]},
                                    {'range': f'F{r}', 'values': [[",".join(edit_paises)]]}
                                ]
                                ws.batch_update(updates)
                                st.success("✅ Datos actualizados correctamente.")
                                time.sleep(1.5)
                                st.rerun()
                            except Exception as e:
                                st.error(f"Error al guardar: {e}")

                # 3. ZONA DE PELIGRO (FUERA DEL FORMULARIO)
                st.markdown("### 🚫 Zona de Riesgo")
                with st.expander(f"Eliminar cuenta de {user_data['nombre']}", expanded=False):
                    st.warning("Esta acción es irreversible. El usuario perderá acceso inmediato al sistema.")
                    
                    # Verificación de seguridad para no borrarse a uno mismo
                    if email_sel == st.session_state.usuario:
                        st.error("⛔ No puedes eliminar tu propia cuenta mientras estás logueado.")
                    else:
                        col_confirm, col_btn_del = st.columns([3, 1])
                        check_del = col_confirm.checkbox("Entiendo, quiero eliminar este usuario permanentemente.")
                        
                        if col_btn_del.button("🔥 Eliminar Usuario"):
                            if check_del:
                                with st.spinner("Eliminando..."):
                                    if eliminar_usuario_db(email_sel):
                                        st.success("Usuario eliminado.")
                                        time.sleep(1.5)
                                        st.rerun()
                            else:
                                st.info("Debes marcar la casilla para confirmar.")
            
            else:
                # Mensaje cuando no hay selección
                st.info("👆 Selecciona un usuario arriba para ver su ficha de detalles.")
        else:
            st.warning("No hay usuarios registrados en la base de datos.")
def app_control_sim():
    # Setup de permisos
    paises_user = st.session_state.get('paises_asignados', [])
    if not paises_user:
        if st.session_state.rol == 'admin': paises_user = LISTA_PAISES
        else: paises_user = []

    st.sidebar.markdown("### 📱 Menú SIMs")
    st.session_state.zona_horaria = st.sidebar.selectbox("Zona Horaria:", ["America/Guatemala", "America/Bogota", "America/Mexico_City", "UTC"])
    st.sidebar.caption(f"Hora: {obtener_hora_actual()}")

    ops = ["Dashboard", "🔍 Consulta SIM", "Reportes"]
    if st.session_state.rol == "admin":
        ops = ["Dashboard", "🔍 Consulta SIM", "Registrar SIM", "Actualizar Datos", "Gestión Clientes", "Traslados", "Cancelar/Gestionar", "Auditoría", "Reportes"]
    
    choice = st.sidebar.radio("Opciones:", ops)
    if 'form_id' not in st.session_state: st.session_state.form_id = 0
    clientes_db = obtener_lista_clientes()

    # --- DASHBOARD CRM ---
    # --- DASHBOARD ---
    if choice == "Dashboard":
        st.title("📊 Tablero de Control")
        
        df_raw = leer_datos("sims")
        
        # --- MODO DIOS (SOLO ADMIN) ---
        # Permite ver todo ignorando los filtros de país
        if st.session_state.rol == 'admin':
            ver_todo = st.checkbox("👁️ Ver Inventario Global (Ignorar mis países asignados)")
            if ver_todo:
                df = df_raw # Usamos la data cruda sin filtrar
            else:
                # Usamos el filtro normal
                df = df_raw[df_raw['pais'].isin(paises_user)] if paises_user else df_raw
        else:
            # Usuarios normales siempre tienen filtro
            df = df_raw[df_raw['pais'].isin(paises_user)] if paises_user else df_raw
        
        # ... (aquí sigue el resto de tu código de tarjetas) ...
        
        if not df.empty and 'estado' in df.columns:
            
            # --- ZONA 1: TARJETAS GENERALES (SIEMPRE ARRIBA) ---
            # Esto es lo que "venía mostrando" antes
            st.markdown("### Resumen General")
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Total Inventario", len(df), border=True)
            c2.metric("Activas", len(df[df['estado']=='Activa']), border=True)
            c3.metric("Botiquín (Stock)", len(df[df['estado']=='Botiquin']), border=True)
            c4.metric("Bajas / Canceladas", len(df[df['estado']=='Cancelada']), border=True)
            
            st.markdown("---")
            
            # --- ZONA 2: DETALLES (PESTAÑAS) ---
            t1, t2 = st.tabs(["🌍 Análisis Geográfico y Financiero", "🏢 CRM por Cliente"])
            
            # Pestaña 1: Gráficos y Dinero
            with t1:
                st.subheader("Distribución por País")
                if 'pais' in df.columns:
                    cp = df['pais'].value_counts().reset_index()
                    cp.columns = ["País", "Cantidad"]
                    
                    ga, gb = st.columns([2, 1])
                    with ga: 
                        st.bar_chart(cp.set_index("País"))
                    with gb: 
                        st.dataframe(
                            cp, 
                            hide_index=True, 
                            use_container_width=True,
                            column_config={"Cantidad": st.column_config.ProgressColumn("Volumen", format="%d", min_value=0, max_value=int(cp['Cantidad'].max()))}
                        )
                
                # Sección Financiera (Solo Admin)
                if st.session_state.rol == 'admin':
                    st.divider()
                    st.subheader("💰 Facturación Estimada (Mensual)")
                    # Calculamos totales
                    df['cq'] = df['costo_q'].apply(limpiar_moneda)
                    df['cd'] = df['costo_d'].apply(limpiar_moneda)
                    act = df[df['estado']=='Activa']
                    
                    f1, f2 = st.columns(2)
                    f1.metric("Total Quetzales (Q)", f"Q {act['cq'].sum():,.2f}", delta="Mensual", border=True)
                    f2.metric("Total Dólares ($)", f"$ {act['cd'].sum():,.2f}", delta="Mensual", border=True)

            # Pestaña 2: Buscador de Clientes (CRM)
            with t2:
                st.subheader("Análisis Individual por Cliente")
                # Unimos clientes de DB con los que ya tienen SIMs
                all_cl = sorted(list(set(clientes_db + df['cliente'].unique().tolist())))
                
                c_sel = st.selectbox("Seleccione Cliente:", all_cl, index=None, placeholder="Buscar empresa...")
                
                if c_sel:
                    df_c = df[df['cliente']==c_sel]
                    if not df_c.empty:
                        # Cálculo de Traslados para este cliente
                        df_h = leer_datos("historial")
                        tras = 0
                        if not df_h.empty:
                            ics = df_c['iccid'].astype(str).tolist()
                            tras = len(df_h[(df_h['ID'].astype(str).isin(ics)) & (df_h['Acción'].str.contains("Traslado", na=False))])
                        
                        # KPIs específicos del cliente
                        k1, k2, k3, k4 = st.columns(4)
                        k1.metric("Líneas Activas", len(df_c[df_c['estado']=='Activa']))
                        k2.metric("Stock (Botiquín)", len(df_c[df_c['estado']=='Botiquin']))
                        k3.metric("Canceladas", len(df_c[df_c['estado']=='Cancelada']))
                        k4.metric("Traslados Históricos", tras, help="Veces que se han cambiado SIMs de este cliente")
                        
                        st.divider()
                        st.caption(f"Inventario detallado de {c_sel}")
                        st.dataframe(
                            df_c[['iccid','numero_linea','placa','estado','pais', 'tipo_plan']], 
                            use_container_width=True, 
                            hide_index=True
                        )
                    else: 
                        st.warning("Este cliente está registrado en la lista, pero NO tiene SIMs asignadas actualmente.")
        else:
            st.info("No hay datos para mostrar en este momento.")
    elif choice == "🔍 Consulta SIM":
        st.subheader("🔍 Buscador")
        df = leer_datos("sims")
        if paises_user: df = df[df['pais'].isin(paises_user)]
        if not df.empty:
            df['s'] = df['iccid'].astype(str) + " | " + df['cliente'].astype(str) + " (" + df['estado'] + ")"
            sel = st.selectbox("Buscar:", df['s'].tolist(), index=None, placeholder="Escribe...")
            if sel:
                ic = sel.split(" | ")[0]
                row = df[df['iccid']==ic].iloc[0]
                est = row['estado']
                c1, c2 = st.columns([1,6])
                with c1: st.header("✅" if est=="Activa" else "📦" if est=="Botiquin" else "🚫")
                with c2: st.subheader(f"{est}"); st.write(f"**Línea:** {row['numero_linea']}")
                with st.container(border=True):
                    k1,k2,k3=st.columns(3); k1.write(f"**ICCID:** {ic}"); k2.write(f"**Cliente:** {row['cliente']}"); k3.write(f"**País:** {row['pais']}")

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
            st.success("✅ Hecho"); st.write(st.session_state.res)
            if st.button("Aceptar"): st.session_state.res=None; st.session_state.ukey+=1; st.rerun()
        else:
            t1, t2 = st.tabs(["Unitario", "Masivo"])
            with t1:
                df = leer_datos("sims")
                if paises_user: df = df[df['pais'].isin(paises_user)]
                if not df.empty:
                    df['d'] = df['iccid'].astype(str) + " | " + df['cliente'].astype(str)
                    sel = st.selectbox("Buscar:", df['d'].tolist(), index=None, key=f"sb_{st.session_state.ukey}")
                    if sel:
                        ic = sel.split(" | ")[0]
                        cur = df[df['iccid']==ic].iloc[0]
                        with st.form("ed"):
                            c1,c2 = st.columns(2)
                            nl = c1.text_input("Línea", value=cur['numero_linea'])
                            # Cliente inteligente
                            idx_c = None
                            if str(cur['cliente']) in clientes_db: idx_c = clientes_db.index(str(cur['cliente']))
                            if clientes_db: nc = c2.selectbox("Cliente", clientes_db, index=idx_c)
                            else: nc = c2.text_input("Cliente", value=cur['cliente'])
                            
                            np = c1.text_input("Placa", value=cur['placa'])
                            npl = c2.text_input("Plan", value=cur['tipo_plan'])
                            
                            lpe = [p for p in LISTA_PAISES if p in paises_user] if paises_user else LISTA_PAISES
                            idx_p = lpe.index(cur['pais']) if cur['pais'] in lpe else 0
                            npa = c1.selectbox("País", lpe, index=idx_p)
                            
                            ncq = c2.number_input("Costo Q", value=limpiar_moneda(cur['costo_q']))
                            ncd = c1.number_input("Costo $", value=limpiar_moneda(cur['costo_d']))
                            
                            if st.form_submit_button("Actualizar"):
                                ch = []
                                if str(nc)!=str(cur['cliente']): ch.append(f"Cliente: {nc}")
                                d = {'numero_linea': nl, 'cliente': nc, 'placa': np, 'imei': cur['imei'], 'tipo_plan': npl, 'pais': npa, 'costo_q': ncq, 'costo_d': ncd}
                                if actualizar_datos_sim(ic, d, st.session_state.usuario):
                                    st.session_state.res = ch if ch else ["Datos Guardados"]; st.rerun()
            with t2:
                upl = st.file_uploader("Update Excel", type=["xlsx"])
                mod = st.radio("Modo", ["Rellenar", "Sobrescribir"])
                if upl and st.button("Ejecutar"):
                    cant, msg = procesar_actualizacion_masiva(pd.read_excel(upl), st.session_state.usuario, "Rellenar" in mod)
                    if cant: st.success(msg)
                    else: st.warning(msg)

    elif choice == "Gestión Clientes":
        app_gestion_clientes()

    elif choice == "Traslados":
        st.subheader("🔄 Traslados")
        df = leer_datos("sims")
        if paises_user: df = df[df['pais'].isin(paises_user)]
        if not df.empty:
            orig = st.selectbox("Vieja", df[~df['estado'].isin(['Retirada','Cancelada'])]['iccid'].tolist())
            dest = st.selectbox("Nueva", df[df['estado']=='Botiquin']['iccid'].tolist())
            if st.button("Trasladar"):
                ok, m = traslado_sim(orig, dest, st.session_state.usuario)
                if ok: st.success(m); refrescar_pagina(2)
                else: st.error(m)

    elif choice == "Cancelar/Gestionar":
        st.subheader("Baja")
        df = leer_datos("sims")
        if paises_user: df = df[df['pais'].isin(paises_user)]
        if not df.empty:
            sel = st.selectbox("SIM:", df['iccid'].tolist())
            mt = st.text_input("Motivo")
            if st.button("Baja"): cancelar_servicio(sel, st.session_state.usuario, mt); st.success("Ok"); refrescar_pagina(2)

    elif choice == "Auditoría":
        st.subheader("Auditoría")
        df = leer_datos("historial")
        if not df.empty:
            df['fd'] = pd.to_datetime(df['Fecha'], errors='coerce')
            c1,c2,c3 = st.columns(3)
            u = c1.multiselect("User", sorted(df['Usuario'].astype(str).unique()))
            a = c2.multiselect("Action", sorted(df['Acción'].astype(str).unique()))
            d = c3.date_input("Fecha", [])
            if u: df = df[df['Usuario'].isin(u)]
            if a: df = df[df['Acción'].isin(a)]
            if len(d)>0: df = df[(df['fd'].dt.date >= d[0]) & (df['fd'].dt.date <= (d[1] if len(d)>1 else d[0]))]
            st.dataframe(df.drop(columns=['fd']), use_container_width=True)

   # --- REPORTES (CON FILTRO DE SEGURIDAD GEOGRÁFICA) ---
    elif choice == "Reportes":
        st.subheader("📑 Reportes Operativos")
        
        # 1. Cargar Datos Crudos
        df_raw = leer_datos("sims")
        
        # 2. FILTRO DE SEGURIDAD (EL CANDADO) 🔒
        # Recuperamos los países que este usuario tiene permiso de ver
        permisos_paises = st.session_state.get('paises_asignados', [])
        
        # Aplicamos el filtro: Solo mostramos filas donde el país esté en su lista de permisos
        if permisos_paises:
            df = df_raw[df_raw['pais'].isin(permisos_paises)]
        else:
            # Si por error no tiene países asignados:
            if st.session_state.rol == 'admin':
                df = df_raw # El admin ve todo por defecto si no tiene restricciones
            else:
                df = pd.DataFrame(columns=df_raw.columns) # El usuario general ve vacío
        
        if not df.empty:
            # 3. Filtro Visual (Opcional para el usuario)
            # El usuario puede querer ver solo uno de sus países permitidos
            paises_disponibles = df['pais'].unique()
            
            # Si tiene más de un país asignado, le mostramos el filtro para que elija
            if len(paises_disponibles) > 1:
                filtro_pais = st.sidebar.multiselect("Filtrar reporte por:", paises_disponibles)
                if filtro_pais:
                    df = df[df['pais'].isin(filtro_pais)]
            
            # 4. Preparar Exportación (Limpiar columnas privadas)
            df_export = df.copy()
            
            if st.session_state.rol != 'admin':
                # ELIMINAR COSTOS PARA ROL GENERAL
                df_export = df_export.drop(columns=['costo_q', 'costo_d'], errors='ignore')
            else:
                # FORMATO DE MONEDA PARA ADMIN
                try:
                    df_export['costo_q'] = df_export['costo_q'].apply(lambda x: f"Q {float(limpiar_moneda(x)):,.2f}")
                    df_export['costo_d'] = df_export['costo_d'].apply(lambda x: f"$ {float(limpiar_moneda(x)):,.2f}")
                except: pass

            # 5. Botón de Descarga
            st.caption(f"Mostrando {len(df_export)} registros autorizados.")
            
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='openpyxl') as writer: 
                df_export.to_excel(writer, index=False)
            
            st.download_button(
                label="📥 Descargar Excel", 
                data=buffer.getvalue(), 
                file_name=f"reporte_sims_{date.today()}.xlsx", 
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
            
            # 6. Mostrar Tabla
            st.dataframe(df_export, use_container_width=True)
            
        else:
            st.warning("No se encontraron registros para los países que tienes asignados.")

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
            st.title("SIM Cloud")
            t1, t2 = st.tabs(["Ingresar", "Recuperar"])
            with t1:
                u = st.text_input("Email")
                p = st.text_input("Pass", type="password")
                if st.button("Login", type="primary"):
                    ws = conectar_google().worksheet("usuarios")
                    df = pd.DataFrame(ws.get_all_records())
                    df['email'] = df['email'].astype(str)
                    usr = df[df['email']==u]
                    if not usr.empty and check_hashes(p, str(usr.iloc[0]['password'])):
                        st.session_state.usuario = u
                        st.session_state.rol = usr.iloc[0]['rol']
                        st.session_state.nombre = usr.iloc[0]['nombre']
                        try: st.session_state.paises_asignados = [x.strip() for x in str(usr.iloc[0]['paises']).split(",") if x.strip()]
                        except: st.session_state.paises_asignados = LISTA_PAISES
                        st.rerun()
                    else: st.error("Datos incorrectos")
            with t2:
                re = st.text_input("Email Recu.")
                if st.button("Enviar"):
                    ws = conectar_google().worksheet("usuarios")
                    c = ws.find(re)
                    if c:
                        tk = str(uuid.uuid4())
                        ws.update_cell(c.row, 5, tk)
                        enviar_link_recuperacion(re, tk, "Usuario")
                        st.success("Enviado")
        return

    st.sidebar.title(f"👤 {st.session_state.nombre}")
    mod = st.sidebar.selectbox("MÓDULO", ["Control SIM", "Gestión Usuarios"] if st.session_state.rol == "admin" else ["Control SIM"])
    st.sidebar.markdown("---")
    
    if mod == "Control SIM": app_control_sim()
    elif mod == "Gestión Usuarios": app_gestion_usuarios()

    if st.sidebar.button("Salir"):
        st.session_state.usuario = None
        st.rerun()

if __name__ == "__main__":
    main()





