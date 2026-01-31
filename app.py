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

# Constantes
NOMBRE_HOJA = "Base de Datos SIMs"
SCOPE = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
KEY_FILE = 'credenciales.json'

# Lista maestra de países soportados
LISTA_PAISES = ["Guatemala", "El Salvador", "Honduras", "Nicaragua", "Costa Rica", "Panamá", "México", "Colombia"]

# ==============================================================================
# 2. FUNCIONES DE UTILIDAD
# ==============================================================================

def make_hashes(password):
    return hashlib.sha256(str.encode(password)).hexdigest()

def check_hashes(password, hashed_text):
    if make_hashes(password) == hashed_text:
        return True
    return False

def obtener_hora_actual():
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
    cuerpo = f"""<h3>Bienvenido {nombre}</h3><p>Configura tu clave aquí: <a href="{link}">{link}</a></p>"""
    return enviar_correo_sistema(email_destino, "🔐 Activa tu cuenta", cuerpo)

def enviar_link_recuperacion(email_destino, token, nombre):
    BASE_URL = st.secrets["email"].get("base_url", "http://localhost:8501")
    link = f"{BASE_URL}/?token_reset={token}"
    cuerpo = f"""<h3>Hola {nombre}</h3><p>Recupera tu clave aquí: <a href="{link}">{link}</a></p>"""
    return enviar_correo_sistema(email_destino, "🔄 Recuperación Clave", cuerpo)

def gestionar_reset_password():
    token_url = st.query_params.get("token_reset", None)
    if token_url:
        st.info("🔄 Gestión de Credenciales")
        sheet = conectar_google()
        ws = sheet.worksheet("usuarios")
        data = ws.get_all_records()
        df = pd.DataFrame(data)
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
    # --- NUEVAS FUNCIONES PARA GESTIÓN DE USUARIOS ---

def actualizar_usuario_batch(df_cambios):
    """
    Recibe un DataFrame con los cambios y actualiza Google Sheets.
    """
    try:
        sheet = conectar_google()
        ws = sheet.worksheet("usuarios")
        
        # Obtenemos todos los datos actuales para mapear filas
        # Asumimos que el email está en la columna 1 (A)
        lista_emails = ws.col_values(1)
        
        updates = []
        
        for index, row in df_cambios.iterrows():
            email_target = str(row['email'])
            
            if email_target in lista_emails:
                # Google Sheets es base 1, Python base 0. El header es fila 1.
                # index+1 es la posición en la lista, pero la lista incluye el header?
                # Lo más seguro es usar .find() o calcular índice si el orden no cambió.
                try:
                    row_num = lista_emails.index(email_target) + 1
                    
                    # Mapeo de columnas: Rol(3), Nombre(4), Paises(6)
                    # Basado en estructura: | email | pass | rol | nombre | token | paises |
                    
                    updates.append({'range': f'C{row_num}', 'values': [[row['rol']]]})
                    updates.append({'range': f'D{row_num}', 'values': [[row['nombre']]]})
                    updates.append({'range': f'F{row_num}', 'values': [[row['paises']]]})
                except:
                    pass

        if updates:
            ws.batch_update(updates)
            return True
        return False
    except Exception as e:
        st.error(f"Error al actualizar: {e}")
        return False

def eliminar_usuario_db(email_a_borrar):
    try:
        sheet = conectar_google()
        ws = sheet.worksheet("usuarios")
        cell = ws.find(email_a_borrar)
        ws.delete_rows(cell.row)
        return True
    except Exception as e:
        st.error(f"Error al eliminar: {e}")
        return False

# ==============================================================================
# 3. LÓGICA DE NEGOCIO SIMS
# ==============================================================================

@st.cache_data(ttl=10)
def leer_datos(pestaña):
    sheet = conectar_google()
    worksheet = sheet.worksheet(pestaña)
    data = worksheet.get_all_records()
    df = pd.DataFrame(data) if data else pd.DataFrame()
    if pestaña == "sims" and df.empty:
        return pd.DataFrame(columns=['iccid', 'numero_linea', 'cliente', 'placa', 'imei', 'tipo_plan', 'pais', 'costo_q', 'costo_d', 'estado', 'fecha_registro'])
    return df

def limpiar_cache():
    st.cache_data.clear()

def escribir_fila(pestaña, fila):
    conectar_google().worksheet(pestaña).append_row(fila)
    limpiar_cache()

