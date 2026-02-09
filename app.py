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

NOMBRE_HOJA = "Base de Datos SIMs"
SCOPE = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
KEY_FILE = 'credenciales.json'
LISTA_PAISES = ["Guatemala", "El Salvador", "Honduras", "Nicaragua", "Costa Rica", "Panamá", "México", "Colombia"]

# ==============================================================================
# 2. FUNCIONES DE UTILIDAD
# ==============================================================================

def make_hashes(password):
    return hashlib.sha256(str.encode(password)).hexdigest()

def check_hashes(password, hashed_text):
    if make_hashes(password) == hashed_text: return True
    return False

def obtener_hora_actual():
    try:
        tz = pytz.timezone(st.session_state.get('zona_horaria', 'America/Guatemala'))
        return datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
    except: return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def refrescar_pagina(segundos=3):
    time.sleep(segundos)
    st.rerun()

def conectar_google():
    try:
        if "gcp_service_account" in st.secrets:
            creds_dict = dict(st.secrets["gcp_service_account"])
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, SCOPE)
        else:
            creds = ServiceAccountCredentials.from_json_keyfile_name(KEY_FILE, SCOPE)
        client = gspread.authorize(creds)
        return client.open(NOMBRE_HOJA)
    except Exception as e:
        st.error(f"Error conexión Google: {e}"); st.stop()

def limpiar_moneda(valor):
    if pd.isna(valor) or str(valor).strip() == "": return 0.0
    if isinstance(valor, (int, float)): return float(valor)
    valor = str(valor).strip().upper().replace("Q", "").replace("$", "").replace(" ", "")
    if "." in valor and "," in valor: valor = valor.replace(".", "").replace(",", ".")
    elif "," in valor: valor = valor.replace(",", ".")
    try: return float(valor)
    except: return 0.0

def normalizar_pais_inteligente(texto_entrada):
    if pd.isna(texto_entrada) or str(texto_entrada).strip() == "": return "" 
    t = str(texto_entrada).strip().lower()
    mapa = {
        "guatemala": "Guatemala", "guate": "Guatemala", "gt": "Guatemala",
        "el salvador": "El Salvador", "sv": "El Salvador",
        "honduras": "Honduras", "hn": "Honduras",
        "nicaragua": "Nicaragua", "ni": "Nicaragua",
        "costa rica": "Costa Rica", "cr": "Costa Rica",
        "panama": "Panamá", "panamá": "Panamá", "pa": "Panamá",
        "mexico": "México", "méxico": "México", "mx": "México",
        "colombia": "Colombia", "co": "Colombia"
    }
    if t in mapa: return mapa[t]
    if t.title() in LISTA_PAISES: return t.title()
    return "PENDIENTE ⚠️"

# --- EMAILS ---
def enviar_correo_sistema(dest, asunto, html):
    try:
        if "email" in st.secrets:
            EMAIL_EMISOR = st.secrets["email"]["address"]
            EMAIL_PASS = st.secrets["email"]["password"]
            msg = MIMEMultipart(); msg['From']=EMAIL_EMISOR; msg['To']=dest; msg['Subject']=asunto
            msg.attach(MIMEText(html, 'html'))
            s = smtplib.SMTP('smtp.gmail.com', 587); s.starttls(); s.login(EMAIL_EMISOR, EMAIL_PASS)
            s.send_message(msg); s.quit(); return True
        return False
    except: return False

def enviar_link_activacion(dest, token, nom):
    base = st.secrets['email'].get('base_url','http://localhost:8501') if "email" in st.secrets else 'http://localhost:8501'
    link = f"{base}/?token_reset={token}"
    return enviar_correo_sistema(dest, "Activa tu cuenta", f"Hola {nom}, activa aquí: {link}")

def enviar_link_recuperacion(dest, token, nom):
    base = st.secrets['email'].get('base_url','http://localhost:8501') if "email" in st.secrets else 'http://localhost:8501'
    link = f"{base}/?token_reset={token}"
    return enviar_correo_sistema(dest, "Recuperar Clave", f"Hola {nom}, recupera aquí: {link}")

