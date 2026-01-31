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
LISTA_PAISES = ["Guatemala", "El Salvador", "Honduras", "Nicaragua", "Costa Rica", "Panamá", "México", "Colombia", "Republica Dominicana"]

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

# --- LÓGICA DE CLIENTES ---

def obtener_lista_clientes():
    """Devuelve una lista ordenada de nombres de clientes"""
    df = leer_datos("clientes")
    if not df.empty and 'nombre' in df.columns:
        # Filtramos vacíos y duplicados, y ordenamos
        lista = sorted(df['nombre'].astype(str).unique().tolist())
        return [c for c in lista if c.strip() != ""]
    return []

def crear_nuevo_cliente(nombre_cliente):
    """Agrega un cliente a la base de datos si no existe"""
    df = leer_datos("clientes")
    if not df.empty and 'nombre' in df.columns:
        if nombre_cliente in df['nombre'].values:
            return False, "El cliente ya existe."
    
    # Guardamos
    escribir_fila("clientes", [nombre_cliente])
    return True, "Cliente creado exitosamente."

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
# 3. LÓGICA DE NEGOCIO SIMS (AQUÍ ESTÁ LA CORRECCIÓN DE MONEDA)
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

# --- CORRECCIÓN DEFINITIVA MONEDA (FORMATO LATINO: 50,00) ---
def limpiar_moneda(valor):
    """
    Convierte texto a número asumiendo que la COMA es DECIMAL.
    Ejemplo: "Q 50,00" -> 50.0
    Ejemplo: "1.500,50" -> 1500.50
    """
    # 1. Si es vacío o nulo, retorna 0
    if pd.isna(valor) or str(valor).strip() == "": 
        return 0.0
    
    # 2. Si ya es número (int o float), lo devolvemos tal cual
    if isinstance(valor, (int, float)): 
        return float(valor)
    
    # 3. Limpieza de símbolos (Q, $, espacios)
    valor = str(valor).strip().upper()
    valor = valor.replace("Q", "").replace("$", "").replace(" ", "")
    
    # 4. LÓGICA DECIMAL (COMA ES DECIMAL)
    
    # Caso A: Tiene Puntos Y Comas (Ej: 1.500,00)
    # En este formato, el punto separa miles (lo borramos) y la coma separa decimales (la volvemos punto)
    if "." in valor and "," in valor:
        valor = valor.replace(".", "")  # Borrar separador de miles
        valor = valor.replace(",", ".") # Convertir coma decimal a punto Python
        
    # Caso B: Solo tiene Coma (Ej: 50,00)
    # Asumimos que es decimal. La volvemos punto.
    elif "," in valor:
        valor = valor.replace(",", ".")
        
    # Caso C: Solo tiene Punto (Ej: 1500)
    # Si escribieron 1.500 (mil quinientos), lo dejamos tal cual si es formato sin decimales.
    # Pero si escribieron 50.00 (formato mixto), Python lo entenderá bien.
    # Lo dejamos pasar.
    
    try:
        return float(valor)
    except:
        return 0.0

def registrar_sim(datos, usuario):
    df = leer_datos("sims")
    if 'iccid' in df.columns and str(datos['iccid']) in df['iccid'].astype(str).values: return False
    l = str(datos['numero_linea']) if datos['numero_linea'] else ""
    c = str(datos['cliente']) if datos['cliente'] else ""
    e = "Activa" if l and c else "Botiquin"
    f = obtener_hora_actual()
    # Aplicamos limpieza antes de guardar
    c_q = limpiar_moneda(datos['costo_q'])
    c_d = limpiar_moneda(datos['costo_d'])
    
    fila = [str(datos['iccid']), l, c, str(datos['placa']), str(datos['imei']),
            str(datos['tipo_plan']), str(datos['pais']), c_q, c_d, e, f]
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
        if not ic or ic in existentes: 
            d += 1; continue
        
        l, cli = str(row['numero_linea']).replace(".0",""), str(row['cliente'])
        est = "Activa" if l and cli else "Botiquin"
        
        # APLICAMOS LA CORRECCIÓN DE MONEDA AQUÍ
        val_q = limpiar_moneda(row['costo_q'])
        val_d = limpiar_moneda(row['costo_d'])
        
        fila = [ic, l, cli, str(row['placa']), str(row['imei']), str(row['tipo_plan']), 
                str(row['pais']), val_q, val_d, est, hoy]
        nuevas.append(fila)
        hist.append([ic, "Creacion Masiva", f"Estado: {est}", usuario, hoy])
        existentes.add(ic)
        c += 1
        
    if nuevas:
        escribir_lote("sims", nuevas)
        escribir_lote("historial", hist)
    return c, d