def escribir_lote(pestaña, filas):
    if filas: conectar_google().worksheet(pestaña).append_rows(filas)
    limpiar_cache()

def actualizar_sim_completa(iccid, datos_dict):
    sheet = conectar_google()
    worksheet = sheet.worksheet("sims")
    try:
        cell = worksheet.find(str(iccid))
        r = cell.row
        # Mapeo de columnas (B=2, C=3...)
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
        worksheet.batch_update(vals)
        limpiar_cache()
        return True
    except: return False

def actualizar_celda_sim(iccid, col, val):
    try:
        sheet = conectar_google()
        ws = sheet.worksheet("sims")
        cell = ws.find(str(iccid))
        h = ws.find(col)
        ws.update_cell(cell.row, h.col, val)
        limpiar_cache()
        return True
    except: return False

def limpiar_moneda(v):
    if isinstance(v, (int, float)): return float(v)
    v = str(v).strip().replace("Q","").replace("$","").replace(",","")
    try: return float(v)
    except: return 0.0

def registrar_sim(datos, usuario):
    df = leer_datos("sims")
    if 'iccid' in df.columns and str(datos['iccid']) in df['iccid'].astype(str).values: return False
    l = str(datos['numero_linea']) if datos['numero_linea'] else ""
    c = str(datos['cliente']) if datos['cliente'] else ""
    e = "Activa" if l and c else "Botiquin"
    f = obtener_hora_actual()
    fila = [str(datos['iccid']), l, c, str(datos['placa']), str(datos['imei']),
            str(datos['tipo_plan']), str(datos['pais']), datos['costo_q'], datos['costo_d'], e, f]
    escribir_fila("sims", fila)
    escribir_fila("historial", [str(datos['iccid']), "Creacion", f"Estado: {e}", usuario, f])
    return True

def procesar_carga_masiva_turbo(df_limpio, usuario):
    # (Misma lógica anterior para creación)
    df_limpio = df_limpio.fillna("")
    df_limpio['iccid'] = df_limpio['iccid'].astype(str).str.replace(".0", "", regex=False)
    df_db = leer_datos("sims")
    existentes = set(df_db['iccid'].astype(str).tolist()) if not df_db.empty else set()
    
    nuevas, hist = [], []
    hoy = obtener_hora_actual()
    c, d = 0, 0
    
    for _, row in df_limpio.iterrows():
        ic = str(row['iccid']).strip()
        if not ic or ic in existentes: 
            d += 1; continue
        
        l, cli = str(row['numero_linea']).replace(".0",""), str(row['cliente'])
        est = "Activa" if l and cli else "Botiquin"
        fila = [ic, l, cli, str(row['placa']), str(row['imei']), str(row['tipo_plan']), 
                str(row['pais']), limpiar_moneda(row['costo_q']), limpiar_moneda(row['costo_d']), est, hoy]
        nuevas.append(fila)
        hist.append([ic, "Creacion Masiva", f"Estado: {est}", usuario, hoy])
        existentes.add(ic)
        c += 1
        
    if nuevas:
        escribir_lote("sims", nuevas)
        escribir_lote("historial", hist)
    return c, d