def gestionar_reset_password():
    tk = st.query_params.get("token_reset", None)
    if tk:
        ws = conectar_google().worksheet("usuarios"); df = pd.DataFrame(ws.get_all_records()); df['token']=df['token'].astype(str)
        u = df[df['token']==tk]
        if not u.empty:
            with st.form("rst"):
                p1=st.text_input("Nueva Clave", type="password"); p2=st.text_input("Confirmar", type="password")
                if st.form_submit_button("Guardar"):
                    if p1==p2 and len(p1)>4:
                        c=ws.find(tk); ws.update_cell(c.row,2,make_hashes(p1)); ws.update_cell(c.row,5,"")
                        st.success("Listo"); st.query_params.clear(); time.sleep(2); st.rerun()
                    else: st.error("Error claves")
        else: st.error("Token vencido")
        return True
    return False

# ==============================================================================
# 3. LÓGICA DE NEGOCIO
# ==============================================================================

@st.cache_data(ttl=10)
def leer_datos(pestaña):
    try:
        ws = conectar_google().worksheet(pestaña)
        raw = ws.get_all_values()
        if not raw or len(raw)<2:
            cols = ['iccid','numero_linea','cliente','placa','imei','tipo_plan','pais','costo_q','costo_d','estado','fecha_registro'] if pestaña=="sims" else ['nombre']
            return pd.DataFrame(columns=cols)

        headers = [str(h).strip().lower().replace(" ", "_") for h in raw[0]]
        df = pd.DataFrame(raw[1:], columns=headers)

        rename_map = {
            'icc_id': 'iccid', 'sim': 'iccid',
            'linea': 'numero_linea', 'número_línea': 'numero_linea', 'nro_linea': 'numero_linea',
            'plan': 'tipo_plan', 'tipo': 'tipo_plan',
            'país': 'pais', 'country': 'pais'
        }
        df = df.rename(columns=rename_map)
        df = df.astype(str).apply(lambda x: x.str.strip())

        if 'iccid' in df.columns:
            df['iccid'] = df['iccid'].str.replace(r'\.0$', '', regex=True)

        if 'pais' in df.columns:
            df['pais'] = df['pais'].str.title()

        if 'costo_q' in df.columns: df['costo_q'] = df['costo_q'].apply(limpiar_moneda)
        if 'costo_d' in df.columns: df['costo_d'] = df['costo_d'].apply(limpiar_moneda)

        if 'estado' in df.columns and 'numero_linea' in df.columns and 'cliente' in df.columns:
            mask_activa = (df['numero_linea'] != "") & (df['cliente'] != "") & (df['estado'] == "")
            df.loc[mask_activa, 'estado'] = "Activa"
            mask_botiquin = (df['numero_linea'] == "") & (df['estado'] == "")
            df.loc[mask_botiquin, 'estado'] = "Botiquin"

        return df
    except Exception as e:
        print(f"Error: {e}"); return pd.DataFrame()

def limpiar_cache(): st.cache_data.clear()
def escribir_fila(p, f): conectar_google().worksheet(p).append_row(f); limpiar_cache()
def escribir_lote(p, f): conectar_google().worksheet(p).append_rows(f); limpiar_cache()

def obtener_lista_clientes():
    df = leer_datos("clientes")
    return sorted(df['nombre'].unique().tolist()) if not df.empty and 'nombre' in df.columns else []

def crear_nuevo_cliente(n):
    df = leer_datos("clientes")
    if not df.empty and n in df['nombre'].values: return False, "Existe"
    escribir_fila("clientes", [n]); return True, "Creado"

