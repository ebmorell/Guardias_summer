import streamlit as st
import pandas as pd
import random
import math
import io
from datetime import datetime

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURACIÓN
# ─────────────────────────────────────────────────────────────────────────────
st.set_page_config(page_title="Asignador de Guardias", layout="wide")

st.markdown("""
<style>
    .block-container { padding-top: 1.5rem; }
    .stButton > button {
        background-color: #005A9C;
        color: white;
        font-weight: 600;
        border-radius: 8px;
        padding: 0.5rem 2rem;
        border: none;
    }
    .stButton > button:hover { background-color: #004070; }
    .metric-box {
        background: #f0f4f8;
        border-radius: 10px;
        padding: 1rem;
        text-align: center;
    }
</style>
""", unsafe_allow_html=True)

st.title("🩺 Asignador de Guardias Médicas")
st.caption("Distribución equitativa proporcional a los días disponibles de cada facultativo.")

# ─────────────────────────────────────────────────────────────────────────────
# ALGORITMO PRINCIPAL (sin ortools)
# ─────────────────────────────────────────────────────────────────────────────

def calcular_cuota_mensual(meses, calendar, num_medicos, medicos_por_dia,
                            vacaciones_por_medico):
    """
    Calcula la cuota esperada (min, max) de guardias por médico y mes,
    proporcional a sus días disponibles en ese mes.
    """
    cuotas = {}  # (mi, str(mes)) -> (min_g, max_g)
    for mes in meses:
        dias_mes = calendar[calendar["Mes"] == mes].index.tolist()
        total_slots = len(dias_mes) * medicos_por_dia

        dias_disponibles = {}
        for mi in range(num_medicos):
            dias_lib = sum(
                1 for d in dias_mes
                if calendar.iloc[d]["Fecha"] not in vacaciones_por_medico[mi]
            )
            dias_disponibles[mi] = dias_lib

        total_disp = sum(dias_disponibles.values())
        if total_disp == 0:
            for mi in range(num_medicos):
                cuotas[(mi, str(mes))] = (0, 0)
            continue

        for mi in range(num_medicos):
            if dias_disponibles[mi] == 0:
                cuotas[(mi, str(mes))] = (0, 0)
            else:
                prop = dias_disponibles[mi] / total_disp
                esperado = total_slots * prop
                min_g = math.floor(esperado)
                max_g = math.ceil(esperado)
                max_g = min(max_g, dias_disponibles[mi])
                min_g = min(min_g, max_g)
                cuotas[(mi, str(mes))] = (min_g, max_g)
    return cuotas


