import streamlit as st
import gspread
import pandas as pd
from datetime import datetime, timedelta
from oauth2client.service_account import ServiceAccountCredentials
import json
import os
import random

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

def add_to_sheet(sheet_name, new_value):
    client = get_gsheet_client()
    sheet = client.open("Gezinsplanning").worksheet(sheet_name)
    sheet.append_row([new_value])
    st.cache_data.clear()

def generate_daily_planning_with_randomness(dag, data, taak_planning_week, seed_offset=0):
    """Aangepaste versie met randomness voor variatie in planning"""
    
    # Gebruik datum + offset als seed voor consistente maar verschillende resultaten
    random.seed(dag.toordinal() + seed_offset)
    
    act_lijst_cedric = data['act_cedric']['Activiteiten'].tolist()
    act_lijst_lise = data['act_lise']['Activiteiten'].tolist()
    act_lijst_kids = data['act_kids']['Activiteiten'].tolist()
    eten_lijst = data['eten'].iloc[:,0].tolist()
    
    taak_cedric, taak_lise = "", ""

    taken_cedric = taak_planning_week.get("cedric", [])
    taken_lise = taak_planning_week.get("lise", [])

    dagnummer = dag.weekday()  # 0=ma, ..., 6=zo
    cedric_dagen = [1, 3, 5]  # di, do, za
    lise_dagen = [0, 2, 4]    # ma, wo, vr

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
        "eten": random.choice(eten_lijst),  # Random eten ipv gebaseerd op dag
        "taak_lise": taak_lise,
        "taak_cedric": taak_cedric,
        "cedric": random.choice(act_lijst_cedric),  # Random activiteit
        "lise": random.choice(act_lijst_lise),      # Random activiteit
        "kids": random.choice(act_lijst_kids),      # Random activiteit
        "all": random.choice(act_lijst_cedric),     # Random activiteit
    }

def verdeel_taken_per_persoon_with_shuffle(taken_df, referentiedatum, personen, shuffle_seed=None):
    """Aangepaste versie die taken shuffelt voor meer variatie"""
    
    if shuffle_seed:
        random.seed(shuffle_seed)
    
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
            '3-maadelijks': delta >= 90,
            'Half jaarlijks': delta >= 182,
            'Jaarlijks': delta >= 365,
            'Om de 5 jaar': delta >= 1825
        }.get(tijdseenheid, True)

    df = taken_df.copy()
    df = df[df.apply(lambda r: mag_nog_niet(r['Frequentie'], r.get('Laatst_Uitgevoerd')), axis=1)]
    df['Effort_Score'] = df['Effort'].map(effort_score)
    
    # Shuffle de taken voor meer variatie
    df = df.sample(frac=1).reset_index(drop=True)

    planning = {persoon: [] for persoon in personen}
    reeds_toegewezen = set()

    # Shuffle ook de personen volgorde
    personen_shuffled = personen.copy()
    random.shuffle(personen_shuffled)

    for persoon in personen_shuffled:
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

def wis_dag_uit_json_en_cache(dag_key):
    """Verbeterde functie die ook session state reset"""
    db = load_db()
    
    if dag_key in db:
        db.pop(dag_key)
    
    save_db(db)
    
    # Reset session state
    st.session_state.db = db
    
    # Clear ook de Google Sheets cache zodat nieuwe data wordt geladen
    st.cache_data.clear()
    
    # Reset een planning counter voor extra randomness
    if 'planning_counter' not in st.session_state:
        st.session_state.planning_counter = 0
    st.session_state.planning_counter += 1
    
    return dag_key

def save_planning_change(dag_key, field, new_value):
    """Helper functie om wijzigingen direct op te slaan"""
    if dag_key in st.session_state.db:
        st.session_state.db[dag_key][field] = new_value
        save_db(st.session_state.db)

def hergenereer_dag(dag, data, taak_planning_week):
    dag_key = dag.strftime("%Y-%m-%d")
    nieuwe_planning = generate_daily_planning_with_randomness(
        dag, data, taak_planning_week, seed_offset=random.randint(0, 99999)
    )
    st.session_state.db[dag_key] = nieuwe_planning
    save_db(st.session_state.db)
    st.success(f"🔄 Dag {dag.strftime('%A %d/%m')} hergegenereerd.")
    st.rerun()

# UI
st.markdown("""
<style>
html, body, [class*="css"] { font-size: 14px !important; }
h1, h2, h3, h4, h5, h6 { font-size: 22px !important; }
</style>
""", unsafe_allow_html=True)

st.title("💖Olieboom Weekplanner💖")
if "db" not in st.session_state:
    st.session_state.db = load_db()

