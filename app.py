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
# 4. APLICACIÓN PRINCIPAL (UI) - CON BUSCADOR PREDICTIVO ÁGIL
# ==============================================================================

def app_control_sim():
    # --- Sidebar ---
    st.sidebar.markdown("### 📱 Menú SIMs")
    
    # Configuración de Zona Horaria
    zonas_disponibles = ["America/Guatemala", "America/Bogota", "America/Mexico_City", "UTC"]
    st.session_state.zona_horaria = st.sidebar.selectbox("Zona Horaria:", zonas_disponibles, index=0)
    st.sidebar.caption(f"Hora: {obtener_hora_actual()}")
    
    # MENÚ DINÁMICO
    if st.session_state.rol == "admin":
        menu_ops = ["Dashboard", "🔍 Consulta SIM", "Registrar SIM", "Actualizar Datos", "Traslados", "Cancelar/Gestionar", "Auditoría", "Reportes"]
    else:
        menu_ops = ["Dashboard", "🔍 Consulta SIM", "Reportes"]
    
    choice = st.sidebar.radio("Opciones:", menu_ops)

    if 'form_id' not in st.session_state: st.session_state.form_id = 0

    # --- PANTALLA DASHBOARD ---
    if choice == "Dashboard":
        st.title("📊 Tablero de Control")
        df = leer_datos("sims")
        if not df.empty and 'estado' in df.columns:
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Total Inventario", len(df))
            c2.metric("Activas", len(df[df['estado']=='Activa']))
            c3.metric("Botiquín", len(df[df['estado']=='Botiquin']))
            c4.metric("Canceladas", len(df[df['estado']=='Cancelada']))

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

    # --- PANTALLA CONSULTA SIM (MEJORADA - TIPO GOOGLE) ---
    elif choice == "🔍 Consulta SIM":
        st.subheader("🔍 Buscador Inteligente")
        st.caption("Escribe para filtrar. Selecciona una SIM para ver su diagnóstico inmediato.")
        
        df = leer_datos("sims")
        if not df.empty:
            # Preparamos los datos para el buscador (ICCID + Cliente para dar contexto)
            df['iccid'] = df['iccid'].astype(str)
            # Creamos una columna "etiqueta" que muestra info mientras buscas
            df['busqueda_visual'] = df['iccid'] + " | " + df['cliente'].astype(str) + " (" + df['estado'] + ")"
            
            # EL BUSCADOR AGIL (Selectbox con búsqueda)
            seleccion = st.selectbox(
                "Buscar SIM por ICCID o Cliente:",
                options=df['busqueda_visual'].tolist(),
                index=None,
                placeholder="Escribe aquí el número..."
            )
            
            st.markdown("---")

            if seleccion:
                # Extraemos el ICCID de la selección
                iccid_seleccionado = seleccion.split(" | ")[0]
                
                # Buscamos los datos completos
                fila = df[df['iccid'] == iccid_seleccionado].iloc[0]
                estado = fila['estado']
                
                # --- DISEÑO DE RESPUESTA VISUAL ---
                col_estado, col_significado = st.columns([1, 4])
                
                with col_estado:
                    # Semáforo Visual Grande
                    if estado == "Activa":
                        st.success(f"✅ {estado}")
                    elif estado == "Botiquin":
                        st.warning(f"📦 {estado}")
                    elif estado == "Retirada":
                        st.error(f"🚫 {estado}")
                    elif estado == "Cancelada":
                        st.error(f"❌ {estado}")
                    else:
                        st.info(f"ℹ️ {estado}")

                with col_significado:
                    # Significado al lado
                    if estado == "Activa":
                        st.markdown(f"**Funcionando Correctamente** | Cliente: **{fila['cliente']}** | Línea: **{fila['numero_linea']}**")
                    elif estado == "Botiquin":
                        st.markdown("**Disponible para usar** | Sin línea ni cliente asignado.")
                    elif estado == "Retirada":
                        st.markdown("**INSERVIBLE** | Esta tarjeta ya fue usada en un traslado. Desechar.")
                    elif estado == "Cancelada":
                        st.markdown("**Dada de Baja** | Servicio cancelado administrativamente.")

                # Detalles técnicos abajo (opcional, expandible)
                with st.expander("Ver detalles técnicos completos"):
                    st.json(fila.to_dict())

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
                ime = c1.text_input("IMEI", key=f"im_{kf}")
                plan = c2.text_input("Plan", key=f"pl_{kf}")
                pais = c1.selectbox("País", ["Guatemala", "El Salvador", "Honduras", "Nicaragua", "Costa Rica", "Panamá", "México", "Colombia"], key=f"pa_{kf}")
                cq = c2.number_input("Costo Q", key=f"cq_{kf}")
                cd = c1.number_input("Costo $", key=f"cd_{kf}")
                if st.form_submit_button("Guardar"):
                    if iccid:
                        d = {'iccid': iccid, 'numero_linea': linea, 'cliente': cli, 'placa': pla, 'imei': ime, 'tipo_plan': plan, 'pais': pais, 'costo_q': cq, 'costo_d': cd}
                        with st.spinner("Guardando..."):
                            if registrar_sim(d, st.session_state.usuario):
                                st.success("Guardado"); st.session_state.form_id += 1; refrescar_pagina(2)
                            else: st.error("Duplicado o Error")
                    else: st.warning("Falta ICCID")

        with tab2:
            st.markdown("### Carga Masiva (Excel)")
            df_t = pd.DataFrame(columns=['iccid', 'numero_linea', 'cliente', 'placa', 'imei', 'tipo_plan', 'pais', 'costo_q', 'costo_d'])
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='openpyxl') as writer: df_t.to_excel(writer, index=False)
            st.download_button("📥 Plantilla", buffer.getvalue(), "plantilla.xlsx")
            
            archivo = st.file_uploader("Subir Excel", type=["xlsx", "xls"])
            if archivo:
                try:
                    df_check = pd.read_excel(archivo)
                    st.success(f"✅ Archivo leído. Filas: {len(df_check)}")
                    cols = list(df_check.columns)
                    def f_idx(t, l):
                        t=t.lower()
                        for i,c in enumerate(l):
                            if t in str(c).lower(): return i
                        return 0
                    
                    st.write("Confirma columnas:")
                    c1,c2,c3 = st.columns(3)
                    si = c1.selectbox("ICCID", cols, index=f_idx("iccid",cols))
                    sl = c2.selectbox("Línea", cols, index=f_idx("linea",cols))
                    sc = c3.selectbox("Cliente", cols, index=f_idx("cliente",cols))
                    c4,c5,c6 = st.columns(3)
                    sp = c4.selectbox("Placa", cols, index=f_idx("placa",cols))
                    sim = c5.selectbox("IMEI", cols, index=f_idx("imei",cols))
                    spl = c6.selectbox("Plan", cols, index=f_idx("plan",cols))
                    c7,c8,c9 = st.columns(3)
                    spa = c7.selectbox("País", cols, index=f_idx("pais",cols))
                    scq = c8.selectbox("Costo Q", cols, index=f_idx("costo q",cols))
                    scd = c9.selectbox("Costo $", cols, index=f_idx("costo",cols))

                    if st.button(f"Procesar {len(df_check)} filas"):
                        df_final = pd.DataFrame()
                        df_final['iccid'] = df_check[si]
                        df_final['numero_linea'] = df_check[sl]
                        df_final['cliente'] = df_check[sc]
                        df_final['placa'] = df_check[sp]
                        df_final['imei'] = df_check[sim]
                        df_final['tipo_plan'] = df_check[spl]
                        df_final['pais'] = df_check[spa]
                        df_final['costo_q'] = df_check[scq]
                        df_final['costo_d'] = df_check[scd]
                        
                        with st.spinner("Enviando a Google..."):
                            c, e = procesar_carga_masiva_turbo(df_final, st.session_state.usuario)
                            st.success(f"✅ Éxito: {c} nuevas | {e} duplicados")
                            refrescar_pagina(5)
                except Exception as e: st.error(f"Error: {e}")

    # --- PANTALLA ACTUALIZAR ---
    elif choice == "Actualizar Datos":
        st.subheader("✏️ Editar")
        df = leer_datos("sims")
        if not df.empty and 'iccid' in df.columns:
            df['iccid'] = df['iccid'].astype(str)
            df['disp'] = df['iccid'] + " | " + df['cliente'].astype(str)
            sel = st.selectbox("Buscar:", df['disp'].tolist(), index=None, placeholder="Escribe...")
            if sel:
                ic = sel.split(" | ")[0]
                cur = df[df['iccid']==ic].iloc[0]
                with st.form("ed"):
                    c1, c2 = st.columns(2)
                    nl = c1.text_input("Línea", value=cur['numero_linea'])
                    nc = c2.text_input("Cliente", value=cur['cliente'])
                    np = c1.text_input("Placa", value=cur['placa'])
                    ni = c2.text_input("IMEI", value=cur['imei'])
                    npl = c1.text_input("Plan", value=cur['tipo_plan'])
                    paises = ["Guatemala", "El Salvador", "Honduras", "Nicaragua", "Costa Rica", "Panamá", "México", "Colombia"]
                    try: idx = paises.index(cur['pais'])
                    except: idx = 0
                    npa = c2.selectbox("País", paises, index=idx)
                    v_q = limpiar_moneda(cur['costo_q'])
                    v_d = limpiar_moneda(cur['costo_d'])
                    ncq = c1.number_input("Costo Q", value=v_q)
                    ncd = c2.number_input("Costo $", value=v_d)
                    if st.form_submit_button("Actualizar"):
                        d = {'numero_linea': nl, 'cliente': nc, 'placa': np, 'imei': ni, 'tipo_plan': npl, 'pais': npa, 'costo_q': ncq, 'costo_d': ncd}
                        with st.spinner("Actualizando..."):
                            if actualizar_datos_sim(ic, d, st.session_state.usuario):
                                st.success("Listo"); refrescar_pagina(2)

    # --- PANTALLA TRASLADOS ---
    elif choice == "Traslados":
        st.subheader("🔄 Traslados")
        df = leer_datos("sims")
        if not df.empty and 'iccid' in df.columns:
            df['iccid'] = df['iccid'].astype(str)
            dfo = df[~df['estado'].isin(['Retirada','Cancelada'])]
            dfd = df[df['estado']=='Botiquin']
            dfo['disp'] = dfo['iccid'] + " (" + dfo['numero_linea'].astype(str) + ")"
            c1, c2 = st.columns(2)
            orig = c1.selectbox("Vieja", dfo['disp'].tolist(), index=None, placeholder="Buscar...")
            dest = c2.selectbox("Nueva", dfd['iccid'].tolist(), index=None, placeholder="Buscar...")
            if orig and dest:
                if st.button("Trasladar"):
                    with st.spinner("Procesando..."):
                        ok, msg = traslado_sim(orig.split(" (")[0], dest, st.session_state.usuario)
                        if ok: st.balloons(); st.success(msg); refrescar_pagina(3)
                        else: st.error(msg)

    # --- PANTALLA CANCELAR ---
    elif choice == "Cancelar/Gestionar":
        st.subheader("⚠️ Cancelar")
        df = leer_datos("sims")
        if not df.empty and 'iccid' in df.columns:
            df['iccid'] = df['iccid'].astype(str)
            dfc = df[df['estado']!='Cancelada']
            dfc['disp'] = dfc['iccid'] + " | " + dfc['cliente'].astype(str)
            sel = st.selectbox("Buscar:", dfc['disp'].tolist(), index=None, placeholder="Buscar...")
            if sel:
                mot = st.text_input("Motivo")
                if st.button("Confirmar"):
                    with st.spinner("Cancelando..."):
                        if cancelar_servicio(sel.split(" | ")[0], st.session_state.usuario, mot):
                            st.success("Listo"); refrescar_pagina(2)

    # --- PANTALLA AUDITORÍA ---
    elif choice == "Auditoría":
        st.subheader("🕵️ Auditoría de Cambios")
        st.info("Filtra por usuario, tipo de acción o fecha.")
        df_hist = leer_datos("historial")
        if not df_hist.empty:
            try: df_hist['Fecha_DT'] = pd.to_datetime(df_hist['Fecha'], errors='coerce')
            except: df_hist['Fecha_DT'] = pd.NaT
            users_opt = sorted(df_hist['Usuario'].astype(str).unique().tolist())
            actions_opt = sorted(df_hist['Acción'].astype(str).unique().tolist())
            c1, c2, c3 = st.columns(3)
            with c1: sel_user = st.multiselect("Usuario", users_opt)
            with c2: sel_action = st.multiselect("Acción", actions_opt)
            with c3: fecha_filtro = st.date_input("Rango de Fecha", [])
            df_show = df_hist.copy()
            if sel_user: df_show = df_show[df_show['Usuario'].isin(sel_user)]
            if sel_action: df_show = df_show[df_show['Acción'].isin(sel_action)]
            if len(fecha_filtro) > 0:
                start_date = fecha_filtro[0]
                end_date = fecha_filtro[1] if len(fecha_filtro) > 1 else start_date
                df_show = df_show[(df_show['Fecha_DT'].dt.date >= start_date) & (df_show['Fecha_DT'].dt.date <= end_date)]
            if 'Fecha_DT' in df_show.columns: df_show = df_show.drop(columns=['Fecha_DT'])
            if 'Fecha' in df_show.columns: df_show = df_show.sort_values(by='Fecha', ascending=False)
            st.dataframe(df_show, use_container_width=True)
        else: st.warning("No hay registros de auditoría disponibles.")

    # --- PANTALLA REPORTES ---
    elif choice == "Reportes":
        st.subheader("📑 Reportes")
        df = leer_datos("sims")
        if not df.empty:
            try:
                p = st.sidebar.multiselect("Filtrar País", df['pais'].unique())
                if p: df = df[df['pais'].isin(p)]
            except: pass
            
            df_export = df.copy()
            if st.session_state.rol != 'admin':
                columnas_prohibidas = ['costo_q', 'costo_d']
                df_export = df_export.drop(columns=[c for c in columnas_prohibidas if c in df_export.columns], errors='ignore')
            else:
                try:
                    df_export['costo_q'] = df_export['costo_q'].apply(lambda x: f"Q {float(limpiar_moneda(x)):,.2f}")
                    df_export['costo_d'] = df_export['costo_d'].apply(lambda x: f"$ {float(limpiar_moneda(x)):,.2f}")
                except: pass

            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='openpyxl') as writer: df_export.to_excel(writer, index=False)
            st.download_button(label="📥 Descargar Excel con Formato", data=buffer.getvalue(), file_name="reporte_sims.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            st.dataframe(df_export)
# ==============================================================================
# 5. MÓDULO C: GESTIÓN USUARIOS (CON EMAIL Y NOMBRE COMPLETO)
# ==============================================================================
def app_gestion_usuarios():
    st.markdown("## 👤 Administración Usuarios")
    tab1, tab2 = st.tabs(["Crear Usuario", "Ver Lista"])
    
    with tab1:
        st.info("El usuario recibirá un correo para activar su cuenta.")
        with st.form("crear_user_mail"):
            col1, col2 = st.columns(2)
            new_email = col1.text_input("Correo Electrónico (Será el usuario)")
            new_nombre = col2.text_input("Nombre Completo")
            new_rol = st.selectbox("Rol", ["admin", "general"])
            
            if st.form_submit_button("Crear Usuario"):
                if "@" not in new_email:
                    st.error("Por favor ingresa un correo válido.")
                elif not new_nombre:
                    st.error("El nombre completo es obligatorio.")
                else:
                    sheet = conectar_google()
                    ws = sheet.worksheet("usuarios")
                    
                    users = ws.col_values(1) 
                    if new_email in users: 
                        st.error("Este correo ya está registrado.")
                    else:
                        token = str(uuid.uuid4())
                        fila = [new_email, "PENDIENTE", new_rol, new_nombre, token]
                        with st.spinner("Guardando y enviando correo..."):
                            ws.append_row(fila)
                            if enviar_link_activacion(new_email, token, new_nombre):
                                st.success(f"✅ Usuario creado. Correo enviado a {new_email}")
                            else: 
                                st.warning("Usuario creado en base de datos, pero falló el envío del correo.")
    with tab2:
        sheet = conectar_google()
        st.dataframe(pd.DataFrame(sheet.worksheet("usuarios").get_all_records())[['email','nombre','rol']])

# ==============================================================================
# 6. MAIN LOOP
# ==============================================================================
def main():
    if gestionar_reset_password(): return

    if 'usuario' not in st.session_state: st.session_state.usuario = None
    if 'nombre' not in st.session_state: st.session_state.nombre = None
    if 'rol' not in st.session_state: st.session_state.rol = None
    
    # --- PANTALLA DE LOGIN ---
    if st.session_state.usuario is None:
        col1, col2, col3 = st.columns([1,2,1])
        with col2:
            st.title("🔐 Acceso Control SIM")
            
            tab_login, tab_recup = st.tabs(["Iniciar Sesión", "Olvidé mi contraseña"])
            
            with tab_login:
                u = st.text_input("Correo Electrónico")
                p = st.text_input("Contraseña", type="password")
                if st.button("Ingresar", type="primary"):
                    try:
                        sheet = conectar_google()
                        ws = sheet.worksheet("usuarios")
                        df = pd.DataFrame(ws.get_all_records())
                        df['email'] = df['email'].astype(str)
                        
                        user_row = df[df['email'] == u]
                        if not user_row.empty:
                            hash_guardado = str(user_row.iloc[0]['password'])
                            if check_hashes(p, hash_guardado):
                                st.session_state.usuario = u
                                st.session_state.rol = user_row.iloc[0]['rol']
                                st.session_state.nombre = user_row.iloc[0]['nombre']
                                st.toast(f"Bienvenido {st.session_state.nombre}")
                                time.sleep(1)
                                st.rerun()
                            else: st.error("Contraseña incorrecta.")
                        else: st.error("Usuario no encontrado.")
                    except Exception as e: st.error(f"Error login: {e}")
            
            with tab_recup:
                st.write("Ingresa tu correo y te enviaremos un enlace para restablecerla.")
                rec_email = st.text_input("Correo de recuperación")
                if st.button("Enviar enlace"):
                    sheet = conectar_google()
                    ws = sheet.worksheet("usuarios")
                    df = pd.DataFrame(ws.get_all_records())
                    df['email'] = df['email'].astype(str)
                    
                    user_row = df[df['email'] == rec_email]
                    if not user_row.empty:
                        token_nuevo = str(uuid.uuid4())
                        nombre_user = user_row.iloc[0]['nombre']
                        
                        cell = ws.find(rec_email)
                        ws.update_cell(cell.row, 5, token_nuevo)
                        
                        if enviar_link_recuperacion(rec_email, token_nuevo, nombre_user):
                            st.success("Correo enviado. Revisa tu bandeja de entrada.")
                        else:
                            st.error("Error al enviar el correo.")
                    else:
                        st.warning("Si el correo existe, se enviará el enlace.")

        return

    # --- SIDEBAR PRINCIPAL (YA LOGUEADO) ---
    st.sidebar.title(f"👤 {st.session_state.nombre}")
    st.sidebar.caption(f"{st.session_state.usuario} | {st.session_state.rol}")
    
    opciones_sistema = ["Control SIM"]
    if st.session_state.rol == "admin":
        opciones_sistema.append("Gestión Usuarios")
        
    app_mode = st.sidebar.selectbox("📍 SISTEMA:", opciones_sistema)
    st.sidebar.markdown("---")
    
    if app_mode == "Control SIM": 
        app_control_sim()
    elif app_mode == "Gestión Usuarios":
        app_gestion_usuarios()

    if st.sidebar.button("Cerrar Sesión"):
        st.session_state.usuario = None
        st.session_state.nombre = None
        st.session_state.rol = None
        st.rerun()

if __name__ == "__main__":
    main()