def intentar_asignacion(calendar, vacaciones_por_medico, restricciones_set,
                         medicos, especialidad_dict, medicos_por_dia,
                         dias_entre_guardias, evitar_misma_especialidad,
                         cuotas, seed):
    """
    Intento greedy de asignación con semilla aleatoria dada.
    Devuelve (dict asignaciones día->lista médicos, None) o (None, mensaje_error).
    """
    rng = random.Random(seed)
    num_medicos = len(medicos)
    num_dias = len(calendar)

    asignaciones = {d: [] for d in range(num_dias)}
    ultima_guardia = [-999] * num_medicos          # último día asignado
    guardias_mes = [{} for _ in range(num_medicos)]  # mes_str -> count
    guardias_total = [0] * num_medicos
    guardias_fds = [0] * num_medicos

    for d in range(num_dias):
        fecha = calendar.iloc[d]["Fecha"]
        mes_str = str(calendar.iloc[d]["Mes"])
        tipo = calendar.iloc[d]["Tipo de día"]

        # ── Elegibles básicos ────────────────────────────────────────────────
        elegibles = []
        for mi in range(num_medicos):
            # Vacaciones
            if fecha in vacaciones_por_medico[mi]:
                continue
            # Restricción individual
            if (medicos[mi], fecha) in restricciones_set:
                continue
            # Descanso mínimo
            if d - ultima_guardia[mi] <= dias_entre_guardias:
                continue
            # Cuota mensual no superada
            _, max_g = cuotas.get((mi, mes_str), (0, 999))
            if guardias_mes[mi].get(mes_str, 0) >= max_g:
                continue
            elegibles.append(mi)

        if len(elegibles) < medicos_por_dia:
            # Relajar cuota mensual como fallback
            elegibles_sin_cuota = []
            for mi in range(num_medicos):
                if fecha in vacaciones_por_medico[mi]:
                    continue
                if (medicos[mi], fecha) in restricciones_set:
                    continue
                if d - ultima_guardia[mi] <= dias_entre_guardias:
                    continue
                elegibles_sin_cuota.append(mi)
            if len(elegibles_sin_cuota) < medicos_por_dia:
                return None, f"Sin médicos suficientes el {fecha.strftime('%d/%m/%Y')}"
            elegibles = elegibles_sin_cuota

        # ── Puntuación (menor = más prioritario) ────────────────────────────
        def score(mi):
            g_mes = guardias_mes[mi].get(mes_str, 0)
            g_tot = guardias_total[mi]
            g_fds = guardias_fds[mi] if tipo in ("Fin de semana", "Festivo") else 0
            jitter = rng.uniform(0, 0.3)
            return (g_mes * 100 + g_fds * 10 + g_tot + jitter)

        elegibles_ord = sorted(elegibles, key=score)

        # ── Selección respetando especialidad ────────────────────────────────
        seleccionados = []
        especialidades_usadas = set()

        for mi in elegibles_ord:
            if len(seleccionados) >= medicos_por_dia:
                break
            esp = especialidad_dict[medicos[mi]]
            if evitar_misma_especialidad and esp in especialidades_usadas:
                continue
            seleccionados.append(mi)
            especialidades_usadas.add(esp)

        # Relajar restricción de especialidad si no hay suficientes
        if len(seleccionados) < medicos_por_dia:
            for mi in elegibles_ord:
                if len(seleccionados) >= medicos_por_dia:
                    break
                if mi not in seleccionados:
                    seleccionados.append(mi)

        if len(seleccionados) < medicos_por_dia:
            return None, f"Sin médicos suficientes el {fecha.strftime('%d/%m/%Y')}"

        # ── Registrar asignación ─────────────────────────────────────────────
        for mi in seleccionados:
            asignaciones[d].append(mi)
            ultima_guardia[mi] = d
            guardias_mes[mi][mes_str] = guardias_mes[mi].get(mes_str, 0) + 1
            guardias_total[mi] += 1
            if tipo in ("Fin de semana", "Festivo"):
                guardias_fds[mi] += 1

    return asignaciones, None


def resolver(calendar, vacaciones_por_medico, restricciones_set,
             medicos, especialidad_dict, medicos_por_dia,
             dias_entre_guardias, evitar_misma_especialidad,
             cuotas, max_intentos=200):
    """
    Ejecuta múltiples intentos con semillas distintas.
    Devuelve el mejor resultado (mínima desviación estándar de guardias totales).
    """
    mejor = None
    mejor_std = float("inf")
    ultimo_error = ""

    for intento in range(max_intentos):
        asign, error = intentar_asignacion(
            calendar, vacaciones_por_medico, restricciones_set,
            medicos, especialidad_dict, medicos_por_dia,
            dias_entre_guardias, evitar_misma_especialidad,
            cuotas, seed=intento
        )
        if asign is None:
            ultimo_error = error
            continue

        # Evaluar calidad: desviación estándar de guardias totales
        totales = [sum(1 for d in asign if mi in asign[d]) for mi in range(len(medicos))]
        std = (sum((t - sum(totales)/len(totales))**2 for t in totales) / len(totales)) ** 0.5

        if std < mejor_std:
            mejor_std = std
            mejor = asign
            if std < 0.5:   # Solución casi perfecta, no seguir buscando
                break

    if mejor is None:
        return None, ultimo_error
    return mejor, None


# ─────────────────────────────────────────────────────────────────────────────
# INTERFAZ
# ─────────────────────────────────────────────────────────────────────────────

archivo = st.file_uploader(
    "📤 Sube el archivo Excel con vacaciones y especialidad",
    type=["xlsx"],
    help="El Excel debe tener columnas: Medico | especialidad | Fecha inicio | Fecha fin"
)