# --- NUEVA FUNCIÓN: ACTUALIZACIÓN MASIVA (Edición) ---
def procesar_actualizacion_masiva(df_updates, usuario, solo_vacios=True):
    """
    Actualiza datos existentes. 
    solo_vacios=True -> Solo llena si la celda en DB está vacía.
    solo_vacios=False -> Sobrescribe con lo que venga en Excel.
    """
    df_db = leer_datos("sims")
    if df_db.empty: return 0, "Base de datos vacía"
    
    # Asegurar tipos string para cruce
    df_db['iccid'] = df_db['iccid'].astype(str)
    df_updates['iccid'] = df_updates['iccid'].astype(str).str.replace(".0", "", regex=False)
    
    sheet = conectar_google()
    ws = sheet.worksheet("sims")
    
    # Mapeo de columnas Excel -> Columnas Sheet (A=1, B=2...)
    # Asumimos que el usuario sube un excel con cabeceras iguales a la DB
    mapa_cols = {
        'numero_linea': 2, 'cliente': 3, 'placa': 4, 'imei': 5, 
        'tipo_plan': 6, 'pais': 7, 'costo_q': 8, 'costo_d': 9
    }
    
    cambios_batch = []
    log_historial = []
    hoy = obtener_hora_actual()
    contador_cambios = 0
    
    # Optimizacion: Crear diccionario de ICCID -> Fila en Sheet
    # (Asumimos que fila 1 es header, datos empiezan en fila 2)
    # gspread row index empieza en 1.
    iccid_a_fila = {str(ic): i+2 for i, ic in enumerate(df_db['iccid'])}
    
    for _, row in df_updates.iterrows():
        ic = str(row['iccid']).strip()
        
        if ic in iccid_a_fila:
            fila_idx = iccid_a_fila[ic]
            
            # Buscamos la fila original en el DF para comparar
            fila_orig = df_db[df_db['iccid'] == ic].iloc[0]
            
            detalles_cambio = []
            
            for col_nombre, col_idx in mapa_cols.items():
                if col_nombre in row:
                    nuevo_valor = str(row[col_nombre]).strip()
                    valor_orig = str(fila_orig[col_nombre]).strip()
                    
                    # Si el Excel trae algo (no es vacío ni nan)
                    if nuevo_valor and nuevo_valor.lower() != 'nan':
                        
                        aplicar = False
                        if solo_vacios:
                            # Solo si DB está vacía
                            if not valor_orig: aplicar = True
                        else:
                            # Sobrescribir si es diferente
                            if nuevo_valor != valor_orig: aplicar = True
                            
                        if aplicar:
                            # Añadir a batch
                            # gspread update cell
                            cambios_batch.append({
                                'range': gspread.utils.rowcol_to_a1(fila_idx, col_idx),
                                'values': [[nuevo_valor]]
                            })
                            detalles_cambio.append(f"{col_nombre}")
            
            if detalles_cambio:
                contador_cambios += 1
                detalles_str = ", ".join(detalles_cambio)
                log_historial.append([ic, "Edición Masiva", f"Campos: {detalles_str}", usuario, hoy])

    # Ejecutar Batch Update
    if cambios_batch:
        try:
            ws.batch_update(cambios_batch)
            escribir_lote("historial", log_historial)
            return contador_cambios, f"Se actualizaron {contador_cambios} SIMs exitosamente."
        except Exception as e:
            return 0, f"Error en batch update: {e}"
    else:
        return 0, "No se encontraron datos nuevos para actualizar."

def actualizar_datos_sim(iccid, datos, usuario):
    # (Lógica original de actualización unitaria)
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
    return True, "Traslado Exitoso"

def cancelar_servicio(iccid, usuario, motivo):
    if actualizar_celda_sim(iccid, "estado", "Cancelada"):
        f = obtener_hora_actual()
        escribir_fila("historial", [iccid, "Cancelacion", f"Motivo: {motivo}", usuario, f])
        return True
    return False

# ==============================================================================
# 4. APLICACIÓN PRINCIPAL (UI)
# ==============================================================================

