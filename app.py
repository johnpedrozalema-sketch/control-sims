import streamlit as st
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
import time
import hashlib
import io
import json # Nuevo import

# ==========================================
# 1. CONFIGURACIÓN
# ==========================================
st.set_page_config(page_title="Control SIM Cards Nube", layout="wide")

NOMBRE_HOJA = "Base de Datos SIMs" 
SCOPE = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
KEY_FILE = 'credenciales.json'

def make_hashes(password):
    return hashlib.sha256(str.encode(password)).hexdigest()

def check_hashes(password, hashed_text):
    if make_hashes(password) == hashed_text:
        return True
    return False

def refrescar_pagina(segundos=3):
    time.sleep(segundos)
    st.rerun()

# ==========================================
# 2. CONEXIÓN CON GOOGLE SHEETS (HÍBRIDA)
# ==========================================
def conectar_google():
    """Conecta usando Secrets (Nube) o Archivo JSON (Local)"""
    try:
        # 1. Intentamos buscar en los Secretos de Streamlit Cloud
        if "gcp_service_account" in st.secrets:
            # Convertimos el objeto de secretos a un diccionario normal
            creds_dict = dict(st.session_state.secrets["gcp_service_account"]) if "secrets" in st.session_state else st.secrets["gcp_service_account"]
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, SCOPE)
        
        # 2. Si no hay secretos, buscamos el archivo local (Tu PC)
        else:
            creds = ServiceAccountCredentials.from_json_keyfile_name(KEY_FILE, SCOPE)
            
        client = gspread.authorize(creds)
        sheet = client.open(NOMBRE_HOJA)
        return sheet
    except Exception as e:
        st.error(f"Error de conexión: {e}")
        st.stop()

# --- FUNCIONES DE LECTURA Y ESCRITURA ---
def leer_datos(pestaña):
    sheet = conectar_google()
    worksheet = sheet.worksheet(pestaña)
    data = worksheet.get_all_records()
    return pd.DataFrame(data)

def escribir_fila(pestaña, fila_lista):
    sheet = conectar_google()
    worksheet = sheet.worksheet(pestaña)
    worksheet.append_row(fila_lista)
    return True

def actualizar_celda_sim(iccid, columna_nombre, nuevo_valor):
    sheet = conectar_google()
    worksheet = sheet.worksheet("sims")
    try:
        cell = worksheet.find(str(iccid))
        header = worksheet.find(columna_nombre)
        worksheet.update_cell(cell.row, header.col, nuevo_valor)
        return True
    except:
        return False

def actualizar_sim_completa(iccid, datos_dict):
    sheet = conectar_google()
    worksheet = sheet.worksheet("sims")
    try:
        cell = worksheet.find(str(iccid))
        row_num = cell.row
        worksheet.update_cell(row_num, 2, datos_dict['numero_linea'])
        worksheet.update_cell(row_num, 3, datos_dict['cliente'])
        worksheet.update_cell(row_num, 4, datos_dict['placa'])
        worksheet.update_cell(row_num, 5, datos_dict['imei'])
        worksheet.update_cell(row_num, 6, datos_dict['tipo_plan'])
        worksheet.update_cell(row_num, 7, datos_dict['pais'])
        worksheet.update_cell(row_num, 8, datos_dict['costo_q'])
        worksheet.update_cell(row_num, 9, datos_dict['costo_d'])
        worksheet.update_cell(row_num, 10, datos_dict['estado'])
        return True
    except Exception as e:
        st.error(f"Error actualizando: {e}")
        return False

# ==========================================
# 3. LÓGICA DE NEGOCIO
# ==========================================

def login_user(username, password):
    df = leer_datos("usuarios")
    df['username'] = df['username'].astype(str)
    usuario_encontrado = df[df['username'] == username]
    if not usuario_encontrado.empty:
        stored_password = usuario_encontrado.iloc[0]['password']
        rol = usuario_encontrado.iloc[0]['rol']
        if check_hashes(password, stored_password):
            return rol
    return None