data = load_all_sheets()
db = st.session_state.db

if st.checkbox("🔍 Debug informatie tonen"):
    st.write("**Session State Info:**")
    st.write(f"Planning counter: {getattr(st.session_state, 'planning_counter', 0)}")
    st.write(f"Database entries: {len(st.session_state.db)}")
    st.write("**Huidige planning keys:**")
    for key in sorted(st.session_state.db.keys()):
        st.write(f"- {key}")

col1, col2 = st.columns(2)
with col1:
    start_dag = st.date_input("Startdatum weekplanning", value=datetime.today())

if data:
    planning = []
    sheet_taken = get_gsheet_client().open("Gezinsplanning").worksheet("Taken")

    personen = ["cedric", "lise"]
    
    # Gebruik counter voor extra randomness bij taken verdeling
    shuffle_seed = getattr(st.session_state, 'planning_counter', 0) * 1000 + start_dag.toordinal()
    taak_planning_week = verdeel_taken_per_persoon_with_shuffle(
        data['taken'], 
        start_dag, 
        personen, 
        shuffle_seed=shuffle_seed
    )
    
    for i in range(7):
        dag = start_dag + timedelta(days=i)
        dag_key = dag.strftime("%Y-%m-%d")
        
        if dag_key in db:
            dag_planning = db[dag_key]
        else:
            # Gebruik counter voor extra randomness
            seed_offset = getattr(st.session_state, 'planning_counter', 0) * 100
            dag_planning = generate_daily_planning_with_randomness(
                dag, 
                data, 
                taak_planning_week, 
                seed_offset=seed_offset
            )
            db[dag_key] = dag_planning
        
        planning.append(dag_planning)

    save_db(db)
    st.session_state.db = db

    st.subheader("📅 Weekoverzicht")
    st.markdown("📝 **Je wijzigingen hieronder worden automatisch opgeslagen**")
    cols = st.columns(7)

    for i, dag_planning in enumerate(planning):
        with cols[i]:
            dag_key = (start_dag + timedelta(days=i)).strftime("%Y-%m-%d")
            st.markdown(f"**{dag_planning['dag_kort']}**")
        
            # Eten selectie met optie om nieuw gerecht toe te voegen
            eten_opties = data['eten'].iloc[:,0].tolist()
            eten_opties.append("➕ Nieuw gerecht toevoegen...")

            current_eten = dag_planning['eten']
            if current_eten not in eten_opties:
                eten_opties.insert(0, current_eten)

            selected_eten = st.selectbox(
                f"🍽️ Eten", 
                options=eten_opties, 
                index=eten_opties.index(current_eten),
                key=f"eten_{i}"
            )

            if selected_eten == "➕ Nieuw gerecht toevoegen...":
                nieuw_eten = st.text_input("Nieuw gerecht invullen:", key=f"nieuw_eten_{i}")
                if st.button("✅ Toevoegen", key=f"toevoegen_eten_{i}") and nieuw_eten:
                    if nieuw_eten not in eten_opties:
                        add_to_sheet("Eten", nieuw_eten)
                        st.success(f"'{nieuw_eten}' toegevoegd aan gerechten.")
                        data['eten'] = load_all_sheets()['eten']  # reload data
                        save_planning_change(dag_key, 'eten', nieuw_eten)
                        st.rerun()
                    else:
                        st.info("ℹ️ Dit gerecht bestaat al.")
            elif selected_eten != current_eten:
                save_planning_change(dag_key, 'eten', selected_eten)
            
            st.markdown("---")
            
            # Taak logica (ongewijzigd)
            if dag_planning['taak_lise']:
                st.markdown(f"🧹 **Taak Lise:**")
                taak_naam = dag_planning['taak_lise']
                taak_afgevinkt = st.checkbox(taak_naam, key=f"taak_lise_chk_{i}")
                
                if taak_afgevinkt:
                    idx = data['taken'][data['taken']['Taak'] == taak_naam].index[0]
                    if data['taken'].iloc[idx]['Frequentie'] == 'Eenmalig':
                        verwijder_taak(sheet_taken, taak_naam)
                        data['taken'] = data['taken'].drop(index=idx).reset_index(drop=True)
                        st.success(f"🗑️ '{taak_naam}' verwijderd (eenmalige taak)")
                    else:
                        data['taken'].at[idx, 'Laatst_Uitgevoerd'] = str(datetime.today())
                        kolomindex = data['taken'].columns.get_loc("Laatst_Uitgevoerd")
                        sheet_taken.update_cell(idx + 2, kolomindex, str(datetime.today().date()))
                        st.success(f"✅ '{taak_naam}' gemarkeerd als uitgevoerd op {datetime.today()}")
                        
            if dag_planning['taak_cedric']:
                st.markdown(f"🧹 **Taak Cedric:**")
                taak_naam = dag_planning['taak_cedric']
                taak_afgevinkt = st.checkbox(taak_naam, key=f"taak_cedric_chk_{i}")
                
                if taak_afgevinkt:
                    idx = data['taken'][data['taken']['Taak'] == taak_naam].index[0]
                    if data['taken'].iloc[idx]['Frequentie'] == 'Eenmalig':
                        verwijder_taak(sheet_taken, taak_naam)
                        data['taken'] = data['taken'].drop(index=idx).reset_index(drop=True)
                        st.success(f"🗑️ '{taak_naam}' verwijderd (eenmalige taak)")
                    else:
                        data['taken'].at[idx, 'Laatst_Uitgevoerd'] = str(datetime.today())
                        kolomindex = data['taken'].columns.get_loc("Laatst_Uitgevoerd")
                        sheet_taken.update_cell(idx + 2, kolomindex, str(datetime.today().date()))
                        st.success(f"✅ '{taak_naam}' gemarkeerd als uitgevoerd op {datetime.today()}")
                        
            if dag_planning['taak_lise'] == "" and dag_planning['taak_cedric'] == "":
                st.markdown(f"🧹 **Geen taak vandaag**")
                st.markdown("")
                st.markdown("")
        
            st.markdown("---")
            
            # Cedric
            current_cedric = dag_planning['cedric']
            selected_cedric = st.selectbox(
                f"👨‍🦱 Cedric", 
                options=data['act_cedric']['Activiteiten'].tolist(), 
                index=data['act_cedric']['Activiteiten'].tolist().index(current_cedric), 
                key=f"cedric_{i}"
            )
            if selected_cedric != current_cedric:
                save_planning_change(dag_key, 'cedric', selected_cedric)
            
            # Lise
            current_lise = dag_planning['lise']
            selected_lise = st.selectbox(
                f"👩‍🦰 Lise", 
                options=data['act_lise']['Activiteiten'].tolist(), 
                index=data['act_lise']['Activiteiten'].tolist().index(current_lise), 
                key=f"lise_{i}"
            )
            if selected_lise != current_lise:
                save_planning_change(dag_key, 'lise', selected_lise)
            
            # Kids
            current_kids = dag_planning['kids']
            selected_kids = st.selectbox(
                f"👦 Kids", 
                options=data['act_kids']['Activiteiten'].tolist(), 
                index=data['act_kids']['Activiteiten'].tolist().index(current_kids), 
                key=f"kids_{i}"
            )
            if selected_kids != current_kids:
                save_planning_change(dag_key, 'lars', selected_kids)

             # Activiteiten met helper functie
            # Iedereen
            current_all = dag_planning['all']
            selected_all = st.selectbox(
                f"👨‍👩‍👦‍👦 Iedereen", 
                options=data['act_cedric']['Activiteiten'].tolist(), 
                index=data['act_cedric']['Activiteiten'].tolist().index(current_all), 
                key=f"all_{i}"
            )
            if selected_all != current_all:
                save_planning_change(dag_key, 'all', selected_all)
            
            st.markdown("---")

            if st.button(f"🔄 Nieuwe dagplanning", key=f"regen_{i}"):
                st.session_state[f"regen_{dag_key}"] = True
                with st.spinner("Bezig met genereren van nieuwe planning..."):
                    # Wis oude planning en reset cache
                    verwijderde_dag = wis_dag_uit_json_en_cache(dag_key)
                    
                    st.success(f"✅ Nieuwe planning gegenereerd!")
                    if verwijderde_dag:
                        st.info(f"🗑️ Verwijderde planning voor: {verwijderde_dag}")
                    else:
                        st.info("ℹ️ Geen bestaande planning gevonden, nieuwe planning wordt gegenereerd")
        
                st.rerun()
                st.session_state[f"regen_{dag_key}"] = False

    st.subheader("➕ Voeg nieuwe input toe")
    col3, col4, col5, col6 = st.columns(4)
    with col3:
        nieuw_eten = st.text_input("Nieuw gerecht")
        if st.button("➕ Toevoegen aan Eten") and nieuw_eten:
            if nieuw_eten not in data['eten'].iloc[:,0].tolist():
                add_to_sheet("Eten", nieuw_eten)
            else:
                st.info("ℹ️ Dit gerecht bestaat al.")
    with col4:
        nieuwe_taak = st.text_input("Nieuwe taak")
        frequentie = st.selectbox("Frequentie:", ["Wekelijks", "Maandelijks", "Jaarlijks", "Half jaarlijks", "Om de 5 jaar"])
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