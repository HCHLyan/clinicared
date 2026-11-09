import os
import random
import psycopg2
from faker import Faker
from dotenv import load_dotenv

# 1. Configuración de Reproducibilidad y Localización
load_dotenv()
fake = Faker('es_CO')  # Genera datos con formato colombiano (cédulas, teléfonos)
Faker.seed(42)         # Semilla fija: garantiza que siempre se generen los mismos datos
random.seed(42)

# 2. Configuración de Volumen y Defectos (Parametrizable)
NUM_PATIENTS = 50
NUM_PROFESSIONALS = 10
NUM_APPOINTMENTS = 100
DEFECT_RATE = 0.0     # 15% de los pacientes tendrán un email nulo a propósito (Data Quality)

def get_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("DB_NAME", "clinicared_db"),
        user=os.getenv("DB_USER", "postgres"),
        password=os.environ["DB_PASSWORD"],  # sin valor por defecto: nunca credenciales en código
    )

def run_seed():
    conn = get_connection()
    cur = conn.cursor()

    print("Limpiando base de datos para garantizar idempotencia...")
    # CASCADE asegura que se borren dependencias, RESTART IDENTITY reinicia los IDs a 1
    cur.execute("""
        TRUNCATE TABLE locations, specialties, professionals, patients, 
        professional_specialties, appointments, attendances, messages RESTART IDENTITY CASCADE;
    """)

    print("Insertando Sedes y Especialidades (Catálogos fijos)...")
    locations = [
        ('Sede Norte Barranquilla', 'Cra 51B # 82-100', 'Barranquilla'),
        ('Sede Viva Malambo', 'Centro Comercial Viva', 'Malambo')
    ]
    for loc in locations:
        cur.execute("INSERT INTO locations (name, address, city) VALUES (%s, %s, %s)", loc)

    specialties = ['Cardiología', 'Pediatría', 'Medicina General', 'Dermatología']
    for spec in specialties:
        cur.execute("INSERT INTO specialties (name, description) VALUES (%s, %s)", (spec, f"Atención en {spec}"))

    print(f"Insertando {NUM_PROFESSIONALS} Profesionales...")
    for _ in range(NUM_PROFESSIONALS):
        cur.execute("""
            INSERT INTO professionals (first_name, last_name, email, medical_license)
            VALUES (%s, %s, %s, %s)
        """, (
            fake.first_name(), fake.last_name(), fake.unique.company_email(), fake.unique.bothify(text='RM-#####')
        ))

    print(f"Insertando {NUM_PATIENTS} Pacientes (Con inyección de defectos del {DEFECT_RATE*100}%)...")
    for _ in range(NUM_PATIENTS):
        # Inyección de defecto: Si el random cae en la probabilidad, el email será NULL
        email = None if random.random() < DEFECT_RATE else fake.unique.email()
        
        cur.execute("""
            INSERT INTO patients (national_id, first_name, last_name, email, phone, date_of_birth)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (
            fake.unique.ssn(), fake.first_name(), fake.last_name(), 
            email, fake.phone_number(), fake.date_of_birth(minimum_age=18, maximum_age=80)
        ))

    print(f"Insertando {NUM_APPOINTMENTS} Citas aleatorias...")
    for _ in range(NUM_APPOINTMENTS):
        cur.execute("""
            INSERT INTO appointments (patient_id, professional_id, location_id, specialty_id, scheduled_datetime)
            VALUES (
                (SELECT id FROM patients ORDER BY RANDOM() LIMIT 1),
                (SELECT id FROM professionals ORDER BY RANDOM() LIMIT 1),
                (SELECT id FROM locations ORDER BY RANDOM() LIMIT 1),
                (SELECT id FROM specialties ORDER BY RANDOM() LIMIT 1),
                %s
            )
        """, (fake.future_datetime(end_date='+30d'),))

    conn.commit()
    cur.close()
    conn.close()
    print("✅ Poblado de datos completado exitosamente.")

if __name__ == '__main__':
    run_seed()