def actualizar_sim_completa(iccid, d):
    ws = conectar_google().worksheet("sims")
    try:
        c = ws.find(str(iccid)); r = c.row
        vals = [{'range': f'B{r}:J{r}', 'values': [[d['numero_linea'], d['cliente'], d['placa'], d['imei'], d['tipo_plan'], d['pais'], d['costo_q'], d['costo_d'], d['estado']]]}]
        ws.batch_update(vals); limpiar_cache(); return True
    except: return False

# --- FUNCIÓN DE ACTUALIZACIÓN INTELIGENTE ---
def procesar_actualizacion_masiva(df_up, user):
    df_db = leer_datos("sims")
    if df_db.empty: return 0, "Base de datos vacía"
    
    df_up.columns = [c.strip().lower().replace(" ", "_").replace("á","a").replace("í","i") for c in df_up.columns]
    
    mapa_flexible = {
        'icc_id': 'iccid', 'sim': 'iccid',
        'linea': 'numero_linea', 'numero': 'numero_linea', 'telefono': 'numero_linea',
        'cliente': 'cliente', 'nombre_cliente': 'cliente', 'empresa': 'cliente',
        'placa': 'placa', 'vehiculo': 'placa',
        'imei': 'imei', 'gps': 'imei',
        'plan': 'tipo_plan', 'tipo': 'tipo_plan', 'datos': 'tipo_plan',
        'pais': 'pais', 'country': 'pais', 'ubicacion': 'pais',
        'costo_q': 'costo_q', 'q': 'costo_q',
        'costo_d': 'costo_d', 'd': 'costo_d', 'usd': 'costo_d'
    }
    df_up = df_up.rename(columns=mapa_flexible)
    ws = conectar_google().worksheet("sims")
    ic_map = {str(ic): i+2 for i, ic in enumerate(df_db['iccid'])}
    cols_db = {'numero_linea':2,'cliente':3,'placa':4,'imei':5,'tipo_plan':6,'pais':7,'costo_q':8,'costo_d':9}
    
    ops = []; log = []; hoy = obtener_hora_actual(); count = 0
    columnas_encontradas = [c for c in df_up.columns if c in cols_db]
    
    if not columnas_encontradas: return 0, f"Sin columnas válidas. (Detectadas: {list(df_up.columns)})"

    for _, row in df_up.iterrows():
        ic = str(row.get('iccid','')).strip().replace(".0","")
        if ic in ic_map:
            r = ic_map[ic]; chg = []
            for k in columnas_encontradas:
                raw_val = row[k]
                if pd.isna(raw_val) or str(raw_val).strip() == "": continue
                
                nv = str(raw_val).strip()
                if k == 'pais': nv = normalizar_pais_inteligente(nv)
                if k == 'pais' and nv == "": continue

                cidx = cols_db[k]
                ops.append({'range': gspread.utils.rowcol_to_a1(r, cidx), 'values': [[nv]]})
                chg.append(k)
            if chg:
                count += 1
                log.append([ic, "Masiva Smart", f"Upd: {','.join(chg)}", user, hoy])
    
    if ops:
        try:
            ws.batch_update(ops); escribir_lote("historial", log)
            return count, f"Actualizadas {count} SIMs exitosamente."
        except Exception as e: return 0, f"Error Google: {str(e)}"
    return 0, "Sin cambios (Excel vacío o datos repetidos)."

def registrar_sim(d, user):
    df = leer_datos("sims")
    if 'iccid' in df.columns and str(d['iccid']) in df['iccid'].values: return False
    pc = normalizar_pais_inteligente(d['pais'])
    e = "Activa" if d['numero_linea'] and d['cliente'] else "Botiquin"
    row = [str(d['iccid']), d['numero_linea'], d['cliente'], d['placa'], d['imei'], d['tipo_plan'], pc, limpiar_moneda(d['costo_q']), limpiar_moneda(d['costo_d']), e, obtener_hora_actual()]
    escribir_fila("sims", row); escribir_fila("historial", [d['iccid'], "Creacion", f"Est: {e}", user, row[-1]]); return True