if archivo:
    try:
        vacaciones_df = pd.read_excel(archivo)
        vacaciones_df["Fecha inicio"] = pd.to_datetime(vacaciones_df["Fecha inicio"])
        vacaciones_df["Fecha fin"] = pd.to_datetime(vacaciones_df["Fecha fin"])
    except Exception as e:
        st.error(f"Error al leer el Excel: {e}")
        st.stop()

    medicos_df = vacaciones_df.drop_duplicates(subset="Medico")[["Medico", "especialidad"]].copy()
    medicos = medicos_df["Medico"].tolist()
    especialidades = medicos_df["especialidad"].tolist()
    medico_idx = {m: i for i, m in enumerate(medicos)}
    especialidad_dict = dict(zip(medicos, especialidades))
    num_medicos = len(medicos)

    col_a, col_b = st.columns(2)
    with col_a:
        st.metric("Facultativos cargados", num_medicos)
    with col_b:
        st.metric("Especialidades distintas", len(set(especialidades)))

    st.divider()

    # ── Periodo ──────────────────────────────────────────────────────────────
    st.subheader("📆 Periodo de guardias")
    col1, col2 = st.columns(2)
    with col1:
        start_date = st.date_input("Inicio", datetime(2025, 7, 1))
    with col2:
        end_date = st.date_input("Fin", datetime(2025, 9, 30))

    if start_date >= end_date:
        st.error("La fecha de inicio debe ser anterior a la fecha de fin.")
        st.stop()

    calendar = pd.DataFrame({"Fecha": pd.date_range(start=start_date, end=end_date, freq="D")})
    calendar["Tipo de día"] = calendar["Fecha"].apply(
        lambda x: "Fin de semana" if x.weekday() >= 5 else "Laborable"
    )
    calendar["Mes"] = calendar["Fecha"].dt.to_period("M")

    # ── Festivos ─────────────────────────────────────────────────────────────
    st.subheader("🎉 Días festivos")
    festivos = st.multiselect(
        "Selecciona los días festivos nacionales o locales:",
        options=calendar["Fecha"].tolist(),
        format_func=lambda x: x.strftime("%A %d/%m/%Y")
    )
    festivos_set = set(pd.Timestamp(f) for f in festivos)
    calendar["Tipo de día"] = calendar.apply(
        lambda row: "Festivo" if row["Fecha"] in festivos_set else row["Tipo de día"], axis=1
    )

    st.divider()

    # ── Parámetros ────────────────────────────────────────────────────────────
    st.subheader("⚙️ Parámetros de asignación")
    col1, col2, col3 = st.columns(3)
    with col1:
        dias_entre_guardias = st.slider("📆 Días mínimos entre guardias", 1, 7, 3)
    with col2:
        medicos_por_dia = st.slider("👥 Médicos por día de guardia", 1, 6, 3)
    with col3:
        evitar_misma_especialidad = st.checkbox("🚫 No coincidir misma especialidad", value=True)

    # ── Restricciones individuales ────────────────────────────────────────────
    st.subheader("🔒 Restricciones individuales")
    with st.expander("➕ Añadir días bloqueados por médico"):
        restricciones_lista = []
        num_res = st.number_input("Número de restricciones", 0, 60, 0)
        for i in range(num_res):
            c1, c2 = st.columns(2)
            with c1:
                nombre = st.selectbox(f"Médico #{i+1}", medicos, key=f"r_med_{i}")
            with c2:
                fecha_r = st.date_input("Día bloqueado", key=f"r_fecha_{i}")
            restricciones_lista.append((nombre, pd.Timestamp(fecha_r)))

    restricciones_set = set(restricciones_lista)

    st.divider()

    # ── Precomputar vacaciones ─────────────────────────────────────────────────
    vacaciones_por_medico = {mi: set() for mi in range(num_medicos)}
    for _, row in vacaciones_df.iterrows():
        if row["Medico"] in medico_idx:
            mi = medico_idx[row["Medico"]]
            for fecha in pd.date_range(row["Fecha inicio"], row["Fecha fin"]):
                vacaciones_por_medico[mi].add(fecha)

    meses = calendar["Mes"].unique()

    # ── Tabla de disponibilidad ───────────────────────────────────────────────
    with st.expander("📋 Ver días disponibles por médico y mes"):
        disp_data = {"Médico": medicos, "Especialidad": especialidades}
        for mes in meses:
            dias_mes = calendar[calendar["Mes"] == mes].index.tolist()
            disp_data[str(mes)] = [
                sum(1 for d in dias_mes if calendar.iloc[d]["Fecha"] not in vacaciones_por_medico[mi])
                for mi in range(num_medicos)
            ]
        disp_data["Total días disponibles"] = [
            sum(disp_data[str(mes)][mi] for mes in meses)
            for mi in range(num_medicos)
        ]
        st.dataframe(pd.DataFrame(disp_data), use_container_width=True)

    # ── Botón generar ─────────────────────────────────────────────────────────
    if st.button("📅 Generar calendario de guardias"):

        cuotas = calcular_cuota_mensual(
            meses, calendar, num_medicos, medicos_por_dia, vacaciones_por_medico
        )

        with st.spinner("Calculando la distribución óptima..."):
            asignaciones, error = resolver(
                calendar, vacaciones_por_medico, restricciones_set,
                medicos, especialidad_dict, medicos_por_dia,
                dias_entre_guardias, evitar_misma_especialidad,
                cuotas, max_intentos=300
            )

        if asignaciones is None:
            st.error(
                f"❌ No se encontró solución: {error}\n\n"
                "Prueba: reducir médicos por día, ampliar el periodo, "
                "reducir días mínimos entre guardias, o revisar las vacaciones."
            )
            st.stop()

        st.success("✅ Guardias generadas correctamente")

        # ── Construir DataFrame resultado ─────────────────────────────────────
        resultados = []
        for d in range(len(calendar)):
            fecha = calendar.iloc[d]["Fecha"]
            tipo = calendar.iloc[d]["Tipo de día"]
            medicos_dia = [medicos[mi] for mi in asignaciones[d]]
            while len(medicos_dia) < medicos_por_dia:
                medicos_dia.append("")
            fila = {"Fecha": fecha.strftime("%d/%m/%Y"), "Día": fecha.strftime("%A"), "Tipo": tipo}
            for i, m in enumerate(medicos_dia):
                fila[f"Médico {i+1}"] = m
            resultados.append(fila)

        df_final = pd.DataFrame(resultados)
        st.dataframe(df_final, use_container_width=True, height=400)

        # ── Resumen por médico ────────────────────────────────────────────────
        st.subheader("📊 Resumen de guardias por médico y mes")

        resumen = {medico: {} for medico in medicos}
        resumen_fds = {medico: 0 for medico in medicos}
        for d in range(len(calendar)):
            mes_str = str(calendar.iloc[d]["Mes"])
            tipo = calendar.iloc[d]["Tipo de día"]
            for mi in asignaciones[d]:
                nombre = medicos[mi]
                resumen[nombre][mes_str] = resumen[nombre].get(mes_str, 0) + 1
                if tipo in ("Fin de semana", "Festivo"):
                    resumen_fds[nombre] += 1

        resumen_df = pd.DataFrame(resumen).T.fillna(0).astype(int)
        resumen_df["FDS/Festivos"] = pd.Series(resumen_fds)
        resumen_df["TOTAL"] = resumen_df[[c for c in resumen_df.columns if c not in ("FDS/Festivos", "TOTAL")]].sum(axis=1)

        # Añadir especialidad
        resumen_df.insert(0, "Especialidad", [especialidad_dict[m] for m in resumen_df.index])

        st.dataframe(resumen_df, use_container_width=True)

        # Métricas de equidad
        totales = resumen_df["TOTAL"].values
        media = totales.mean()
        desv = totales.std()
        col1, col2, col3 = st.columns(3)
        col1.metric("Media guardias/médico", f"{media:.1f}")
        col2.metric("Desviación estándar", f"{desv:.2f}")
        col3.metric("Diferencia máx-mín", f"{totales.max() - totales.min()}")

        # ── Descarga Excel ────────────────────────────────────────────────────
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
            df_final.to_excel(writer, index=False, sheet_name="Calendario")
            resumen_df.to_excel(writer, sheet_name="Resumen por médico")

            # Dar formato
            wb = writer.book
            fmt_header = wb.add_format({
                "bold": True, "bg_color": "#005A9C",
                "font_color": "white", "border": 1
            })
            for sheet_name in ["Calendario", "Resumen por médico"]:
                ws = writer.sheets[sheet_name]
                ws.set_row(0, 18, fmt_header)
                ws.freeze_panes(1, 0)

        output.seek(0)
        st.download_button(
            label="📥 Descargar Excel",
            data=output,
            file_name="Guardias_verano.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
