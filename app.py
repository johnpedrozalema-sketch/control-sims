import streamlit as st
import pandas as pd
import uuid  # Para generar códigos únicos de invitación

# ==============================================================================
# CONFIGURACIÓN DE PÁGINA
# ==============================================================================
st.set_page_config(
    page_title="Gestor de Eventos V17.0",
    page_icon="🎉",
    layout="wide"
)

# ==============================================================================
# 1. INICIALIZACIÓN ROBUSTA (ELIMINA EL RIESGO DE KEYERROR)
# ==============================================================================
def inicializar_estado():
    # Inicializamos el DataFrame de invitados si no existe
    if 'df_invitados' not in st.session_state:
        # Datos iniciales de prueba
        datos_iniciales = {
            'ID': [str(uuid.uuid4())[:8], str(uuid.uuid4())[:8]],
            'Nombre': ['Juan Pérez', 'Ana Gómez'],
            'Email': ['juan@ejemplo.com', 'ana@ejemplo.com'],
            'Familia': [2, 1], # Número de personas
            'Estado': ['Pendiente', 'Confirmado'],
            'Mesa': [None, 5]
        }
        st.session_state['df_invitados'] = pd.DataFrame(datos_iniciales)
    
    # Inicializamos variables de interfaz
    if 'pagina_actual' not in st.session_state:
        st.session_state['pagina_actual'] = 'Dashboard'

inicializar_estado()

# Función auxiliar para guardar cambios (simulada)
def guardar_cambios(nuevo_df):
    st.session_state['df_invitados'] = nuevo_df
    st.toast('Datos actualizados correctamente', icon='✅')

# ==============================================================================
# BARRA LATERAL (SIDEBAR)
# ==============================================================================
with st.sidebar:
    st.title("📂 Menú V17.0")
    st.markdown("---")
    
    opcion = st.radio(
        "Navegación", 
        ["Dashboard", "Gestión de Invitados", "Enviar Invitaciones"],
        index=0 if st.session_state['pagina_actual'] == 'Dashboard' else 1
    )
    
    st.markdown("---")
    st.info("Sistema protegido contra errores de estado.")
    
    # Botón de reinicio de emergencia
    if st.button("⚠️ Resetear Fábrica"):
        st.session_state.clear()
        st.rerun()

# ==============================================================================
# PÁGINA 1: DASHBOARD
# ==============================================================================
if opcion == "Dashboard":
    st.title("📊 Panel de Control General")
    
    df = st.session_state['df_invitados']
    
    # Cálculos seguros (usando .get o validando columnas)
    total_invitados = df['Familia'].sum()
    total_confirmados = df[df['Estado'] == 'Confirmado']['Familia'].sum()
    total_pendientes = total_invitados - total_confirmados
    
    # Métricas visuales
    col1, col2, col3 = st.columns(3)
    col1.metric("Total Personas", total_invitados, "Capacidad")
    col2.metric("Confirmados", total_confirmados, "Asistentes firmes")
    col3.metric("Pendientes", total_pendientes, "Por confirmar")
    
    st.markdown("---")
    st.subheader("Estado de las Confirmaciones")
    
    # Gráfico simple de barras
    conteo_estados = df['Estado'].value_counts()
    st.bar_chart(conteo_estados)

# ==============================================================================
# PÁGINA 2: GESTIÓN DE INVITADOS (CRUD)
# ==============================================================================
elif opcion == "Gestión de Invitados":
    st.title("📝 Gestión Total de Lista")
    
    df = st.session_state['df_invitados']
    
    tab1, tab2 = st.tabs(["📋 Lista y Edición", "➕ Agregar Nuevo"])
    
    # --- TAB 1: EDICIÓN TIPO EXCEL ---
    with tab1:
        st.write("Edita los datos directamente en la tabla:")
        
        # Data Editor permite editar celdas directamente
        df_editado = st.data_editor(
            df,
            column_config={
                "Estado": st.column_config.SelectboxColumn(
                    "Estado",
                    help="Estado de la invitación",
                    width="medium",
                    options=["Pendiente", "Confirmado", "Rechazado"],
                    required=True,
                ),
                "Familia": st.column_config.NumberColumn(
                    "Pax (Personas)",
                    min_value=1,
                    max_value=10,
                    step=1,
                ),
            },
            hide_index=True,
            num_rows="dynamic" # Permite borrar o agregar filas abajo
        )
        
        # Detectar si hubo cambios comparando con el session_state original
        if not df_editado.equals(df):
            guardar_cambios(df_editado)
            st.rerun()

    # --- TAB 2: FORMULARIO AGREGAR ---
    with tab2:
        st.subheader("Registrar nuevo invitado")
        with st.form("form_agregar"):
            col_a, col_b = st.columns(2)
            nombre = col_a.text_input("Nombre Completo")
            email = col_b.text_input("Correo Electrónico")
            
            col_c, col_d = st.columns(2)
            personas = col_c.number_input("Número de personas (Pax)", min_value=1, value=1)
            estado = col_d.selectbox("Estado Inicial", ["Pendiente", "Confirmado"])
            
            submit = st.form_submit_button("Guardar Invitado")
            
            if submit:
                if nombre:
                    nuevo_dato = {
                        'ID': str(uuid.uuid4())[:8],
                        'Nombre': nombre,
                        'Email': email,
                        'Familia': personas,
                        'Estado': estado,
                        'Mesa': None
                    }
                    # Concatenar de forma segura
                    st.session_state['df_invitados'] = pd.concat(
                        [st.session_state['df_invitados'], pd.DataFrame([nuevo_dato])], 
                        ignore_index=True
                    )
                    st.success(f"Invitado {nombre} agregado con éxito.")
                    st.rerun()
                else:
                    st.error("El nombre es obligatorio.")

# ==============================================================================
# PÁGINA 3: ENVÍO DE INVITACIONES
# ==============================================================================
elif opcion == "Enviar Invitaciones":
    st.title("📩 Centro de Envíos")
    
    df = st.session_state['df_invitados']
    
    st.info("Aquí puedes ver los enlaces únicos para enviar por WhatsApp o Correo.")
    
    busqueda = st.selectbox("Selecciona un invitado para ver su link:", df['Nombre'].unique())
    
    if busqueda:
        # Búsqueda segura usando filtros de Pandas
        datos_invitado = df[df['Nombre'] == busqueda].iloc[0]
        codigo_unico = datos_invitado['ID']
        
        # Simulamos un link real
        link_falso = f"https://mi-evento.com/rsvp?code={codigo_unico}"
        
        st.subheader(f"Invitación para: {busqueda}")
        st.code(link_falso, language="text")
        
        col1, col2 = st.columns(2)
        with col1:
            st.button("Copiar Link (Simulado)")
        with col2:
            mensaje_wa = f"Hola {busqueda}, te invito a mi evento. Confirma aquí: {link_falso}"
            st.text_area("Mensaje para WhatsApp", value=mensaje_wa, height=100)