def procesar_carga_masiva_turbo(df, user):
    df = df.fillna(""); df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
    db = leer_datos("sims"); ex = set(db['iccid'].tolist()) if not db.empty else set()
    new = []; log = []; h = obtener_hora_actual(); c=0; d=0
    for _, r in df.iterrows():
        ic = str(r.get('iccid','')).strip().replace(".0","")
        if not ic or ic in ex: d+=1; continue
        l = str(r.get('numero_linea','')).replace(".0",""); cli = str(r.get('cliente',''))
        est = "Activa" if l and cli else "Botiquin"
        pc = normalizar_pais_inteligente(r.get('pais',''))
        new.append([ic, l, cli, r.get('placa',''), r.get('imei',''), r.get('tipo_plan',''), pc, limpiar_moneda(r.get('costo_q','')), limpiar_moneda(r.get('costo_d','')), est, h])
        log.append([ic, "Carga Masiva", est, user, h]); ex.add(ic); c+=1
    if new: escribir_lote("sims", new); escribir_lote("historial", log)
    return c, d

def eliminar_usuario_db(email):
    try:
        ws = conectar_google().worksheet("usuarios"); c = ws.find(email); ws.delete_rows(c.row); return True
    except: return False

# ==============================================================================
# 4. INTERFAZ
# ==============================================================================

# --- MÓDULO DE GESTIÓN DE CLIENTES (RESTAURADO) ---
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

