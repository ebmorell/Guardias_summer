import streamlit as st
import pandas as pd
import math
from datetime import datetime
from ortools.sat.python import cp_model
import io

st.set_page_config(page_title="Asignador de Guardias", layout="wide")
st.title("🩺 Asignador de Guardias Médicas")

archivo = st.file_uploader("📤 Sube el archivo Excel con vacaciones y especialidad", type=["xlsx"])

if archivo:
    vacaciones_df = pd.read_excel(archivo)
    vacaciones_df["Fecha inicio"] = pd.to_datetime(vacaciones_df["Fecha inicio"])
    vacaciones_df["Fecha fin"] = pd.to_datetime(vacaciones_df["Fecha fin"])

    medicos_df = vacaciones_df.drop_duplicates(subset="Medico")[["Medico", "especialidad"]].copy()
    medicos = medicos_df["Medico"].tolist()
    especialidades = medicos_df["especialidad"].tolist()
    medico_idx = {m: i for i, m in enumerate(medicos)}
    especialidad_dict = dict(zip(medicos, especialidades))
    num_medicos = len(medicos)

    st.subheader("📆 Periodo de guardias")
    col1, col2 = st.columns(2)
    with col1:
        start_date = st.date_input("Inicio", datetime(2025, 7, 1))
    with col2:
        end_date = st.date_input("Fin", datetime(2025, 9, 30))

    calendar = pd.DataFrame({"Fecha": pd.date_range(start=start_date, end=end_date, freq='D')})
    calendar["Tipo de día"] = calendar["Fecha"].apply(lambda x: "Fin de semana" if x.weekday() >= 5 else "Laborable")
    calendar["Mes"] = calendar["Fecha"].dt.to_period("M")
    num_dias = len(calendar)

    st.subheader("🎉 Días festivos")
    festivos = st.multiselect(
        "Selecciona los días festivos:",
        options=calendar["Fecha"].tolist(),
        format_func=lambda x: x.strftime("%A %d/%m/%Y")
    )
    festivos_set = set(festivos)
    calendar["Tipo de día"] = calendar.apply(
        lambda row: "Festivo" if row["Fecha"] in festivos_set else row["Tipo de día"], axis=1
    )

    st.header("⚙️ Parámetros de asignación")
    col1, col2 = st.columns(2)
    with col1:
        dias_entre_guardias = st.slider("📆 Días mínimos entre guardias", 1, 7, 3)
        medicos_por_dia = st.slider("👥 Número de médicos por día", 1, 5, 3)
    with col2:
        evitar_misma_especialidad = st.checkbox("🚫 Evitar coincidencia de misma especialidad", value=True)
        st.info(
            "ℹ️ La distribución mensual es **automáticamente proporcional** "
            "a los días disponibles de cada médico en ese mes."
        )

    st.subheader("🔒 Restricciones individuales")
    with st.expander("➕ Añadir restricciones personalizadas"):
        restricciones_individuales = []
        num_restricciones = st.number_input(
            "¿Cuántas restricciones quieres añadir?", min_value=0, max_value=50, value=0
        )
        for i in range(num_restricciones):
            col1, col2 = st.columns(2)
            with col1:
                nombre = st.selectbox(f"👤 Médico #{i+1}", options=medicos, key=f"medico_{i}")
            with col2:
                fecha_restringida = st.date_input(f"📅 Día bloqueado", key=f"fecha_{i}")
            restricciones_individuales.append((nombre, pd.to_datetime(fecha_restringida)))

    if st.button("📅 Generar calendario de guardias"):

        # ── Precomputar días de vacaciones por médico ──────────────────────────
        vacaciones_por_medico = {mi: set() for mi in range(num_medicos)}
        for _, row in vacaciones_df.iterrows():
            if row["Medico"] in medico_idx:
                mi = medico_idx[row["Medico"]]
                for fecha in pd.date_range(row["Fecha inicio"], row["Fecha fin"]):
                    vacaciones_por_medico[mi].add(fecha)

        # ── Precomputar días disponibles por médico y mes ──────────────────────
        meses = calendar["Mes"].unique()
        disponibilidad = {}  # (mi, mes) -> lista de índices de días disponibles
        for mes in meses:
            dias_mes = calendar[calendar["Mes"] == mes].index.tolist()
            for mi in range(num_medicos):
                dias_libres = [
                    d for d in dias_mes
                    if calendar.iloc[d]["Fecha"] not in vacaciones_por_medico[mi]
                ]
                disponibilidad[(mi, mes)] = dias_libres

        # ── Mostrar tabla de disponibilidad ───────────────────────────────────
        disp_data = {
            "Médico": medicos,
            **{
                str(mes): [len(disponibilidad[(mi, mes)]) for mi in range(num_medicos)]
                for mes in meses
            }
        }
        with st.expander("📋 Ver días disponibles por médico y mes"):
            st.dataframe(pd.DataFrame(disp_data))

        # ── Modelo CP-SAT ──────────────────────────────────────────────────────
        model = cp_model.CpModel()
        x = {
            (m, d): model.NewBoolVar(f"x_{m}_{d}")
            for m in range(num_medicos) for d in range(num_dias)
        }

        # 1. Médicos por día exacto
        for d in range(num_dias):
            model.Add(sum(x[m, d] for m in range(num_medicos)) == medicos_por_dia)

        # 2. Bloquear días de vacaciones
        for mi in range(num_medicos):
            for d in range(num_dias):
                if calendar.iloc[d]["Fecha"] in vacaciones_por_medico[mi]:
                    model.Add(x[mi, d] == 0)

        # 3. Restricciones individuales
        fechas_calendar = calendar["Fecha"].values
        for nombre, fecha_restringida in restricciones_individuales:
            if nombre in medico_idx:
                mi = medico_idx[nombre]
                matches = calendar[calendar["Fecha"] == fecha_restringida]
                if not matches.empty:
                    d = matches.index[0]
                    model.Add(x[mi, d] == 0)

        # 4. Mínimo de días entre guardias
        for mi in range(num_medicos):
            for d in range(num_dias - dias_entre_guardias):
                model.Add(sum(x[mi, d + i] for i in range(dias_entre_guardias + 1)) <= 1)

        # 5. ── DISTRIBUCIÓN PROPORCIONAL POR MES ──────────────────────────────
        #    Para cada mes, cada médico recibe guardias proporcionales a sus
        #    días disponibles en ese mes. Así, quien está de vacaciones en julio
        #    no acumula guardias extra en agosto o septiembre.
        for mes in meses:
            dias_mes = calendar[calendar["Mes"] == mes].index.tolist()
            total_slots_mes = len(dias_mes) * medicos_por_dia

            total_dias_disponibles = sum(
                len(disponibilidad[(mi, mes)]) for mi in range(num_medicos)
            )
            if total_dias_disponibles == 0:
                continue

            for mi in range(num_medicos):
                dias_libres_mes = len(disponibilidad[(mi, mes)])
                if dias_libres_mes == 0:
                    # Sin días disponibles → 0 guardias ese mes
                    model.Add(sum(x[mi, d] for d in dias_mes) == 0)
                else:
                    proporcion = dias_libres_mes / total_dias_disponibles
                    guardias_esperadas = total_slots_mes * proporcion
                    min_g = math.floor(guardias_esperadas)
                    max_g = math.ceil(guardias_esperadas)
                    # No puede hacer más guardias que días disponibles
                    max_g = min(max_g, dias_libres_mes)
                    min_g = min(min_g, max_g)  # Seguridad por si hay muy pocos días
                    model.Add(sum(x[mi, d] for d in dias_mes) >= min_g)
                    model.Add(sum(x[mi, d] for d in dias_mes) <= max_g)

        # 6. Reparto equitativo de fines de semana y festivos
        fds_festivos = [
            i for i, tipo in enumerate(calendar["Tipo de día"])
            if tipo in ("Fin de semana", "Festivo")
        ]
        if fds_festivos:
            total_fds = len(fds_festivos) * medicos_por_dia
            min_fds = total_fds // num_medicos
            max_fds = min_fds + (1 if total_fds % num_medicos > 0 else 0)
            for mi in range(num_medicos):
                disponibles_fds = [
                    d for d in fds_festivos
                    if calendar.iloc[d]["Fecha"] not in vacaciones_por_medico[mi]
                ]
                if disponibles_fds:
                    model.Add(sum(x[mi, d] for d in disponibles_fds) >= min_fds)
                    model.Add(sum(x[mi, d] for d in disponibles_fds) <= max_fds)

        # 7. No repetir especialidad en un mismo día
        if evitar_misma_especialidad:
            especialidades_unicas = list(set(especialidades))
            for d in range(num_dias):
                for esp in especialidades_unicas:
                    indices = [i for i, m in enumerate(medicos) if especialidad_dict[m] == esp]
                    if len(indices) > 1:
                        model.Add(sum(x[mi, d] for mi in indices) <= 1)

        # ── Resolver ──────────────────────────────────────────────────────────
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = 90.0
        status = solver.Solve(model)

        if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
            resultados = []
            for d in range(num_dias):
                fecha = calendar.iloc[d]["Fecha"]
                tipo = calendar.iloc[d]["Tipo de día"]
                medicos_dia = [medicos[mi] for mi in range(num_medicos) if solver.Value(x[mi, d]) == 1]
                while len(medicos_dia) < medicos_por_dia:
                    medicos_dia.append("")
                fila = {"Fecha": fecha.strftime("%d/%m/%Y"), "Tipo de día": tipo}
                for i in range(medicos_por_dia):
                    fila[f"Médico {i+1}"] = medicos_dia[i]
                resultados.append(fila)

            df_final = pd.DataFrame(resultados)
            st.success("✅ Guardias generadas correctamente")
            st.dataframe(df_final, use_container_width=True)

            # ── Resumen por médico y mes ───────────────────────────────────────
            st.subheader("📊 Resumen de guardias por médico y mes")
            resumen = {medico: {} for medico in medicos}
            for d in range(num_dias):
                mes_str = str(calendar.iloc[d]["Mes"])
                for mi in range(num_medicos):
                    if solver.Value(x[mi, d]) == 1:
                        resumen[medicos[mi]][mes_str] = resumen[medicos[mi]].get(mes_str, 0) + 1

            resumen_df = pd.DataFrame(resumen).T.fillna(0).astype(int)
            resumen_df["TOTAL"] = resumen_df.sum(axis=1)
            st.dataframe(resumen_df, use_container_width=True)

            # ── Descargar Excel con dos hojas ──────────────────────────────────
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                df_final.to_excel(writer, index=False, sheet_name="Calendario")
                resumen_df.to_excel(writer, sheet_name="Resumen por médico")
            output.seek(0)

            st.download_button(
                label="📥 Descargar Excel",
                data=output,
                file_name="Guardias_verano.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
        else:
            st.error(
                "❌ No se encontró una solución factible. "
                "Prueba reduciendo el número de médicos por día, "
                "aumentando los días mínimos entre guardias, "
                "o revisando si hay suficientes médicos disponibles."
            )