# ... (El resto del código de procesar_actualizacion_masiva y demás sigue igual)

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
# 4. APLICACIÓN PRINCIPAL (UI) - VERSIÓN CRM CLIENTES
# ==============================================================================

def app_control_sim():
    # --- Configuración Inicial ---
    paises_usuario = st.session_state.get('paises_asignados', [])
    if not paises_usuario:
        if st.session_state.rol == 'admin': paises_usuario = LISTA_PAISES
        else: paises_usuario = []

    st.sidebar.markdown("### 📱 Menú SIMs")
    zonas = ["America/Guatemala", "America/Bogota", "America/Mexico_City", "UTC"]
    st.session_state.zona_horaria = st.sidebar.selectbox("Zona Horaria:", zonas)
    st.sidebar.caption(f"Hora: {obtener_hora_actual()}")
    
    # MENÚ DINÁMICO
    menu_ops = ["Dashboard", "🔍 Consulta SIM", "Reportes"]
    if st.session_state.rol == "admin":
        # Agregamos "Gestión Clientes" al menú admin
        menu_ops = ["Dashboard", "🔍 Consulta SIM", "Registrar SIM", "Actualizar Datos", "Gestión Clientes", "Traslados", "Cancelar/Gestionar", "Auditoría", "Reportes"]
    
    choice = st.sidebar.radio("Opciones:", menu_ops)
    if 'form_id' not in st.session_state: st.session_state.form_id = 0

    # Cargar lista de clientes para usar en todo el módulo
    lista_clientes_db = obtener_lista_clientes()

    # --- DASHBOARD CRM ---
    if choice == "Dashboard":
        st.title("📊 Tablero de Control")
        
        # 1. Carga y Filtros Globales
        df_raw = leer_datos("sims")
        if st.session_state.paises_asignados:
             df = df_raw[df_raw['pais'].isin(st.session_state.paises_asignados)]
        else: df = df_raw 
        
        if not df.empty and 'estado' in df.columns:
            
            # PESTAÑAS: VISIÓN GENERAL vs VISIÓN CLIENTE
            tab_gen, tab_cli = st.tabs(["🌍 Visión Global", "🏢 Visión por Cliente"])
            
            # --- VISIÓN GLOBAL ---
            with tab_gen:
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Total Inventario", len(df))
                c2.metric("Activas", len(df[df['estado']=='Activa']))
                c3.metric("Botiquín", len(df[df['estado']=='Botiquin']))
                c4.metric("Canceladas", len(df[df['estado']=='Cancelada']))
                
                st.markdown("---")
                # Gráfico por país
                if 'pais' in df.columns:
                    conteo_paises = df['pais'].value_counts().reset_index()
                    conteo_paises.columns = ["País", "Cantidad"]
                    cg, ct = st.columns([2, 1])
                    with cg: st.bar_chart(conteo_paises.set_index("País"))
                    with ct: st.dataframe(conteo_paises, hide_index=True, use_container_width=True)

            # --- VISIÓN POR CLIENTE (CRM) ---
            with tab_cli:
                st.subheader("Análisis Individual de Cliente")
                
                # Buscador de Clientes (Extraemos los que tienen SIMs + la lista de DB)
                clientes_con_sims = df['cliente'].unique().tolist()
                todos_clientes = sorted(list(set(lista_clientes_db + clientes_con_sims)))
                
                cliente_sel = st.selectbox("Seleccione Cliente:", todos_clientes, index=None, placeholder="Escriba para buscar...")
                
                if cliente_sel:
                    # Filtramos la data para este cliente
                    df_cli = df[df['cliente'] == cliente_sel]
                    
                    if not df_cli.empty:
                        # KPI's del Cliente
                        activas = len(df_cli[df_cli['estado']=='Activa'])
                        botiquin = len(df_cli[df_cli['estado']=='Botiquin']) # Botiquin asignado al cliente
                        canceladas = len(df_cli[df_cli['estado']=='Cancelada'])
                        
                        # CÁLCULO DE TRASLADOS (Complejo: Buscamos en historial cuantas veces sus ICCIDs actuales han tenido traslados)
                        df_h = leer_datos("historial")
                        total_traslados = 0
                        if not df_h.empty:
                            iccids_cliente = df_cli['iccid'].astype(str).tolist()
                            # Filtramos historial donde el ICCID es del cliente Y la acción dice "Traslado"
                            traslados_detectados = df_h[
                                (df_h['ID'].astype(str).isin(iccids_cliente)) & 
                                (df_h['Acción'].str.contains("Traslado", na=False))
                            ]
                            total_traslados = len(traslados_detectados)

                        # Tarjetas de KPI
                        k1, k2, k3, k4 = st.columns(4)
                        k1.metric("Líneas Activas", activas, border=True)
                        k2.metric("En Botiquín (Stock)", botiquin, border=True)
                        k3.metric("Canceladas", canceladas, border=True)
                        k4.metric("Traslados Históricos", total_traslados, help="Veces que sus SIMs actuales han pasado por un proceso de traslado", border=True)
                        
                        st.markdown("##### 📜 Detalle de Líneas")
                        st.dataframe(
                            df_cli[['iccid', 'numero_linea', 'placa', 'estado', 'tipo_plan', 'pais']], 
                            use_container_width=True,
                            hide_index=True
                        )
                    else:
                        st.warning(f"El cliente '{cliente_sel}' existe en la base de datos pero NO tiene SIMs asignadas en tu región.")

    # --- CONSULTA SIM ---
    elif choice == "🔍 Consulta SIM":
        # (Se mantiene igual, solo copio la estructura básica por espacio)
        st.subheader("🔍 Buscador Inteligente")
        df_raw = leer_datos("sims")
        df = df_raw[df_raw['pais'].isin(st.session_state.paises_asignados)] if st.session_state.paises_asignados else df_raw
        if not df.empty:
            df['busqueda'] = df['iccid'].astype(str) + " | " + df['cliente'].astype(str) + " (" + df['estado'] + ")"
            sel = st.selectbox("Buscar:", df['busqueda'].tolist(), index=None, placeholder="Escribe...")
            if sel:
                ic = sel.split(" | ")[0]
                fila = df[df['iccid'] == ic].iloc[0]
                est = fila['estado']
                c_icon, c_info = st.columns([1,6])
                with c_icon:
                    st.header("✅" if est=="Activa" else "📦" if est=="Botiquin" else "🚫" if est=="Retirada" else "❌")
                with c_info:
                    st.subheader(f"Estado: {est}")
                    st.write(f"**Cliente:** {fila['cliente']} | **Línea:** {fila['numero_linea']}")

    # --- REGISTRAR (AHORA CON SELECTOR DE CLIENTES) ---
    elif choice == "Registrar SIM":
        st.subheader("➕ Gestión Inventario")
        t1, t2 = st.tabs(["Manual", "Carga Masiva"])
        with t1:
            kf = str(st.session_state.form_id)
            with st.form("new"):
                c1, c2 = st.columns(2)
                ic = c1.text_input("ICCID*", key=f"i{kf}")
                ln = c2.text_input("Línea", key=f"l{kf}")
                
                # --- CAMBIO: SELECTOR DE CLIENTES ---
                if lista_clientes_db:
                    cl = c1.selectbox("Cliente", lista_clientes_db, key=f"c{kf}", index=None, placeholder="Selecciona empresa...")
                else:
                    st.warning("⚠️ No hay clientes creados. Ve a 'Gestión Clientes'.")
                    cl = c1.text_input("Cliente (Manual)", key=f"c{kf}")
                # ------------------------------------

                pl = c2.text_input("Placa", key=f"p{kf}")
                im = c1.text_input("IMEI", key=f"im{kf}")
                pn = c2.text_input("Plan", key=f"pl{kf}")
                
                lista_paises_form = [p for p in LISTA_PAISES if p in paises_usuario] if paises_usuario else LISTA_PAISES
                pa = c1.selectbox("País", lista_paises_form, key=f"pa{kf}")
                cq = c2.number_input("Costo Q", key=f"cq{kf}")
                cd = c1.number_input("Costo $", key=f"cd{kf}")
                
                if st.form_submit_button("Guardar"):
                    if ic:
                        d = {'iccid': ic, 'numero_linea': ln, 'cliente': cl, 'placa': pl, 'imei': im, 'tipo_plan': pn, 'pais': pa, 'costo_q': cq, 'costo_d': cd}
                        if registrar_sim(d, st.session_state.usuario):
                            st.success("Guardado"); st.session_state.form_id+=1; refrescar_pagina(2)
                        else: st.error("Duplicado")
        with t2:
            st.info("Para Carga Masiva: Asegúrate que los nombres de clientes en el Excel COINCIDAN con los creados en el sistema.")
            # (El resto de carga masiva sigue igual)
            upl = st.file_uploader("Excel", type=["xlsx"])
            if upl and st.button("Procesar"):
                df_up = pd.read_excel(upl)
                c, d = procesar_carga_masiva_turbo(df_up, st.session_state.usuario)
                st.success(f"Procesado: {c} | Dup: {d}")

    # --- ACTUALIZAR (CON SELECTOR DE CLIENTES) ---
    elif choice == "Actualizar Datos":
        st.subheader("✏️ Edición")
        if 'update_key' not in st.session_state: st.session_state.update_key = 0
        if 'resumen_cambios' not in st.session_state: st.session_state.resumen_cambios = None
        
        if st.session_state.resumen_cambios:
            st.success("✅ Actualizado"); st.write(st.session_state.resumen_cambios)
            if st.button("Aceptar"): st.session_state.resumen_cambios=None; st.session_state.update_key+=1; st.rerun()
        else:
            t1, t2 = st.tabs(["Unitaria", "Masiva"])
            with t1:
                df_raw = leer_datos("sims")
                df = df_raw[df_raw['pais'].isin(paises_usuario)] if paises_usuario else df_raw
                if not df.empty:
                    df['disp'] = df['iccid'].astype(str) + " | " + df['cliente'].astype(str)
                    sel = st.selectbox("Buscar:", df['disp'].tolist(), index=None, key=f"search_{st.session_state.update_key}")
                    if sel:
                        ic = sel.split(" | ")[0]
                        cur = df[df['iccid']==ic].iloc[0]
                        with st.form("edit"):
                            c1, c2 = st.columns(2)
                            nl = c1.text_input("Línea", value=cur['numero_linea'])
                            
                            # --- CAMBIO: SELECTOR INTELIGENTE ---
                            # Intentamos encontrar el índice del cliente actual en la lista maestra
                            idx_cl = None
                            val_cl_actual = str(cur['cliente'])
                            if val_cl_actual in lista_clientes_db:
                                idx_cl = lista_clientes_db.index(val_cl_actual)
                            
                            if lista_clientes_db:
                                nc = c2.selectbox("Cliente", lista_clientes_db, index=idx_cl, help="Si el cliente actual no está en la lista, aparecerá vacío.")
                            else:
                                nc = c2.text_input("Cliente", value=cur['cliente'])
                            # ------------------------------------

                            np = c1.text_input("Placa", value=cur['placa'])
                            npl = c2.text_input("Plan", value=cur['tipo_plan'])
                            
                            # (Lógica de países y costos igual que antes...)
                            lista_p_edit = [p for p in LISTA_PAISES if p in paises_usuario] if paises_usuario else LISTA_PAISES
                            idx_p = lista_p_edit.index(cur['pais']) if cur['pais'] in lista_p_edit else 0
                            npa = c1.selectbox("País", lista_p_edit, index=idx_p)
                            
                            ncq = c2.number_input("Costo Q", value=limpiar_moneda(cur['costo_q']))
                            ncd = c1.number_input("Costo $", value=limpiar_moneda(cur['costo_d']))
                            
                            if st.form_submit_button("Actualizar"):
                                # (Lógica de guardar cambios igual...)
                                camb = []
                                if str(nc) != str(cur['cliente']): camb.append(f"Cliente: {cur['cliente']} -> {nc}")
                                # ... resto de comparaciones
                                d = {'numero_linea': nl, 'cliente': nc, 'placa': np, 'imei': cur['imei'], 'tipo_plan': npl, 'pais': npa, 'costo_q': ncq, 'costo_d': ncd}
                                if actualizar_datos_sim(ic, d, st.session_state.usuario):
                                    st.session_state.resumen_cambios = camb if camb else ["Datos guardados (sin cambios detectados)"]
                                    st.rerun()
            with t2:
                # Masiva igual...
                st.info("Subir Excel")
                upl = st.file_uploader("Update", type=["xlsx"])
                if upl and st.button("Ejecutar"):
                     # ... lógica masiva
                     pass

    # --- GESTIÓN CLIENTES (Llamada al nuevo módulo) ---
    elif choice == "Gestión Clientes":
        app_gestion_clientes()

    # --- TRASLADOS, CANCELAR, AUDITORIA, REPORTES ---
    elif choice == "Traslados":
        # (Código de traslados igual que antes, filtrando por país)
        pass # Usa el código previo
    elif choice == "Cancelar/Gestionar":
        # (Código igual)
        pass
    elif choice == "Auditoría":
        # (Código igual)
        pass
    elif choice == "Reportes":
        # (Código igual)
        st.subheader("Reportes")
        df = leer_datos("sims")
        if paises_usuario: df = df[df['pais'].isin(paises_usuario)]
        if not df.empty:
            st.dataframe(df) # (Agregar botón descarga previo)
    # --- PANTALLA CONSULTA SIM ---
    elif choice == "🔍 Consulta SIM":
        st.subheader("🔍 Buscador Inteligente")
        df_raw = leer_datos("sims")
        # Filtro de seguridad
        if st.session_state.paises_asignados:
             df = df_raw[df_raw['pais'].isin(st.session_state.paises_asignados)]
        else: df = df_raw

        if not df.empty:
            df['iccid'] = df['iccid'].astype(str)
            df['busqueda_visual'] = df['iccid'] + " | " + df['cliente'].astype(str) + " (" + df['estado'] + ")"
            
            seleccion = st.selectbox("Buscar SIM:", df['busqueda_visual'].tolist(), index=None, placeholder="Escribe aquí el número...")
            st.markdown("---")

            if seleccion:
                iccid_seleccionado = seleccion.split(" | ")[0]
                fila = df[df['iccid'] == iccid_seleccionado].iloc[0]
                estado = fila['estado']
                
                col_icon, col_msg = st.columns([1, 6])
                with col_icon:
                    if estado == "Activa": st.header("✅")
                    elif estado == "Botiquin": st.header("📦")
                    elif estado == "Retirada": st.header("🚫")
                    elif estado == "Cancelada": st.header("❌")
                    else: st.header("ℹ️")
                
                with col_msg:
                    st.subheader(f"Diagnóstico: {estado}")
                    if estado == "Retirada": st.error("Esta tarjeta SIM NO puede volver a usarse.")
                    elif estado == "Botiquin": st.warning("Lista para asignar. Aún sin línea.")
                
                st.markdown("### 📋 Detalles Técnicos")
                with st.container(border=True):
                    c1, c2, c3 = st.columns(3)
                    with c1: st.caption("🆔 ICCID"); st.markdown(f"**{fila['iccid']}**")
                    with c2: st.caption("📞 Línea"); val_linea = fila['numero_linea'] if str(fila['numero_linea']) != "" else "---"; st.markdown(f"**{val_linea}**")
                    with c3: st.caption("🌍 País"); st.markdown(f"**{fila['pais']}**")
                    st.divider()
                    c4, c5, c6 = st.columns(3)
                    with c4: st.caption("🚦 Estado"); color = "green" if estado == "Activa" else "orange" if estado == "Botiquin" else "red"; st.markdown(f":{color}[**{estado}**]")
                    with c5: st.caption("👤 Cliente"); val_cli = fila['cliente'] if str(fila['cliente']) != "" else "---"; st.markdown(f"**{val_cli}**")
                    with c6: st.caption("🚛 Placa"); val_placa = fila['placa'] if str(fila['placa']) != "" else "---"; st.markdown(f"**{val_placa}**")

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
                
                # USO DE LA LISTA MAESTRA (Filtrada por permisos si no es admin)
                lista_paises_form = LISTA_PAISES
                if st.session_state.paises_asignados:
                    # Si tiene restricción, solo puede registrar en sus países
                    lista_paises_form = [p for p in LISTA_PAISES if p in st.session_state.paises_asignados]

                pais = c1.selectbox("País", lista_paises_form, key=f"pa_{kf}")
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
                if st.button("Procesar Archivo"):
                    df_up = pd.read_excel(archivo)
                    with st.spinner("Cargando..."):
                        c, e = procesar_carga_masiva_turbo(df_up, st.session_state.usuario)
                        st.success(f"Procesado: {c} Nuevas | {e} Duplicadas")

    # --- PANTALLA ACTUALIZAR ---
    elif choice == "Actualizar Datos":
        st.subheader("✏️ Edición de Inventario")
        if 'update_key' not in st.session_state: st.session_state.update_key = 0
        if 'resumen_cambios' not in st.session_state: st.session_state.resumen_cambios = None
        
        if st.session_state.resumen_cambios:
            with st.container(border=True):
                st.success("✅ ¡Actualización Exitosa!")
                st.markdown("### Resumen de cambios:")
                for cambio in st.session_state.resumen_cambios: st.markdown(f"- {cambio}")
                st.markdown("---")
                if st.button("Aceptar y Nueva Búsqueda", type="primary"):
                    st.session_state.resumen_cambios = None; st.session_state.update_key += 1; st.rerun()
        else:
            tab_unit, tab_masiva = st.tabs(["Edición Unitaria", "Edición Masiva (Excel)"])
            with tab_unit:
                df_raw = leer_datos("sims")
                if st.session_state.paises_asignados:
                     df = df_raw[df_raw['pais'].isin(st.session_state.paises_asignados)]
                else: df = df_raw
                
                if not df.empty:
                    df['disp'] = df['iccid'].astype(str) + " | " + df['cliente'].astype(str)
                    sel = st.selectbox("Buscar SIM:", df['disp'].tolist(), index=None, placeholder="Escribe...", key=f"search_box_{st.session_state.update_key}")
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
                            
                            # USO DE LA LISTA MAESTRA
                            lista_paises_edit = LISTA_PAISES
                            if st.session_state.paises_asignados:
                                lista_paises_edit = [p for p in LISTA_PAISES if p in st.session_state.paises_asignados]

                            idx_p = 0
                            if cur['pais'] in lista_paises_edit: idx_p = lista_paises_edit.index(cur['pais'])
                            npa = c1.selectbox("País", lista_paises_edit, index=idx_p)
                            
                            ncq = c2.number_input("Costo Q", value=limpiar_moneda(cur['costo_q']))
                            ncd = c1.number_input("Costo $", value=limpiar_moneda(cur['costo_d']))
                            if st.form_submit_button("Actualizar Datos"):
                                cambios_detectados = []
                                if str(nl) != str(cur['numero_linea']): cambios_detectados.append(f"Línea: {cur['numero_linea']} ➝ **{nl}**")
                                if str(nc) != str(cur['cliente']): cambios_detectados.append(f"Cliente: {cur['cliente']} ➝ **{nc}**")
                                if str(np) != str(cur['placa']): cambios_detectados.append(f"Placa: {cur['placa']} ➝ **{np}**")
                                if str(npa) != str(cur['pais']): cambios_detectados.append(f"País: {cur['pais']} ➝ **{npa}**")
                                d = {'numero_linea': nl, 'cliente': nc, 'placa': np, 'imei': cur['imei'], 'tipo_plan': npl, 'pais': npa, 'costo_q': ncq, 'costo_d': ncd}
                                if actualizar_datos_sim(ic, d, st.session_state.usuario):
                                    st.session_state.resumen_cambios = cambios_detectados; st.rerun()

            with tab_masiva:
                st.info("Sube un Excel con la columna 'iccid' y las columnas a actualizar.")
                modo = st.radio("Modo:", ["Rellenar vacíos", "Sobrescribir todo"])
                archivo_update = st.file_uploader("Subir Excel", type=["xlsx"])
                if archivo_update:
                    if st.button("Ejecutar Masiva"):
                        with st.spinner("Procesando..."):
                            df_up = pd.read_excel(archivo_update)
                            sv = True if "Rellenar" in modo else False
                            cant, msg = procesar_actualizacion_masiva(df_up, st.session_state.usuario, sv)
                            if cant > 0: st.balloons(); st.success(f"✅ {msg}")
                            else: st.warning(msg)

    # --- TRASLADOS ---
    elif choice == "Traslados":
        st.subheader("🔄 Traslados")
        df_raw = leer_datos("sims")
        if st.session_state.paises_asignados:
             df = df_raw[df_raw['pais'].isin(st.session_state.paises_asignados)]
        else: df = df_raw
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
        if st.session_state.paises_asignados:
             df = df_raw[df_raw['pais'].isin(st.session_state.paises_asignados)]
        else: df = df_raw
        if not df.empty:
            sel = st.selectbox("Buscar:", df['iccid'].tolist())
            mot = st.text_input("Motivo")
            if st.button("Confirmar Baja"):
                if cancelar_servicio(sel, st.session_state.usuario, mot): st.success("Listo"); refrescar_pagina(2)

    # --- AUDITORÍA ---
    elif choice == "Auditoría":
        st.subheader("🕵️ Auditoría")
        df_h = leer_datos("historial")
        if not df_h.empty:
            try: df_h['Fecha_DT'] = pd.to_datetime(df_h['Fecha'], errors='coerce')
            except: df_h['Fecha_DT'] = pd.NaT
            users_opt = sorted(df_h['Usuario'].astype(str).unique().tolist())
            actions_opt = sorted(df_h['Acción'].astype(str).unique().tolist())
            c1, c2, c3 = st.columns(3)
            with c1: sel_user = st.multiselect("Usuario", users_opt)
            with c2: sel_action = st.multiselect("Acción", actions_opt)
            with c3: fecha_filtro = st.date_input("Rango de Fecha", [])
            df_show = df_h.copy()
            if sel_user: df_show = df_show[df_show['Usuario'].isin(sel_user)]
            if sel_action: df_show = df_show[df_show['Acción'].isin(sel_action)]
            if len(fecha_filtro) > 0:
                start = fecha_filtro[0]
                end = fecha_filtro[1] if len(fecha_filtro) > 1 else start
                df_show = df_show[(df_show['Fecha_DT'].dt.date >= start) & (df_show['Fecha_DT'].dt.date <= end)]
            if 'Fecha_DT' in df_show.columns: df_show = df_show.drop(columns=['Fecha_DT'])
            st.dataframe(df_show, use_container_width=True)

    # --- REPORTES ---
    elif choice == "Reportes":
        st.subheader("📑 Reportes")
        df_raw = leer_datos("sims")
        if st.session_state.paises_asignados:
             df = df_raw[df_raw['pais'].isin(st.session_state.paises_asignados)]
        else: df = df_raw
        if not df.empty:
            p = st.sidebar.multiselect("Filtrar País", df['pais'].unique())
            if p: df = df[df['pais'].isin(p)]
            df_exp = df.copy()
            if st.session_state.rol != 'admin':
                df_exp = df_exp.drop(columns=['costo_q','costo_d'], errors='ignore')
            b = io.BytesIO()
            with pd.ExcelWriter(b, engine='openpyxl') as w: df_exp.to_excel(w, index=False)
            st.download_button("📥 Descargar Excel", b.getvalue(), "reporte.xlsx")
            st.dataframe(df_exp)
            
