import streamlit as st
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
import time
import hashlib
import io
import json

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
# 2. CONEXIÓN OPTIMIZADA
# ==========================================
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
        # Si da error de cuota, esperamos un poco y reintentamos (Backoff)
        if "429" in str(e):
            st.warning("⏳ Tráfico alto con Google. Esperando 5 segundos...")
            time.sleep(5)
            st.rerun()
        else:
            st.error(f"Error de conexión: {e}")
            st.stop()

# --- AQUÍ ESTÁ EL TRUCO (CACHÉ) ---
# ttl=10 significa: "Recuerda esto por 10 segundos antes de volver a preguntar a Google"
@st.cache_data(ttl=10)
def leer_datos(pestaña):
    sheet = conectar_google()
    worksheet = sheet.worksheet(pestaña)
    data = worksheet.get_all_records()
    
    if not data:
        if pestaña == "sims":
            return pd.DataFrame(columns=['iccid', 'numero_linea', 'cliente', 'placa', 'imei', 'tipo_plan', 'pais', 'costo_q', 'costo_d', 'estado', 'fecha_registro'])
        elif pestaña == "usuarios":
             return pd.DataFrame(columns=['username', 'password', 'rol'])
    
    return pd.DataFrame(data)

def limpiar_cache():
    """Borra la memoria temporal para obligar a leer datos frescos"""
    st.cache_data.clear()

def escribir_fila(pestaña, fila_lista):
    sheet = conectar_google()
    worksheet = sheet.worksheet(pestaña)
    worksheet.append_row(fila_lista)
    limpiar_cache() # Importante: Al escribir, borramos la memoria vieja
    return True

def actualizar_sim_completa(iccid, datos_dict):
    sheet = conectar_google()
    worksheet = sheet.worksheet("sims")
    try:
        cell = worksheet.find(str(iccid))
        row_num = cell.row
        # Actualización por lote (batch) para ahorrar peticiones
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

# ==========================================
# 3. LÓGICA DE NEGOCIO
# ==========================================

def registrar_sim(datos, usuario, df_cache=None):
    # Si nos pasan el DF, usamos ese (ahorra lecturas en carga masiva)
    if df_cache is None:
        df = leer_datos("sims")
    else:
        df = df_cache

    # Validación segura
    if 'iccid' in df.columns:
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

def procesar_carga_masiva_final(df_limpio, usuario):
    correctos = 0
    errores = 0
    df_limpio = df_limpio.fillna("")
    df_limpio['iccid'] = df_limpio['iccid'].astype(str).str.replace(".0", "", regex=False)

    # LEEMOS LA BASE DE DATOS UNA SOLA VEZ AL PRINCIPIO
    df_db = leer_datos("sims")

    for index, row in df_limpio.iterrows():
        iccid_val = str(row['iccid']).strip()
        if not iccid_val or iccid_val.lower() == 'nan': continue

        datos = {
            'iccid': iccid_val, 
            'numero_linea': str(row['numero_linea']).replace(".0", ""), 
            'cliente': str(row['cliente']),
            'placa': str(row['placa']), 
            'imei': str(row['imei']), 
            'tipo_plan': str(row['tipo_plan']), 
            'pais': str(row['pais']),
            'costo_q': row['costo_q'] if row['costo_q'] != "" else 0.0,
            'costo_d': row['costo_d'] if row['costo_d'] != "" else 0.0
        }
        
        # Pasamos df_db para no leer de Google en cada vuelta
        if registrar_sim(datos, usuario, df_cache=df_db): 
            correctos += 1
        else: 
            errores += 1
            
    return correctos, errores

def login_user(username, password):
    df = leer_datos("usuarios")
    if 'username' not in df.columns: return None
    
    df['username'] = df['username'].astype(str)
    user_row = df[df['username'] == username]
    if not user_row.empty:
        if check_hashes(password, user_row.iloc[0]['password']):
            return user_row.iloc[0]['rol']
    return None