def registrar_sim(datos, usuario):
    df = leer_datos("sims")
    df['iccid'] = df['iccid'].astype(str)
    if str(datos['iccid']) in df['iccid'].values:
        return False

    linea = str(datos['numero_linea']) if datos['numero_linea'] and str(datos['numero_linea']).lower() != 'nan' else ""
    cliente = str(datos['cliente']) if datos['cliente'] and str(datos['cliente']).lower() != 'nan' else ""
    estado = "Activa" if linea and cliente else "Botiquin"
    fecha = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    fila = [str(datos['iccid']), linea, cliente, str(datos['placa']), str(datos['imei']),
            str(datos['tipo_plan']), str(datos['pais']), datos['costo_q'], datos['costo_d'],
            estado, fecha]
    escribir_fila("sims", fila)
    escribir_fila("historial", [str(datos['iccid']), "Creacion", f"SIM creada como {estado}", usuario, fecha])
    return True

def procesar_carga_masiva(df, usuario):
    correctos = 0
    errores = 0
    
    # 1. Limpieza agresiva de encabezados
    # Convertimos a texto, minúsculas y quitamos espacios al inicio y final
    df.columns = [str(c).lower().strip() for c in df.columns]
    
    # 2. Verificación DEBUG (Para saber qué está pasando)
    esperadas = ['iccid', 'numero_linea', 'cliente', 'placa', 'imei', 'tipo_plan', 'pais', 'costo_q', 'costo_d']
    
    # Verificamos si falta alguna columna vital (ICCID)
    if 'iccid' not in df.columns:
        # AQUÍ ESTÁ LA MAGIA: Le decimos al usuario qué columnas encontró
        columnas_encontradas = ", ".join(list(df.columns))
        return 0, 0, f"Error: No encuentro la columna 'iccid'. Las columnas que veo en tu archivo son: [{columnas_encontradas}]. Revisa si hay filas vacías al inicio."

    # 3. Limpieza de datos
    df = df.fillna("")
    
    # Forzamos que iccid sea texto para evitar problemas de notación científica
    df['iccid'] = df['iccid'].astype(str).str.replace(".0", "", regex=False)

    for index, row in df.iterrows():
        # Ignorar filas donde el ICCID esté vacío o sea 'nan'
        iccid_val = str(row['iccid']).strip()
        if not iccid_val or iccid_val.lower() == 'nan':
            continue

        # Usamos .get() para evitar errores si falta alguna columna no esencial
        datos = {
            'iccid': iccid_val, 
            'numero_linea': str(row.get('numero_linea', '')).replace(".0", ""), 
            'cliente': str(row.get('cliente', '')),
            'placa': str(row.get('placa', '')), 
            'imei': str(row.get('imei', '')), 
            'tipo_plan': str(row.get('tipo_plan', '')), 
            'pais': str(row.get('pais', '')),
            'costo_q': row.get('costo_q', 0.0) if row.get('costo_q', "") != "" else 0.0,
            'costo_d': row.get('costo_d', 0.0) if row.get('costo_d', "") != "" else 0.0
        }
        
        if registrar_sim(datos, usuario): 
            correctos += 1
        else: 
            errores += 1
            
    return correctos, errores, "Proceso finalizado"

def actualizar_datos_sim(iccid, datos, usuario):
    linea = str(datos['numero_linea'])
    cliente = str(datos['cliente'])
    nuevo_estado = "Activa" if linea and cliente and linea.lower() != 'nan' else "Botiquin"
    datos_completos = datos.copy()
    datos_completos['estado'] = nuevo_estado
    
    if actualizar_sim_completa(iccid, datos_completos):
        fecha = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        escribir_fila("historial", [iccid, "Actualizacion", f"Datos actualizados. Estado: {nuevo_estado}", usuario, fecha])
        return True
    return False

