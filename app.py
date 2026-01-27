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

def obtener_hora_actual():
    """Devuelve la fecha y hora ajustada a la zona horaria seleccionada"""
    zona_seleccionada = st.session_state.get('zona_horaria', 'America/Guatemala')
    try:
        tz = pytz.timezone(zona_seleccionada)
        fecha_ajustada = datetime.now(tz)
        return fecha_ajustada.strftime("%Y-%m-%d %H:%M:%S")
    except:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# ==========================================
# 2. CONEXIÓN Y CACHÉ
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
        if "429" in str(e):
            st.warning("⏳ Esperando a Google (Límite de velocidad)...")
            time.sleep(5)
            st.rerun()
        else:
            st.error(f"Error de conexión: {e}")
            st.stop()

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
    
    # Convertimos a DataFrame
    df = pd.DataFrame(data)
    return df

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

# ==========================================
# 3. LÓGICA DE NEGOCIO
# ==========================================

# --- FUNCIÓN DE LIMPIEZA FINANCIERA ---
def limpiar_moneda(valor):
    """Recibe texto o numero y devuelve float puro"""
    if isinstance(valor, (int, float)):
        return float(valor)
    valor = str(valor)
    # Quitamos simbolos de moneda y comas para poder sumar
    valor = valor.replace("Q", "").replace("$", "").replace(",", "").strip()
    try:
        return float(valor)
    except:
        return 0.0

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
            duplicados += 1
            continue

        n_linea = str(row['numero_linea']).replace(".0", "")
        n_cliente = str(row['cliente'])
        estado = "Activa" if n_linea and n_cliente and n_linea.lower()!='nan' else "Botiquin"
        
        # Limpiamos costos antes de subir para que suban como números puros
        cq = limpiar_moneda(row['costo_q'])
        cd = limpiar_moneda(row['costo_d'])

        fila_sim = [
            iccid_val, n_linea, n_cliente, str(row['placa']), str(row['imei']), 
            str(row['tipo_plan']), str(row['pais']),
            cq, cd, # Subimos float puro
            estado, fecha_hoy
        ]
        
        fila_hist = [iccid_val, "Creacion Masiva", f"Carga Excel. Estado: {estado}", usuario,