def app_control_sim():
    # --- FILTRADO DE SEGURIDAD POR PAÍS ---
    # Obtenemos los países permitidos del usuario logueado
    paises_usuario = st.session_state.get('paises_asignados', [])
    
    # Si la lista está vacía o es None, asumimos que no tiene permisos (o es un error)
    # A menos que sea admin global (pero el prompt pide config de países).
    # Para evitar bloqueos, si está vacío mostramos todo SOLO si es admin, si es general mostramos nada.
    if not paises_usuario:
        if st.session_state.rol == 'admin':
            paises_usuario = LISTA_PAISES # Admin ve todo si no se le asignó nada
        else:
            paises_usuario = [] # General ve nada

    # Función helper para filtrar dataframes
    def filtrar_por_pais(df_in):
        if df_in.empty or 'pais' not in df_in.columns: return df_in
        # Filtramos solo lo que esté en la lista permitida
        return df_in[df_in['pais'].isin(paises_usuario)]

    # -------------------------------------
    
    st.sidebar.markdown("### 📱 Menú SIMs")
    
    zonas = ["America/Guatemala", "America/Bogota", "America/Mexico_City", "UTC"]
    st.session_state.zona_horaria = st.sidebar.selectbox("Zona Horaria:", zonas)
    st.sidebar.caption(f"Hora: {obtener_hora_actual()}")
    st.sidebar.info(f"🌍 Región: {', '.join(paises_usuario) if paises_usuario else 'Sin Acceso'}")

    menu_ops = ["Dashboard", "🔍 Consulta SIM", "Reportes"]
    if st.session_state.rol == "admin":
        menu_ops = ["Dashboard", "🔍 Consulta SIM", "Registrar SIM", "Actualizar Datos", "Traslados", "Cancelar/Gestionar", "Auditoría", "Reportes"]
    
    choice = st.sidebar.radio("Opciones:", menu_ops)
    if 'form_id' not in st.session_state: st.session_state.form_id = 0

    # --- DASHBOARD ---
    if choice == "Dashboard":
        st.title("📊 Tablero de Control")
        # 1. Leemos todo
        df_raw = leer_datos("sims")
        # 2. Filtramos por país permitido
        df = filtrar_por_pais(df_raw)
        
        if not df.empty and 'estado' in df.columns:
            c1,c2,c3,c4 = st.columns(4)
            c1.metric("Total Inventario", len(df))
            c2.metric("Activas", len(df[df['estado']=='Activa']))
            c3.metric("Botiquín", len(df[df['estado']=='Botiquin']))
            c4.metric("Canceladas", len(df[df['estado']=='Cancelada']))

            if st.session_state.rol == 'admin':
                st.markdown("---")
                st.subheader("💰 Facturación Mensual Estimada")
                df['costo_q_calc'] = df['costo_q'].apply(limpiar_moneda)
                df['costo_d_calc'] = df['costo_d'].apply(limpiar_moneda)
                act = df[df['estado'] == 'Activa']
                k1, k2 = st.columns(2)
                k1.metric("Total Q", f"Q {act['costo_q_calc'].sum():,.2f}")
                k2.metric("Total $", f"$ {act['costo_d_calc'].sum():,.2f}")
        else: st.info("Sin datos para tu región.")

    # --- CONSULTA SIM ---
    elif choice == "🔍 Consulta SIM":
        st.subheader("🔍 Buscador Inteligente")
        df_raw = leer_datos("sims")
        df = filtrar_por_pais(df_raw) # Filtro de seguridad
        
        if not df.empty:
            df['iccid'] = df['iccid'].astype(str)
            df['busqueda'] = df['iccid'] + " | " + df['cliente'].astype(str) + " (" + df['estado'] + ")"
            sel = st.selectbox("Buscar SIM:", df['busqueda'].tolist(), index=None, placeholder="Escribe...")
            
            if sel:
                ic = sel.split(" | ")[0]
                fila = df[df['iccid'] == ic].iloc[0]
                est = fila['estado']
                
                c_icon, c_info = st.columns([1,6])
                with c_icon:
                    if est=="Activa": st.header("✅")
                    elif est=="Botiquin": st.header("📦")
                    elif est=="Retirada": st.header("🚫")
                    else: st.header("❌")
                with c_info:
                    st.subheader(f"Estado: {est}")
                    if est=="Retirada": st.error("SIM INUTILIZABLE")
                
                st.markdown("### 📋 Ficha Técnica")
                with st.container(border=True):
                    k1, k2, k3 = st.columns(3)
                    k1.markdown(f"**ICCID:** {fila['iccid']}")
                    k2.markdown(f"**Línea:** {fila['numero_linea']}")
                    k3.markdown(f"**País:** {fila['pais']}")
                    st.divider()
                    k4, k5, k6 = st.columns(3)
                    k4.markdown(f"**Cliente:** {fila['cliente']}")
                    k5.markdown(f"**Placa:** {fila['placa']}")
                    k6.markdown(f"**Plan:** {fila['tipo_plan']}")

    # --- REGISTRAR ---
    elif choice == "Registrar SIM":
        st.subheader("➕ Registrar")
        t1, t2 = st.tabs(["Manual", "Carga Masiva"])
        with t1:
            kf = str(st.session_state.form_id)
            with st.form("new"):
                c1,c2 = st.columns(2)
                ic = c1.text_input("ICCID*", key=f"i{kf}")
                ln = c2.text_input("Línea", key=f"l{kf}")
                cl = c1.text_input("Cliente", key=f"c{kf}")
                pl = c2.text_input("Placa", key=f"p{kf}")
                im = c1.text_input("IMEI", key=f"im{kf}")
                pn = c2.text_input("Plan", key=f"pl{kf}")
                # Solo permite registrar países permitidos
                pa = c1.selectbox("País", paises_usuario, key=f"pa{kf}")
                cq = c2.number_input("Costo Q", key=f"cq{kf}")
                cd = c1.number_input("Costo $", key=f"cd{kf}")
                if st.form_submit_button("Guardar"):
                    if ic:
                        d = {'iccid': ic, 'numero_linea': ln, 'cliente': cl, 'placa': pl, 'imei': im, 'tipo_plan': pn, 'pais': pa, 'costo_q': cq, 'costo_d': cd}
                        if registrar_sim(d, st.session_state.usuario):
                            st.success("Guardado"); st.session_state.form_id+=1; refrescar_pagina(2)
                        else: st.error("Error/Duplicado")
        with t2:
            st.info("Carga de SIMs Nuevas")
            upl = st.file_uploader("Excel Nuevas", type=["xlsx"])
            if upl:
                df_up = pd.read_excel(upl)
                st.write(f"Filas: {len(df_up)}")
                if st.button("Procesar Nuevas"):
                    with st.spinner("Cargando..."):
                        c, d = procesar_carga_masiva_turbo(df_up, st.session_state.usuario)
                        st.success(f"Nuevas: {c} | Duplicadas: {d}")

    # --- ACTUALIZAR (CORREGIDO: Inicio Vacío + Resumen Cambios) ---
    elif choice == "Actualizar Datos":
        st.subheader("✏️ Edición de Inventario")
        
        # Inicializamos variables de estado para el flujo de limpieza
        if 'update_key' not in st.session_state: st.session_state.update_key = 0
        if 'resumen_cambios' not in st.session_state: st.session_state.resumen_cambios = None
        
        # Si acabamos de actualizar exitosamente, mostramos el resumen y el botón de aceptar
        if st.session_state.resumen_cambios:
            with st.container(border=True):
                st.success("✅ ¡Actualización Exitosa!")
                st.markdown("### Resumen de cambios realizados:")
                
                # Mostramos la lista de cambios
                if len(st.session_state.resumen_cambios) > 0:
                    for cambio in st.session_state.resumen_cambios:
                        st.markdown(f"- {cambio}")
                else:
                    st.info("Se guardó el registro, pero no se detectaron cambios en los datos.")
                
                st.markdown("---")
                
                # Botón para limpiar y volver a empezar
                if st.button("Aceptar y Realizar otra búsqueda", type="primary"):
                    st.session_state.resumen_cambios = None # Limpiamos el resumen
                    st.session_state.update_key += 1 # Esto fuerza al buscador a reiniciarse
                    st.rerun()
        
        else:
            # FLUJO NORMAL DE BÚSQUEDA Y EDICIÓN
            tab_unit, tab_masiva = st.tabs(["Edición Unitaria", "Edición Masiva (Excel)"])
            
            # 1. EDICIÓN UNITARIA
            with tab_unit:
                df_raw = leer_datos("sims")
                df = filtrar_por_pais(df_raw)
                
                if not df.empty:
                    df['disp'] = df['iccid'].astype(str) + " | " + df['cliente'].astype(str)
                    
                    # BÚSQUEDA: Usamos una key dinámica para poder resetearlo desde el código
                    sel = st.selectbox(
                        "Buscar SIM:", 
                        df['disp'].tolist(), 
                        index=None, 
                        placeholder="Escribe para buscar...",
                        key=f"search_box_{st.session_state.update_key}"
                    )
                    
                    if sel:
                        ic = sel.split(" | ")[0]
                        cur = df[df['iccid']==ic].iloc[0]
                        
                        st.info(f"Editando SIM: **{ic}**")
                        
                        with st.form("edit"):
                            c1, c2 = st.columns(2)
                            nl = c1.text_input("Línea", value=cur['numero_linea'])
                            nc = c2.text_input("Cliente", value=cur['cliente'])
                            np = c1.text_input("Placa", value=cur['placa'])
                            npl = c2.text_input("Plan", value=cur['tipo_plan'])
                            
                            idx_p = 0
                            if cur['pais'] in paises_usuario: idx_p = paises_usuario.index(cur['pais'])
                            npa = c1.selectbox("País", paises_usuario, index=idx_p)
                            
                            ncq = c2.number_input("Costo Q", value=limpiar_moneda(cur['costo_q']))
                            ncd = c1.number_input("Costo $", value=limpiar_moneda(cur['costo_d']))
                            
                            if st.form_submit_button("Actualizar Datos"):
                                # 1. Detectar Cambios para el resumen
                                cambios_detectados = []
                                if str(nl) != str(cur['numero_linea']): cambios_detectados.append(f"Línea: {cur['numero_linea']} ➝ **{nl}**")
                                if str(nc) != str(cur['cliente']): cambios_detectados.append(f"Cliente: {cur['cliente']} ➝ **{nc}**")
                                if str(np) != str(cur['placa']): cambios_detectados.append(f"Placa: {cur['placa']} ➝ **{np}**")
                                if str(npl) != str(cur['tipo_plan']): cambios_detectados.append(f"Plan: {cur['tipo_plan']} ➝ **{npl}**")
                                if str(npa) != str(cur['pais']): cambios_detectados.append(f"País: {cur['pais']} ➝ **{npa}**")
                                if float(ncq) != float(limpiar_moneda(cur['costo_q'])): cambios_detectados.append(f"Costo Q: {cur['costo_q']} ➝ **{ncq}**")
                                if float(ncd) != float(limpiar_moneda(cur['costo_d'])): cambios_detectados.append(f"Costo $: {cur['costo_d']} ➝ **{ncd}**")

                                # 2. Guardar en BD
                                d = {'numero_linea': nl, 'cliente': nc, 'placa': np, 'imei': cur['imei'], 'tipo_plan': npl, 'pais': npa, 'costo_q': ncq, 'costo_d': ncd}
                                
                                if actualizar_datos_sim(ic, d, st.session_state.usuario):
                                    # 3. Guardar cambios en sesión y recargar para mostrar resumen
                                    st.session_state.resumen_cambios = cambios_detectados
                                    st.rerun()
            
            # 2. EDICIÓN MASIVA (Se mantiene igual)
            with tab_masiva:
                st.info("Sube un Excel con la columna 'iccid' y las columnas que quieras actualizar.")
                modo = st.radio("Modo:", ["Rellenar vacíos", "Sobrescribir todo"])
                archivo_update = st.file_uploader("Subir Excel", type=["xlsx"])
                if archivo_update:
                    df_up = pd.read_excel(archivo_update)
                    if st.button("Ejecutar Masiva"):
                        with st.spinner("Procesando..."):
                            sv = True if "Rellenar" in modo else False
                            cant, msg = procesar_actualizacion_masiva(df_up, st.session_state.usuario, sv)
                            if cant > 0:
                                st.balloons(); st.success(f"✅ {msg}")
                            else: st.warning(msg)
    
    # --- TRASLADOS ---
    elif choice == "Traslados":
        st.subheader("🔄 Traslados")
        df_raw = leer_datos("sims")
        df = filtrar_por_pais(df_raw)
        
        if not df.empty:
            dfo = df[~df['estado'].isin(['Retirada','Cancelada'])]
            dfd = df[df['estado']=='Botiquin']
            dfo['disp'] = dfo['iccid'].astype(str) + " (" + dfo['numero_linea'].astype(str) + ")"
            
            c1, c2 = st.columns(2)
            orig = c1.selectbox("Vieja", dfo['disp'].tolist())
            dest = c2.selectbox("Nueva", dfd['iccid'].tolist())
            if st.button("Trasladar"):
                ok, m = traslado_sim(orig.split(" (")[0], dest, st.session_state.usuario)
                if ok: st.success(m); refrescar_pagina(2)
                else: st.error(m)

    # --- CANCELAR ---
    elif choice == "Cancelar/Gestionar":
        st.subheader("⚠️ Cancelar")
        df_raw = leer_datos("sims")
        df = filtrar_por_pais(df_raw)
        if not df.empty:
            sel = st.selectbox("Buscar:", df['iccid'].tolist())
            mot = st.text_input("Motivo")
            if st.button("Confirmar Baja"):
                if cancelar_servicio(sel, st.session_state.usuario, mot):
                    st.success("Listo"); refrescar_pagina(2)

    # --- AUDITORÍA ---
    elif choice == "Auditoría":
        st.subheader("🕵️ Auditoría")
        df_h = leer_datos("historial")
        if not df_h.empty:
            # Filtros
            u = st.multiselect("Usuario", sorted(df_h['Usuario'].astype(str).unique()))
            a = st.multiselect("Acción", sorted(df_h['Acción'].astype(str).unique()))
            df_show = df_h.copy()
            if u: df_show = df_show[df_show['Usuario'].isin(u)]
            if a: df_show = df_show[df_show['Acción'].isin(a)]
            st.dataframe(df_show, use_container_width=True)

    # --- REPORTES ---
    elif choice == "Reportes":
        st.subheader("📑 Reportes")
        df_raw = leer_datos("sims")
        df = filtrar_por_pais(df_raw)
        
        if not df.empty:
            p = st.sidebar.multiselect("Filtrar País", df['pais'].unique())
            if p: df = df[df['pais'].isin(p)]
            
            # Exportar
            df_exp = df.copy()
            if st.session_state.rol != 'admin':
                df_exp = df_exp.drop(columns=['costo_q','costo_d'], errors='ignore')
            
            b = io.BytesIO()
            with pd.ExcelWriter(b, engine='openpyxl') as w: df_exp.to_excel(w, index=False)
            st.download_button("📥 Descargar Excel", b.getvalue(), "reporte.xlsx")
            st.dataframe(df_exp)

