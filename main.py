import streamlit as st
import gspread
import pandas as pd
from datetime import datetime, timedelta
from oauth2client.service_account import ServiceAccountCredentials
import json
import os

# Instellingen
st.set_page_config(layout="wide")

DB_PATH = "weekplanning_db.json"

# Google Sheets toegang
@st.cache_resource
def get_gsheet_client():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    
    def to_dict(_d):
        if isinstance(_d, dict):
            return {k: to_dict(v) for k, v in _d.items()}
        elif isinstance(_d, list):
            return [to_dict(x) for x in _d]
        else:
            return _d
    
    service_account_info = to_dict(st.secrets["gcp_service_account"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(service_account_info, scopes=scope)
    return gspread.authorize(creds)

@st.cache_data(ttl=300)
def load_all_sheets():
    try:
        client = get_gsheet_client()
        spreadsheet = client.open("Gezinsplanning")
        sheets = {
            'eten': spreadsheet.worksheet("Eten"),
            'taken': spreadsheet.worksheet("Taken"), 
            'act_cedric': spreadsheet.worksheet("Activiteiten Cédric"),
            'act_lise': spreadsheet.worksheet("Activiteiten Lise"),
            'act_kids': spreadsheet.worksheet("Activiteiten kids")
        }
        data = {}
        for key, sheet in sheets.items():
            df = pd.DataFrame(sheet.get_all_records())
            data[key] = df
        return data
    except Exception as e:
        st.error(f"❌ Fout bij laden van Google Sheets: {e}")
        return None

def taak_bestaat_al(nieuwe_taak, taken_df):
    return nieuwe_taak in taken_df['taak'].values

def add_to_taken_sheet(nieuwe_taak, frequency, effort):
    try:
        client = get_gsheet_client()
        sheet = client.open("Gezinsplanning").worksheet("Taken")
        sheet.append_row([nieuwe_taak, frequency, effort])
        st.success(f"✅ '{nieuwe_taak}' toegevoegd aan Taken")
        st.cache_data.clear()
    except Exception as e:
        st.warning(f"⚠️ Fout bij toevoegen aan Taken: {e}")

def verwijder_taak(sheet, taak_naam):
    cell = sheet.find(taak_naam)
    if cell:
        sheet.delete_rows(cell.row)

def load_db():
    if os.path.exists(DB_PATH):
        with open(DB_PATH, "r") as f:
            return json.load(f)
    return {}

def save_db(data):
    with open(DB_PATH, "w") as f:
        json.dump(data, f, indent=2)

def save_planning_to_gsheet(week_key, planning):
    try:
        client = get_gsheet_client()
        sheet = client.open("Gezinsplanning").worksheet("Weekresultaten")
        sheet.append_row([week_key, json.dumps(planning)])
    except Exception as e:
        st.warning(f"⚠️ Kon niet opslaan naar Google Sheets: {e}")

def generate_daily_planning(dag, data, taak_planning_week):
    #taken_lijst = data['taken']['Taak'].tolist()
    act_lijst_cedric = data['act_cedric']['Activiteiten'].tolist()
    act_lijst_lise = data['act_lise']['Activiteiten'].tolist()
    act_lijst_kids = data['act_kids']['Activiteiten'].tolist()
    #dag_index = dag.weekday()
    taak_cedric, taak_lise = "", ""

    taken_cedric = taak_planning_week.get("cedric", [])
    taken_lise = taak_planning_week.get("lise", [])

    dagnummer = dag.weekday()  # 0=ma, ..., 6=zo
    cedric_dagen = [1, 3, 5]
    lise_dagen = [0, 2, 4]

    if dagnummer in cedric_dagen:
        index = cedric_dagen.index(dagnummer)
        if index < len(taken_cedric):
            taak_cedric = taken_cedric[index]['Taak']

    if dagnummer in lise_dagen:
        index = lise_dagen.index(dagnummer)
        if index < len(taken_lise):
            taak_lise = taken_lise[index]['Taak']

    return {
        "datum": dag.strftime('%A %d %B %Y'),
        "dag_kort": dag.strftime('%a %d/%m'),
        "eten": data['eten'].iloc[dag.day % len(data['eten']), 0],
        "taak_lise": taak_lise,
        "taak_cedric": taak_cedric,
        "all": act_lijst_cedric[dag.day % len(act_lijst_cedric)],
        "cedric": act_lijst_cedric[(dag.day +1) % len(act_lijst_cedric)],
        "lise": act_lijst_lise[dag.day % len(act_lijst_lise)],
        "lars": act_lijst_kids[dag.day % len(act_lijst_kids)],
        "robbe": act_lijst_kids[(dag.day +3) % len(act_lijst_kids)],
    }

def verdeel_taken_per_persoon(taken_df, referentiedatum, personen):
    def effort_score(e):
        return {'Laag': 1, 'Gemiddeld': 2, 'Hoog': 3}[e]

    def mag_nog_niet(tijdseenheid, laatst):
        if not laatst:
            return True
        try:
            laatst_datum = datetime.strptime(laatst, "%Y-%m-%d")
        except:
            return True
        delta = (referentiedatum - laatst_datum).days
        return {
            'Wekelijks': delta >= 7,
            'Maandelijks': delta >= 30,
            'Jaarlijks': delta >= 365,
            'Om de 5 jaar': delta >= 1825
        }.get(tijdseenheid, True)

    df = taken_df.copy()
    df = df[df.apply(lambda r: mag_nog_niet(r['Frequentie'], r.get('Laatst_Uitgevoerd')), axis=1)]
    df['Effort_Score'] = df['Effort'].map(effort_score)

    planning = {persoon: [] for persoon in personen}
    reeds_toegewezen = set()

    for persoon in personen:
        huidige_taken = []
        huidige_efforts = []

        for _, taak in df.iterrows():
            if taak['Taak'] in reeds_toegewezen:
                continue

            if len(huidige_taken) >= 3:
                break

            kandidaat_efforts = sorted(huidige_efforts + [taak['Effort']])
            toegelaten = kandidaat_efforts in [
                ["Laag"], ["Laag", "Laag"], ["Laag", "Hoog"], ["Laag", "Gemiddeld"],
                ["Gemiddeld", "Gemiddeld"], ["Laag", "Laag", "Laag"],
                ["Laag", "Laag", "Hoog"], ["Gemiddeld", "Gemiddeld", "Laag"]
            ]

            if toegelaten:
                planning[persoon].append(taak)
                huidige_taken.append(taak)
                huidige_efforts.append(taak['Effort'])
                reeds_toegewezen.add(taak['Taak'])
    return planning

# UI
st.markdown("""
<style>
html, body, [class*="css"] { font-size: 18px !important; }
h1, h2, h3, h4, h5, h6 { font-size: 26px !important; }
</style>
""", unsafe_allow_html=True)

st.title("💖Olieboom Weekplanner💖")
col1, col2 = st.columns([1, 1])
with col1:
    start_dag = st.date_input("Startdatum weekplanning", value=datetime.today())
with col2:
    if st.button("🔄 Nieuwe planning genereren"):
        st.cache_data.clear()

data = load_all_sheets()
db = load_db()

if data:
    planning = []
    sheet_taken = get_gsheet_client().open("Gezinsplanning").worksheet("Taken")

    personen = ["cedric", "lise"]
    taak_planning_week = verdeel_taken_per_persoon(data['taken'], start_dag, personen)
    for i in range(7):
        dag = start_dag + timedelta(days=i)
        dag_key = dag.strftime("%Y-%m-%d")
        if dag_key in db:
            dag_planning = db[dag_key]
        else:
            dag_planning = generate_daily_planning(dag, data, taak_planning_week)
            db[dag_key] = dag_planning
        planning.append(dag_planning)

    save_db(db)

    st.subheader("📅 Weekoverzicht")
    st.markdown("📝 **Je wijzigingen hieronder worden automatisch opgeslagen**")
    cols = st.columns(7)

    for i, dag_planning in enumerate(planning):
        with cols[i]:
            st.markdown(f"**{dag_planning['dag_kort']}**")
            dag_planning['eten'] = st.selectbox(f"🍽️ Eten", options=data['eten'].iloc[:,0].tolist(), index=data['eten'].iloc[:,0].tolist().index(dag_planning['eten']), key=f"eten_{i}")
            st.markdown("---")
            if dag_planning['taak_lise']:
                st.markdown(f"🧹 **Taak Lise:**")
                taak_naam = dag_planning['taak_lise']
                taak_afgevinkt = st.checkbox(taak_naam, key=f"taak_chk_{i}")
            if dag_planning['taak_cedric']:
                st.markdown(f"🧹 **Taak Cedric:**")
                taak_naam = dag_planning['taak_cedric']
                taak_afgevinkt = st.checkbox(taak_naam, key=f"taak_chk_{i}")
            if dag_planning['taak_lise'] == "" and dag_planning['taak_cedric'] == "":
                st.markdown(f"🧹 **Geen taak vandaag**")
                taak_afgevinkt = None
            
            if taak_afgevinkt:
                idx = data['taken'][data['taken']['Taak'] == taak_naam].index[0]
                if data['taken'].iloc[idx]['Frequentie'] == 'Eenmalig':
                    verwijder_taak(sheet_taken, taak_naam)
                    data['taken'] = data['taken'].drop(index=idx).reset_index(drop=True)
                    st.success(f"🗑️ '{taak_naam}' verwijderd (eenmalige taak)")
                else:
                    data['taken'].at[idx, 'Laatst_Uitgevoerd'] = str(datetime.today())
                    kolomindex = data['taken'].columns.get_loc("Laatst_Uitgevoerd")
                    st.write(kolomindex)
                    sheet_taken.update_cell(idx + 2, kolomindex, str(datetime.today().date()))
                    st.success(f"✅ '{taak_naam}' gemarkeerd als uitgevoerd op {datetime.today()}")
            st.markdown("---")
            dag_planning['all'] = st.selectbox(f"👨‍👩‍👦‍👦 Iedereen", options=data['act_cedric']['Activiteiten'].tolist(), index=data['act_cedric']['Activiteiten'].tolist().index(dag_planning['all']), key=f"all_{i}")
            st.markdown("---")
            dag_planning['cedric'] = st.selectbox(f"👨‍🦱 Cedric", options=data['act_cedric']['Activiteiten'].tolist(), index=data['act_cedric']['Activiteiten'].tolist().index(dag_planning['cedric']), key=f"cedric_{i}")
            dag_planning['lise'] = st.selectbox(f"👩‍🦰 Lise", options=data['act_lise']['Activiteiten'].tolist(), index=data['act_lise']['Activiteiten'].tolist().index(dag_planning['lise']), key=f"lise_{i}")
            dag_planning['lars'] = st.selectbox(f"👦 Lars", options=data['act_kids']['Activiteiten'].tolist(), index=data['act_kids']['Activiteiten'].tolist().index(dag_planning['lars']), key=f"lars_{i}")
            dag_planning['robbe'] = st.selectbox(f"👶 Robbe", options=data['act_kids']['Activiteiten'].tolist(), index=data['act_kids']['Activiteiten'].tolist().index(dag_planning['robbe']), key=f"robbe_{i}")
            st.markdown("---")

    st.subheader("➕ Voeg nieuwe input toe")
    col3, col4, col5 = st.columns(3)
    with col3:
        nieuw_eten = st.text_input("Nieuw gerecht")
        if st.button("➕ Toevoegen aan Eten") and nieuw_eten:
            if nieuw_eten not in data['eten'].iloc[:,0].tolist():
                add_to_sheet("Eten", nieuw_eten)
            else:
                st.info("ℹ️ Dit gerecht bestaat al.")
    with col4:
        nieuwe_taak = st.text_input("Nieuwe taak")
        frequentie = st.selectbox("Frequentie:", ["Wekelijks", "Maandelijks", "Jaarlijks", "Om de 5 jaar"])
        effort = st.selectbox("Effort:", ["Laag", "Gemiddeld", "Hoog"])
        if st.button("➕ Toevoegen aan Taken") and nieuwe_taak:
            if not taak_bestaat_al(nieuwe_taak, data['taken']):
                add_to_taken_sheet(nieuwe_taak, frequentie, effort)
            else:
                st.info("ℹ️ Deze taak bestaat al.")
    with col5:
        nieuwe_activiteit = st.text_input("Nieuwe activiteit")
        if st.button("➕ Toevoegen aan Activiteiten") and nieuwe_activiteit:
            if nieuwe_activiteit not in data['activiteiten'].iloc[:,0].tolist():
                add_to_sheet("Activiteiten", nieuwe_activiteit)
            else:
                st.info("ℹ️ Deze activiteit bestaat al.")