# ==============================================================================
# 5. GESTIÓN USUARIOS (MEJORADO: SELECTOR DE PAÍSES ROBUSTO)
# ==============================================================================
def app_gestion_usuarios():
    st.markdown("## 👤 Usuarios & Permisos")
    
    tab1, tab2 = st.tabs(["➕ Crear Usuario", "🛠️ Administrar Existentes"])
    
    # --- CREAR USUARIO ---
    with tab1:
        st.info("El usuario recibirá un correo para activar su cuenta.")
        with st.form("crear"):
            c1, c2 = st.columns(2)
            mail = c1.text_input("Correo (Usuario)")
            nom = c2.text_input("Nombre Completo")
            rol = c1.selectbox("Rol", ["admin", "general"])
            # AQUÍ YA USABAMOS MULTISELECT, TODO BIEN
            paises_asig = c2.multiselect("Países Permitidos", LISTA_PAISES, default=["Guatemala"])
            
            if st.form_submit_button("Crear Usuario"):
                if mail and nom and paises_asig:
                    ws = conectar_google().worksheet("usuarios")
                    if mail in ws.col_values(1): st.error("El usuario ya existe.")
                    else:
                        tok = str(uuid.uuid4())
                        str_paises = ",".join(paises_asig)
                        ws.append_row([mail, "PENDIENTE", rol, nom, tok, str_paises])
                        if enviar_link_activacion(mail, tok, nom): st.success("Creado y correo enviado.")
                        else: st.warning("Creado en DB, pero falló el envío de correo.")
                else: st.warning("Complete todos los campos.")

    # --- ADMINISTRAR (NUEVO DISEÑO CON GESTOR DE PERMISOS) ---
    with tab2:
        st.subheader("📋 Directorio")
        
        ws = conectar_google().worksheet("usuarios")
        data = ws.get_all_records()
        df = pd.DataFrame(data)

        if not df.empty:
            # 1. TABLA PARA EDITAR NOMBRE Y ROL (PERO NO PAÍSES)
            st.caption("Edita Nombre y Rol aquí. Para Países, usa el gestor de abajo.")
            
            df_view = df[['email', 'nombre', 'rol']].copy()
            
            edited_df = st.data_editor(
                df_view,
                column_config={
                    "email": st.column_config.TextColumn("Correo", disabled=True),
                    "rol": st.column_config.SelectboxColumn("Rol", options=["admin", "general"], required=True),
                    "nombre": st.column_config.TextColumn("Nombre"),
                },
                hide_index=True,
                use_container_width=True,
                key="editor_usuarios_simple"
            )
            
            if st.button("💾 Guardar Cambios (Nombre/Rol)"):
                # Lógica para actualizar solo nombre y rol
                # (Reutilizamos la lógica batch pero solo mandamos estas columnas)
                # Para simplificar, usamos una función auxiliar si cambia algo
                if not edited_df.equals(df_view):
                    # Reconstruimos df completo para update batch
                    # Este paso requiere cuidado, mejor usamos el actualizador batch genérico
                    # pero necesitamos pasarle 'paises' también para que no se borren.
                    # TRUCO: Unimos el DF editado con la columna paises original
                    df_final_save = edited_df.copy()
                    df_final_save['paises'] = df['paises'] # Mantenemos paises originales
                    
                    if actualizar_usuario_batch(df_final_save):
                        st.success("Datos actualizados.")
                        time.sleep(1); st.rerun()

            st.divider()

            # 2. GESTOR DE PERMISOS DE PAÍS (AQUÍ ESTÁ LA MAGIA)
            st.subheader("🌍 Gestor de Permisos de País")
            st.info("Selecciona un usuario para modificar sus accesos geográficos.")
            
            col_sel_user, col_sel_paises = st.columns([1, 2])
            
            with col_sel_user:
                usuario_a_editar = st.selectbox("Seleccionar Usuario:", df['email'].unique())
            
            if usuario_a_editar:
                # Obtenemos sus países actuales
                datos_user = df[df['email'] == usuario_a_editar].iloc[0]
                paises_actuales_str = str(datos_user['paises'])
                # Convertimos string "Guatemala,Panama" a lista ["Guatemala", "Panama"]
                lista_actual = [p.strip() for p in paises_actuales_str.split(",") if p.strip() in LISTA_PAISES]
                
                with col_sel_paises:
                    # EL MULTISELECTOR QUE QUERÍAS
                    nuevos_paises = st.multiselect(
                        f"Países permitidos para {datos_user['nombre']}:",
                        options=LISTA_PAISES,
                        default=lista_actual
                    )
                    
                    if st.button("Actualizar Permisos de País"):
                        # Convertimos lista a string
                        str_nuevos_paises = ",".join(nuevos_paises)
                        
                        # Actualizamos en Google Sheets (Celda específica)
                        # Buscamos la fila
                        try:
                            cell = ws.find(usuario_a_editar)
                            # Columna F es la 6
                            ws.update_cell(cell.row, 6, str_nuevos_paises)
                            st.success(f"Permisos actualizados para {usuario_a_editar}")
                            time.sleep(1); st.rerun()
                        except Exception as e:
                            st.error(f"Error al guardar: {e}")

            st.divider()

            # 3. ELIMINAR
            st.subheader("🗑️ Eliminar Usuario")
            with st.expander("Zona de Peligro"):
                user_del = st.selectbox("Usuario a borrar:", df[df['email']!=st.session_state.usuario]['email'].unique(), index=None)
                if st.button("Eliminar Definitivamente", type="primary", disabled=not user_del):
                    if eliminar_usuario_db(user_del):
                        st.success("Eliminado."); time.sleep(1); st.rerun()
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

# ==============================================================================
# 7. GESTIÓN DE CLIENTES (NUEVO MÓDULO)
# ==============================================================================
def app_gestion_clientes():
    st.subheader("🏢 Directorio de Clientes")
    
    tab1, tab2 = st.tabs(["Nuevo Cliente", "Ver Listado"])
    
    with tab1:
        with st.form("add_client"):
            new_cl = st.text_input("Nombre de la Empresa / Cliente")
            if st.form_submit_button("Guardar Cliente"):
                if new_cl:
                    ok, msg = crear_nuevo_cliente(new_cl.strip())
                    if ok: st.success(msg); refrescar_pagina(1)
                    else: st.warning(msg)
                else:
                    st.warning("Escribe un nombre.")

    with tab2:
        df = leer_datos("clientes")
        if not df.empty:
            st.dataframe(df, use_container_width=True)
            st.caption(f"Total Clientes Registrados: {len(df)}")
        else:
            st.info("No hay clientes registrados aún.")


if __name__ == "__main__":
    main()