# --- MÓDULO DE GESTIÓN DE USUARIOS (EXISTENTE) ---
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
                new_paises = st.multiselect("🌍 Países Asignados", LISTA_PAISES, default=["Guatemala"])
            if st.form_submit_button("✨ Crear Usuario", type="primary"):
                ws = conectar_google().worksheet("usuarios")
                if new_email and new_nombre and new_paises:
                    if new_email in ws.col_values(1): st.error("El usuario ya existe.")
                    else:
                        token = str(uuid.uuid4())
                        ws.append_row([new_email, "PENDIENTE", new_rol, new_nombre, token, ",".join(new_paises)])
                        if enviar_link_activacion(new_email, token, new_nombre): st.success("✅ Usuario creado y correo enviado.")
                        else: st.warning("Usuario creado, pero hubo error enviando el correo.")
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
    p_user = st.session_state.get('paises_asignados', [])
    if not p_user and st.session_state.rol != 'admin': p_user = ["SIN_ACCESO"]

    st.sidebar.markdown("### 📱 Menú SIMs")
    ops = ["Dashboard", "🔍 Consulta SIM", "Reportes"]
    if st.session_state.rol == "admin":
        ops = ["Dashboard", "🔍 Consulta SIM", "Registrar SIM", "Actualizar Datos", "Gestión Clientes", "Traslados", "Cancelar/Gestionar", "Auditoría", "Reportes"]
    choice = st.sidebar.radio("Opciones:", ops)
    
    df_raw = leer_datos("sims")
    if 'pais' in df_raw.columns:
        df_raw['pais'] = df_raw['pais'].replace("", "PENDIENTE ⚠️")
        df_raw.loc[~df_raw['pais'].isin(LISTA_PAISES + ["PENDIENTE ⚠️"]), 'pais'] = "PENDIENTE ⚠️"

    if st.session_state.rol == 'admin': df = df_raw
    elif p_user: df = df_raw[ (df_raw['pais'].isin(p_user)) | (df_raw['pais'] == "PENDIENTE ⚠️") ]
    else: df = pd.DataFrame(columns=df_raw.columns)

    if choice == "Dashboard":
        st.title("📊 Tablero de Control")
        if st.session_state.rol == 'admin' and not df.empty:
            sin_pais = len(df[df['pais']=="PENDIENTE ⚠️"])
            if sin_pais > 0:
                st.warning(f"⚠️ Atención: Hay {sin_pais} SIMs con país 'PENDIENTE' o desconocido.")

        st.markdown("### Resumen General")
        tot = len(df)
        act = len(df[df['estado']=='Activa']) if 'estado' in df.columns else 0
        bot = len(df[df['estado']=='Botiquin']) if 'estado' in df.columns else 0
        baj = len(df[df['estado']=='Cancelada']) if 'estado' in df.columns else 0
        
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Inventario", tot, border=True)
        c2.metric("Activas", act, border=True)
        c3.metric("Botiquín", bot, border=True)
        c4.metric("Bajas", baj, border=True)
        st.markdown("---")

        if tot > 0:
            t1, t2 = st.tabs(["🌍 Global", "🏢 CRM"])
            with t1:
                if 'pais' in df.columns:
                    cp = df['pais'].value_counts().reset_index(); cp.columns=["País", "Cant"]
                    st.bar_chart(cp.set_index("País"))
            with t2:
                clientes_db = obtener_lista_clientes()
                clis = clientes_db + df['cliente'].unique().tolist()
                c = st.selectbox("Cliente:", sorted(list(set(clis))), index=None)
                if c: st.dataframe(df[df['cliente']==c], use_container_width=True)

    elif choice == "🔍 Consulta SIM":
        st.subheader("🔍 Buscador")
        if not df.empty:
            s = st.text_input("Buscar ICCID, Cliente, Línea o Placa:")
            if s:
                m = df.astype(str).apply(lambda x: x.str.contains(s, case=False)).any(axis=1)
                res = df[m]
                if not res.empty: st.success(f"Encontrados: {len(res)}"); st.dataframe(res)
                else: st.warning("No encontrado")

    elif choice == "Registrar SIM":
        st.subheader("➕ Registro")
        t1, t2 = st.tabs(["Manual", "Masiva"])
        with t1:
            with st.form("reg"):
                c1,c2 = st.columns(2)
                i=c1.text_input("ICCID"); l=c2.text_input("Línea"); cl=c1.selectbox("Cliente", obtener_lista_clientes()); p=c2.selectbox("País", LISTA_PAISES)
                pl=c1.text_input("Placa"); im=c2.text_input("IMEI"); pn=c1.text_input("Plan"); cq=c2.number_input("Costo Q"); cd=c1.number_input("Costo $")
                if st.form_submit_button("Guardar") and i:
                    if registrar_sim({'iccid':i,'numero_linea':l,'cliente':cl,'pais':p,'placa':pl,'imei':im,'tipo_plan':pn,'costo_q':cq,'costo_d':cd}, st.session_state.usuario):
                        st.success("Guardado"); refrescar_pagina(2)
                    else: st.error("Duplicado")
        
        with t2:
            st.info("Sube Excel. Columnas: iccid, linea, cliente, pais...")
            upl = st.file_uploader("Archivo Excel", type=["xlsx"], key="up_reg_final_v12_1")
            if upl and st.button("Procesar Carga"):
                c, d = procesar_carga_masiva_turbo(pd.read_excel(upl), st.session_state.usuario)
                st.success(f"Cargados: {c} | Duplicados: {d}")

    elif choice == "Actualizar Datos":
        st.subheader("✏️ Actualizar")
        t1, t2 = st.tabs(["Manual", "Masiva"])
        with t1:
            ic = st.text_input("ICCID a editar:")
            if ic and not df[df['iccid']==ic].empty:
                curr = df[df['iccid']==ic].iloc[0]
                with st.form("upd"):
                    c1,c2 = st.columns(2)
                    nl=c1.text_input("Línea", curr['numero_linea']); nc=c2.text_input("Cliente", curr['cliente'])
                    np=c1.text_input("Placa", curr['placa']); ni=c2.text_input("IMEI", curr['imei'])
                    npl=c1.text_input("Plan", curr['tipo_plan'])
                    
                    p_val = curr['pais'] if curr['pais'] in LISTA_PAISES else LISTA_PAISES[0]
                    p_idx = LISTA_PAISES.index(p_val)
                    npa=c2.selectbox("País", LISTA_PAISES, index=p_idx)
                    
                    ncq=c1.number_input("Q", value=float(curr['costo_q'])); ncd=c2.number_input("$", value=float(curr['costo_d']))
                    if st.form_submit_button("Actualizar"):
                        actualizar_sim_completa(ic, {'numero_linea':nl,'cliente':nc,'placa':np,'imei':ni,'tipo_plan':npl,'pais':npa,'costo_q':ncq,'costo_d':ncd,'estado':'Activa' if nl and nc else 'Botiquin'})
                        st.success("Hecho"); refrescar_pagina(2)
        
        with t2:
            st.markdown("### 📥 Actualización Inteligente")
            st.info("Solo se actualizarán las celdas que contengan datos. Las celdas vacías en el Excel se ignorarán.")
            upl_upd = st.file_uploader("Archivo Excel (Update)", type=["xlsx"], key="upl_upd_smart_v12_1")
            
            if upl_upd is not None:
                st.success("✅ Archivo recibido.")
                if st.button("🚀 Ejecutar Actualización Inteligente"):
                    c, m = procesar_actualizacion_masiva(pd.read_excel(upl_upd), st.session_state.usuario)
                    if c > 0:
                        st.balloons()
                        st.success(m)
                    else:
                        st.warning(m)

    elif choice == "Gestión Clientes": app_gestion_clientes()
    
    elif choice == "Reportes": st.dataframe(df)
    elif choice == "Traslados": 
        st.subheader("Traslados"); 
        if not df.empty:
            o=st.selectbox("Origen", df[df['estado']=='Activa']['iccid']); d=st.selectbox("Destino", df[df['estado']=='Botiquin']['iccid'])
            if st.button("Trasladar") and o and d: traslado_sim(o,d,st.session_state.usuario); st.success("Ok"); refrescar_pagina(2)
    elif choice == "Cancelar/Gestionar":
        st.subheader("Baja"); 
        if not df.empty:
            s=st.selectbox("SIM", df['iccid']); m=st.text_input("Motivo")
            if st.button("Baja") and s: cancelar_servicio(s,st.session_state.usuario,m); st.success("Ok"); refrescar_pagina(2)
    elif choice == "Auditoría":
        st.subheader("Auditoría"); h=leer_datos("historial"); st.dataframe(h)