def traslado_sim(iccid_antiguo, iccid_nuevo, usuario):
    df = leer_datos("sims")
    df['iccid'] = df['iccid'].astype(str)
    
    row_old = df[df['iccid'] == str(iccid_antiguo)]
    if row_old.empty: return False, "ICCID antiguo no encontrado"
    
    row_new = df[df['iccid'] == str(iccid_nuevo)]
    if row_new.empty: return False, "ICCID nuevo no encontrado"
    if row_new.iloc[0]['estado'] != 'Botiquin': return False, "La nueva SIM no es Botiquín"
    
    datos_viejos = row_old.iloc[0]
    
    datos_para_nueva = {
        'numero_linea': datos_viejos['numero_linea'], 'cliente': datos_viejos['cliente'],
        'placa': datos_viejos['placa'], 'imei': datos_viejos['imei'], 'tipo_plan': datos_viejos['tipo_plan'],
        'pais': datos_viejos['pais'], 'costo_q': datos_viejos['costo_q'], 'costo_d': datos_viejos['costo_d'],
        'estado': 'Activa'
    }
    actualizar_sim_completa(iccid_nuevo, datos_para_nueva)
    actualizar_celda_sim(iccid_antiguo, "estado", "Retirada")
    actualizar_celda_sim(iccid_antiguo, "numero_linea", "SIM RETIRADA")
    
    fecha = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    escribir_fila("historial", [iccid_nuevo, "Traslado Entrada", f"Recibió de {iccid_antiguo}", usuario, fecha])
    escribir_fila("historial", [iccid_antiguo, "Traslado Salida", f"Movida a {iccid_nuevo}", usuario, fecha])
    return True, "Traslado Exitoso"

def cancelar_servicio(iccid, usuario, motivo):
    if actualizar_celda_sim(iccid, "estado", "Cancelada"):
        fecha = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        escribir_fila("historial", [iccid, "Cancelacion", f"Motivo: {motivo}", usuario, fecha])
        return True
    return False

def crear_usuario(username, password, rol):
    df = leer_datos("usuarios")
    df['username'] = df['username'].astype(str)
    if str(username) in df['username'].values:
        return False
    escribir_fila("usuarios", [username, make_hashes(password), rol])
    return True