# ==============================================================================
# 5. GESTIÓN USUARIOS (CON EDICIÓN Y ELIMINACIÓN)
# ==============================================================================
def app_gestion_usuarios():
    st.markdown("## 👤 Usuarios & Permisos")
    
    tab1, tab2 = st.tabs(["➕ Crear Usuario", "✏️ Editar / ❌ Eliminar"])
    
    # --- TAB 1: CREAR (Se mantiene igual, solo compactado visualmente) ---
    with tab1:
        st.info("El usuario recibirá un correo para activar su cuenta.")
        with st.form("crear"):
            c1, c2 = st.columns(2)
            mail = c1.text_input("Correo (Usuario)")
            nom = c2.text_input("Nombre Completo")
            rol = c1.selectbox("Rol", ["admin", "general"])
            paises_asig = c2.multiselect("Países Permitidos", LISTA_PAISES, default=["Guatemala"])
            
            if st.form_submit_button("Crear Usuario"):
                if mail and nom and paises_asig:
                    ws = conectar_google().worksheet("usuarios")
                    if mail in ws.col_values(1): 
                        st.error("El usuario ya existe.")
                    else:
                        tok = str(uuid.uuid4())
                        str_paises = ",".join(paises_asig)
                        ws.append_row([mail, "PENDIENTE", rol, nom, tok, str_paises])
                        if enviar_link_activacion(mail, tok, nom):
                            st.success("Creado y correo enviado.")
                        else:
                            st.warning("Creado en DB, pero falló el envío de correo.")
                else: 
                    st.warning("Complete todos los campos.")

    # --- TAB 2: VER, EDITAR Y ELIMINAR ---
    with tab2:
        st.subheader("📋 Directorio de Usuarios")
        
        # 1. Cargar Datos
        ws = conectar_google().worksheet("usuarios")
        data = ws.get_all_records()
        df = pd.DataFrame(data)

        if not df.empty:
            # Aseguramos que existan las columnas clave
            required_cols = ['email', 'nombre', 'rol', 'paises']
            df_view = df[required_cols].copy()
            
            # --- ZONA DE EDICIÓN ---
            st.caption("💡 Puedes editar 'Nombre', 'Rol' y 'Países' directamente en la tabla. El correo no se puede cambiar.")
            
            edited_df = st.data_editor(
                df_view,
                column_config={
                    "email": st.column_config.TextColumn(
                        "Correo Electrónico",
                        help="Identificador único (No editable)",
                        disabled=True, # Bloqueamos edición de email
                    ),
                    "rol": st.column_config.SelectboxColumn(
                        "Rol",
                        options=["admin", "general"],
                        required=True,
                        width="small"
                    ),
                    "nombre": st.column_config.TextColumn("Nombre Completo"),
                    "paises": st.column_config.TextColumn(
                        "Países (Separados por coma)",
                        help="Ej: Guatemala,El Salvador"
                    ),
                },
                hide_index=True,
                use_container_width=True,
                num_rows="fixed", # No permitir agregar filas aquí (usar Tab 1)
                key="editor_usuarios"
            )

            # Botón para guardar cambios
            col_save, _ = st.columns([1, 4])
            if col_save.button("💾 Guardar Cambios"):
                # Comparamos si hubo cambios reales
                if not edited_df.equals(df_view):
                    with st.spinner("Actualizando permisos..."):
                        if actualizar_usuario_batch(edited_df):
                            st.success("Datos actualizados correctamente.")
                            time.sleep(1)
                            st.rerun()
                else:
                    st.info("No hay cambios pendientes por guardar.")

            st.divider()

            # --- ZONA DE ELIMINACIÓN ---
            st.subheader("🗑️ Eliminar Usuario")
            with st.container(border=True):
                col_del_1, col_del_2 = st.columns([3, 1])
                
                # Selector para elegir a quién borrar (evita errores de dedo)
                # Excluimos al usuario actual para que no se borre a sí mismo
                opciones_borrar = df[df['email'] != st.session_state.usuario]['email'].tolist()
                
                user_to_delete = col_del_1.selectbox(
                    "Seleccione el usuario a eliminar:", 
                    opciones_borrar,
                    index=None,
                    placeholder="Seleccionar correo..."
                )
                
                # Botón con confirmación
                if col_del_2.button("Eliminar Definitivamente", type="primary", disabled=(not user_to_delete)):
                    if user_to_delete:
                        with st.spinner(f"Eliminando a {user_to_delete}..."):
                            if eliminar_usuario_db(user_to_delete):
                                st.success(f"Usuario {user_to_delete} eliminado.")
                                time.sleep(1.5)
                                st.rerun()
                            else:
                                st.error("No se pudo eliminar.")
        else:
            st.warning("No se encontraron usuarios en la base de datos.")