# ==============================================================================
# 5. MAIN
# ==============================================================================
def main():
    if gestionar_reset_password(): return
    if 'usuario' not in st.session_state: st.session_state.usuario = None

    if not st.session_state.usuario:
        c1,c2,c3=st.columns([1,2,1])
        with c2:
            st.title("Login"); u=st.text_input("User"); p=st.text_input("Pass", type="password")
            if st.button("Entrar"):
                ws=conectar_google().worksheet("usuarios"); df=pd.DataFrame(ws.get_all_records()); df['email']=df['email'].astype(str)
                usr=df[df['email']==u]
                if not usr.empty and check_hashes(p, str(usr.iloc[0]['password'])):
                    st.session_state.usuario=u; st.session_state.rol=usr.iloc[0]['rol']; st.session_state.nombre=usr.iloc[0]['nombre']
                    try: st.session_state.paises_asignados=[x.strip() for x in str(usr.iloc[0]['paises']).split(",") if x.strip()]
                    except: st.session_state.paises_asignados=[]
                    st.rerun()
                else: st.error("Error")
        return

    st.sidebar.title(f"👤 {st.session_state.nombre}"); st.sidebar.caption(f"Rol: {st.session_state.rol}")
    op = st.sidebar.selectbox("Módulo", ["Control SIM", "Usuarios"] if st.session_state.rol=='admin' else ["Control SIM"])
    
    if op == "Control SIM": app_control_sim()
    elif op == "Usuarios": app_gestion_usuarios()

    if st.sidebar.button("Salir"): st.session_state.usuario=None; st.rerun()

if __name__ == "__main__":
    main()