# ==========================================
# 4. INTERFAZ GRÁFICA (UI)
# ==========================================
def main():
    if 'usuario' not in st.session_state: st.session_state.usuario = None
    if 'rol' not in st.session_state: st.session_state.rol = None
    if 'form_id' not in st.session_state: st.session_state.form_id = 0

    if st.session_state.usuario is None:
        col1, col2 = st.columns([1,2])
        with col2:
            st.title("☁️ Control SIMs (Google Sheets)")
            user = st.text_input("Usuario")
            password = st.text_input("Contraseña", type="password")
            if st.button("Ingresar"):
                try:
                    rol = login_user(user, password)
                    if rol:
                        st.session_state.usuario = user
                        st.session_state.rol = rol
                        st.rerun()
                    else:
                        st.error("Credenciales incorrectas")
                except Exception as e:
                    st.error(f"Error conexión: {e}")
        return

    st.sidebar.header(f"Hola, {st.session_state.usuario}")
    st.sidebar.info(f"Rol: {st.session_state.rol}")
    
    menu = ["Dashboard", "Reportes", "Salir"]
    if st.session_state.rol == "admin":
        menu = ["Dashboard", "Registrar SIM", "Actualizar Datos", "Traslados", "Cancelar/Gestionar", "Usuarios", "Reportes", "Salir"]
    
    choice = st.sidebar.radio("Ir a:", menu)

    if choice == "Salir":
        st.session_state.usuario = None
        st.rerun()

    if choice == "Dashboard":
        st.title("📊 Tablero en la Nube")
        df = leer_datos("sims")
        if not df.empty:
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Total", len(df))
            c2.metric("Activas", len(df[df['estado']=='Activa']))
            c3.metric("Botiquín", len(df[df['estado']=='Botiquin']))
            c4.metric("Canceladas", len(df[df['estado']=='Cancelada']))
        else: st.warning("Hoja vacía.")

    elif choice == "Registrar SIM":
        st.subheader("➕ Gestión de Inventario")
        tab1, tab2 = st.tabs(["Individual", "Carga Masiva"])
        with tab1:
            key_prefix = str(st.session_state.form_id) 
            with st.form("new_sim"):
                c1, c2 = st.columns(2)
                iccid = c1.text_input("ICCID*", key=f"{key_prefix}_iccid")
                linea = c2.text_input("Línea", key=f"{key_prefix}_linea")
                cliente = c1.text_input("Cliente", key=f"{key_prefix}_cliente")
                placa = c2.text_input("Placa", key=f"{key_prefix}_placa")
                imei = c1.text_input("IMEI", key=f"{key_prefix}_imei")
                plan = c2.text_input("Plan", key=f"{key_prefix}_plan")
                pais = c1.selectbox("País", ["Guatemala", "El Salvador", "Honduras", "Nicaragua", "Costa Rica", "Panamá", "México", "Colombia"], key=f"{key_prefix}_pais")
                costo_q = c2.number_input("Costo Q", min_value=0.0, key=f"{key_prefix}_costoq")
                costo_d = c1.number_input("Costo $", min_value=0.0, key=f"{key_prefix}_costod")
                if st.form_submit_button("Guardar"):
                    if iccid:
                        datos = {'iccid': iccid, 'numero_linea': linea, 'cliente': cliente, 'placa': placa, 'imei': imei, 'tipo_plan': plan, 'pais': pais, 'costo_q': costo_q, 'costo_d': costo_d}
                        with st.spinner("Guardando..."):
                            if registrar_sim(datos, st.session_state.usuario):
                                st.success("✅ Guardado."); st.session_state.form_id += 1; refrescar_pagina(2)
                            else: st.error("ICCID Duplicado.")
                    else: st.warning("Falta ICCID")
        with tab2:
            st.markdown("### Carga Masiva")
            df_template = pd.DataFrame(columns=['iccid', 'numero_linea', 'cliente', 'placa', 'imei', 'tipo_plan', 'pais', 'costo_q', 'costo_d'])
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='openpyxl') as writer: df_template.to_excel(writer, index=False)
            st.download_button("📥 Plantilla", data=buffer.getvalue(), file_name="plantilla.xlsx")
            archivo = st.file_uploader("Suelte archivo", type=["xlsx", "xls"])
            if archivo and st.button("Procesar"):
                try:
                    df_upload = pd.read_excel(archivo)
                    with st.spinner("Analizando archivo..."):
                        c, e, msg = procesar_carga_masiva(df_upload, st.session_state.usuario)
                    
                    if "Error" in msg:
                        st.error(msg)
                    else:
                        st.success(f"✅ Guardados: {c} | Errores/Duplicados: {e}")
                        refrescar_pagina(3)
                except Exception as ex:
                    st.error(f"Error crítico: {ex}")

    elif choice == "Actualizar Datos":
        st.subheader("✏️ Editar")
        df = leer_datos("sims")
        df['iccid'] = df['iccid'].astype(str)
        if not df.empty:
            df['display'] = df['iccid'] + " | " + df['estado'] + " | " + df['cliente'].astype(str)
            seleccion = st.selectbox("Buscar SIM:", df['display'].tolist(), index=None, placeholder="Buscar...")
            if seleccion:
                iccid_sel = seleccion.split(" | ")[0]
                current = df[df['iccid'] == iccid_sel].iloc[0]
                with st.form("edit"):
                    c1, c2 = st.columns(2)
                    n_linea = c1.text_input("Línea", value=current['numero_linea'])
                    n_cliente = c2.text_input("Cliente", value=current['cliente'])
                    n_placa = c1.text_input("Placa", value=current['placa'])
                    n_imei = c2.text_input("IMEI", value=current['imei'])
                    n_plan = c1.text_input("Plan", value=current['tipo_plan'])
                    try: idx_p = ["Guatemala", "El Salvador", "Honduras", "Nicaragua", "Costa Rica", "Panamá", "México", "Colombia"].index(current['pais']) 
                    except: idx_p = 0
                    n_pais = c2.selectbox("País", ["Guatemala", "El Salvador", "Honduras", "Nicaragua", "Costa Rica", "Panamá", "México", "Colombia"], index=idx_p)
                    n_costoq = c1.number_input("Costo Q", value=float(current['costo_q']) if current['costo_q'] else 0.0)
                    n_costod = c2.number_input("Costo $", value=float(current['costo_d']) if current['costo_d'] else 0.0)
                    if st.form_submit_button("Actualizar"):
                        datos = {'numero_linea': n_linea, 'cliente': n_cliente, 'placa': n_placa, 'imei': n_imei, 'tipo_plan': n_plan, 'pais': n_pais, 'costo_q': n_costoq, 'costo_d': n_costod}
                        with st.spinner("Actualizando..."):
                            if actualizar_datos_sim(iccid_sel, datos, st.session_state.usuario):
                                st.success("Actualizado"); refrescar_pagina(2)

    elif choice == "Traslados":
        st.subheader("🔄 Traslados")
        df = leer_datos("sims")
        if not df.empty:
            df['iccid'] = df['iccid'].astype(str)
            df_origen = df[~df['estado'].isin(['Retirada', 'Cancelada'])]
            df_destino = df[df['estado'] == 'Botiquin']
            df_origen['display'] = df_origen['iccid'] + " (" + df_origen['numero_linea'].astype(str) + ")"
            c1, c2 = st.columns(2)
            sel_origen = c1.selectbox("Origen", df_origen['display'].tolist(), index=None, placeholder="Buscar...")
            sel_destino = c2.selectbox("Destino", df_destino['iccid'].tolist(), index=None, placeholder="Buscar...")
            if sel_origen and sel_destino:
                if st.button("Trasladar"):
                    with st.spinner("Procesando..."):
                        ok, msg = traslado_sim(sel_origen.split(" (")[0], sel_destino, st.session_state.usuario)
                        if ok: st.balloons(); st.success(msg); refrescar_pagina(3)
                        else: st.error(msg)

    elif choice == "Cancelar/Gestionar":
        st.subheader("⚠️ Cancelar")
        df = leer_datos("sims")
        if not df.empty:
            df['iccid'] = df['iccid'].astype(str)
            df_can = df[df['estado'] != 'Cancelada']
            df_can['display'] = df_can['iccid'] + " | " + df_can['cliente'].astype(str)
            sel = st.selectbox("Buscar:", df_can['display'].tolist(), index=None, placeholder="Buscar...")
            if sel:
                motivo = st.text_input("Motivo")
                if st.button("Confirmar Cancelación"):
                    with st.spinner("Cancelando..."):
                        if cancelar_servicio(sel.split(" | ")[0], st.session_state.usuario, motivo):
                            st.success("Cancelada"); refrescar_pagina(2)

    elif choice == "Usuarios":
        st.subheader("👤 Usuarios")
        if 'user_key' not in st.session_state: st.session_state.user_key = 0
        uk = str(st.session_state.user_key)
        with st.form("crear_user"):
            c1, c2, c3 = st.columns(3)
            u_user = c1.text_input("Usuario", key=f"u_{uk}")
            u_pass = c2.text_input("Password", type="password", key=f"p_{uk}")
            u_rol = c3.selectbox("Rol", ["admin", "general"], key=f"r_{uk}")
            if st.form_submit_button("Crear"):
                if crear_usuario(u_user, u_pass, u_rol):
                    st.success("Creado"); st.session_state.user_key += 1; refrescar_pagina(2)
                else: st.error("Error: Usuario existente")
        st.table(leer_datos("usuarios")[['username', 'rol']])

    elif choice == "Reportes":
        st.subheader("📑 Reportes")
        df = leer_datos("sims")
        if not df.empty:
            st.sidebar.markdown("---")
            st.sidebar.header("Filtros")
            try:
                p = st.sidebar.multiselect("País", df['pais'].unique())
                if p: df = df[df['pais'].isin(p)]
            except: pass
            st.dataframe(df)
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='openpyxl') as writer: df.to_excel(writer, index=False)
            st.download_button("Descargar Excel", buffer.getvalue(), "reporte.xlsx")
            st.markdown("---")
            if st.checkbox("Ver Historial"): st.dataframe(leer_datos("historial"))

if __name__ == "__main__":

    main()