def actualizar_datos_sim(iccid, datos, usuario):
    linea = str(datos['numero_linea'])
    cliente = str(datos['cliente'])
    nuevo_estado = "Activa" if linea and cliente else "Botiquin"
    datos_full = datos.copy()
    datos_full['estado'] = nuevo_estado
    if actualizar_sim_completa(iccid, datos_full):
        fecha = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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
    fecha = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    escribir_fila("historial", [iccid_nuevo, "Traslado Entrada", f"De {iccid_antiguo}", usuario, fecha])
    escribir_fila("historial", [iccid_antiguo, "Traslado Salida", f"A {iccid_nuevo}", usuario, fecha])
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
    if str(username) in df['username'].values: return False
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
            st.title("☁️ Control SIMs")
            user = st.text_input("Usuario")
            password = st.text_input("Contraseña", type="password")
            if st.button("Ingresar"):
                try:
                    rol = login_user(user, password)
                    if rol:
                        st.session_state.usuario = user
                        st.session_state.rol = rol
                        st.rerun()
                    else: st.error("Incorrecto")
                except Exception as e: st.error(f"Error Conexión: {e}")
        return

    st.sidebar.title(f"👤 {st.session_state.usuario}")
    menu = ["Dashboard", "Reportes", "Salir"]
    if st.session_state.rol == "admin":
        menu = ["Dashboard", "Registrar SIM", "Actualizar Datos", "Traslados", "Cancelar/Gestionar", "Usuarios", "Reportes", "Salir"]
    choice = st.sidebar.radio("Menú", menu)

    if choice == "Salir":
        st.session_state.usuario = None
        st.rerun()

    # --- PANTALLAS ---
    if choice == "Dashboard":
        st.title("📊 Tablero")
        df = leer_datos("sims")
        if not df.empty and 'estado' in df.columns:
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Total", len(df))
            c2.metric("Activas", len(df[df['estado']=='Activa']))
            c3.metric("Botiquín", len(df[df['estado']=='Botiquin']))
            c4.metric("Canceladas", len(df[df['estado']=='Cancelada']))

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
                df_check = None
                try:
                    df_check = pd.read_excel(archivo)
                    st.success("✅ Archivo leído.")
                except: st.error("Error leyendo archivo")
                
                if df_check is not None:
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

                    if st.button("Procesar Datos"):
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
                        
                        with st.spinner("Subiendo... (Esto puede tardar unos segundos)"):
                            try:
                                c, e = procesar_carga_masiva_final(df_final, st.session_state.usuario)
                                st.success(f"✅ Finalizado: {c} guardados | {e} duplicados")
                                refrescar_pagina(5)
                            except Exception as db_err:
                                st.error(f"Error: {db_err}")

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
                    ncq = c1.number_input("Costo Q", value=float(cur['costo_q']) if cur['costo_q'] else 0.0)
                    ncd = c2.number_input("Costo $", value=float(cur['costo_d']) if cur['costo_d'] else 0.0)
                    if st.form_submit_button("Actualizar"):
                        d = {'numero_linea': nl, 'cliente': nc, 'placa': np, 'imei': ni, 'tipo_plan': npl, 'pais': npa, 'costo_q': ncq, 'costo_d': ncd}
                        with st.spinner("Actualizando..."):
                            if actualizar_datos_sim(ic, d, st.session_state.usuario):
                                st.success("Listo"); refrescar_pagina(2)

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

    elif choice == "Usuarios":
        st.subheader("👤 Usuarios")
        if 'uk' not in st.session_state: st.session_state.uk=0
        k = str(st.session_state.uk)
        with st.form("us"):
            c1,c2,c3 = st.columns(3)
            nu = c1.text_input("User", key=f"u{k}")
            np = c2.text_input("Pass", type="password", key=f"p{k}")
            nr = c3.selectbox("Rol", ["admin", "general"], key=f"r{k}")
            if st.form_submit_button("Crear"):
                if crear_usuario(nu, np, nr):
                    st.success("Creado"); st.session_state.uk+=1; refrescar_pagina(2)
                else: st.error("Error")
        st.table(leer_datos("usuarios")[['username','rol']])

    elif choice == "Reportes":
        st.subheader("📑 Reportes")
        df = leer_datos("sims")
        if not df.empty:
            try:
                p = st.sidebar.multiselect("País", df['pais'].unique())
                if p: df = df[df['pais'].isin(p)]
            except: pass
            st.dataframe(df)
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='openpyxl') as writer: df.to_excel(writer, index=False)
            st.download_button("Excel", buffer.getvalue(), "reporte.xlsx")

if __name__ == "__main__":
    main()