# ==============================================================================
# 6. MAIN
# ==============================================================================
def main():
    if gestionar_reset_password(): return

    if 'usuario' not in st.session_state: 
        st.session_state.usuario = None
        st.session_state.paises_asignados = [] # Inicializamos lista vacía

    if st.session_state.usuario is None:
        c1,c2,c3 = st.columns([1,2,1])
        with c2:
            st.title("🔐 Acceso SIM Cloud")
            tab_l, tab_r = st.tabs(["Login", "Recuperar"])
            with tab_l:
                u = st.text_input("Correo")
                p = st.text_input("Pass", type="password")
                if st.button("Entrar", type="primary"):
                    ws = conectar_google().worksheet("usuarios")
                    df = pd.DataFrame(ws.get_all_records())
                    df['email'] = df['email'].astype(str)
                    usr = df[df['email']==u]
                    if not usr.empty and check_hashes(p, str(usr.iloc[0]['password'])):
                        st.session_state.usuario = u
                        st.session_state.rol = usr.iloc[0]['rol']
                        st.session_state.nombre = usr.iloc[0]['nombre']
                        
                        # LEER PAÍSES ASIGNADOS
                        # Asumimos columna 'paises' existe. Si no, damos acceso a todo por seguridad temporal.
                        try:
                            str_p = str(usr.iloc[0]['paises'])
                            st.session_state.paises_asignados = [x.strip() for x in str_p.split(",") if x.strip()]
                        except:
                            st.session_state.paises_asignados = LISTA_PAISES # Fallback
                            
                        st.rerun()
                    else: st.error("Error credenciales")
            with tab_r:
                rec = st.text_input("Correo Recup.")
                if st.button("Enviar"):
                    ws = conectar_google().worksheet("usuarios")
                    cell = ws.find(rec)
                    if cell:
                        tk = str(uuid.uuid4())
                        # Columna 5 es Token (E)
                        ws.update_cell(cell.row, 5, tk)
                        enviar_link_recuperacion(rec, tk, "Usuario")
                        st.success("Enviado")
                    else: st.error("No existe")
        return

    # LOGGED IN
    st.sidebar.title(f"👤 {st.session_state.nombre}")
    
    app_mode = "Control SIM"
    ops = ["Control SIM"]
    if st.session_state.rol == "admin": ops.append("Gestión Usuarios")
    
    app_mode = st.sidebar.selectbox("SISTEMA", ops)
    st.sidebar.markdown("---")
    
    if app_mode == "Control SIM": app_control_sim()
    elif app_mode == "Gestión Usuarios": app_gestion_usuarios()

    if st.sidebar.button("Salir"):
        st.session_state.usuario = None
        st.rerun()

if __name__ == "__main__":
    main()



