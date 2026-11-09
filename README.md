# 🏥 ClínicaRed - Backend & Data Platform

Plataforma backend y de ingeniería de datos para la gestión de citas médicas, recordatorios
segmentados por riesgo, lista de espera, control de asistencia y analítica (22 sedes, ~400 000
pacientes).

## 🚀 Prerrequisitos
- Docker y Docker Compose
- Python 3.13 (Airflow 3.3.2, PySpark 4.2.0, Java 17)
- (Opcional) Java 17 para ejecutar Spark localmente

## 📁 Estructura

```text
├── src/
│   ├── domain/            # Reglas RN-01..RN-11 puras (sin E/S) + idempotencia + privacidad
│   ├── services/          # Casos de uso (despacho, webhooks, citas, lista de espera, riesgo, admisiones)
│   ├── repositories/      # Adaptadores PostgreSQL
│   ├── messaging/         # Topología RabbitMQ, publicador confiable, consumidor, outbox relay
│   ├── gateways/          # Proveedor de mensajería (HTTP con Idempotency-Key)
│   ├── workers/           # Consumidores (ECS)
│   ├── api/               # FastAPI: rutas, middleware de auditoría y métricas
│   ├── analytics/         # Simulación histórica (US 06 T3)
│   └── seeders/           # Datos sintéticos y de referencia (F5/F7)
├── dags/                  # Airflow: admisiones, riesgo, orquestación, ETL Spark
├── spark/jobs/            # PySpark: cleanse (Silver) y build_gold (estrella)
├── spark/benchmarks/      # Benchmark baseline vs. optimizado
├── sql/analytics/         # DDL Athena, preguntas de negocio y controles de calidad
├── infra/                 # Terraform US 04-06 + definiciones de tareas ECS
├── tests/                 # unit (CA-01..CA-10), integration (PostgreSQL), dags
├── perf/                  # Pruebas de carga (Locust)
├── scripts/               # Protección de ramas, inyección de fallas, herramienta de DLQ
└── docs/                  # ADR, runbook, EIPD E-15, modelo dimensional, FinOps, demo
```

## ⚙️ Puesta en marcha local

```bash
cp .env.example .env            # completar valores locales (nunca commitear .env)
docker compose up -d            # Postgres (esquema 01+02), RabbitMQ, API, workers, relay, Airflow
pip install -r requirements-dev.txt
python src/seeders/generate_seed.py
python -m src.seeders.seed_reference_data --years 2026 2027
```

* API: http://localhost:8000/docs · RabbitMQ: http://localhost:15672 · Airflow: http://localhost:8080

Para crear manualmente el usuario administrador de Airflow en el entorno local:

```bash
docker compose exec airflow-apiserver airflow users create --username admin --firstname Admin --lastname Admin --role Admin --email admin@example.org --password change-me-local-only
```

Usa esta contraseña solo en local y cámbiala antes de cualquier despliegue. Si el usuario `admin` ya existe, no es necesario volver a ejecutar el comando.

## ✅ Pruebas

```bash
pytest tests/unit                                    # CA-01..CA-10, sin infraestructura
INTEGRATION_DATABASE_URL=postgresql+psycopg2://user:pass@localhost:5432/clinicared_test \
  pytest -m integration tests/integration            # BD desechable
pytest tests/dags                                    # requiere Airflow
```

Trazabilidad: [docs/testing/acceptance_criteria.md](docs/testing/acceptance_criteria.md)

## 📐 Documentación
- Arquitectura: [v1](docs/architecture/architecture_v1.md) · [v2](docs/architecture/architecture_v2.md)
- ADR: [001](docs/adr/001-motor-base-datos-y-docker.md) · [002](docs/adr/002-estrategia-seeding-idempotente-calidad.md) ·
  [003](docs/adr/003-extraccion-incremental-watermark.md) · [004 RabbitMQ](docs/adr/004-mensajeria-rabbitmq-prioridad-reintentos.md) ·
  [005 Idempotencia](docs/adr/005-idempotencia-y-resiliencia.md) · [006 Supuestos de negocio](docs/adr/006-interpretacion-reglas-y-supuestos.md) ·
  [007 Airflow](docs/adr/007-orquestacion-airflow.md) · [008 Spark](docs/adr/008-lakehouse-spark-optimizacion.md) ·
  [009 Privacidad/Auditoría](docs/adr/009-seudonimizacion-y-auditoria.md) · [010 CI/CD](docs/adr/010-cicd-y-despliegue-aws.md)
- Fuentes F1–F7: [docs/data_sources.md](docs/data_sources.md)
- Modelo dimensional, diccionario y linaje: [docs/analytics/star_schema.md](docs/analytics/star_schema.md)
- Runbook: [docs/runbook/operational_runbook.md](docs/runbook/operational_runbook.md)
- EIPD (E-15): [docs/privacy/E-15_evaluacion_impacto_privacidad.md](docs/privacy/E-15_evaluacion_impacto_privacidad.md)
- Rendimiento: [docs/performance/benchmarks.md](docs/performance/benchmarks.md) · FinOps: [docs/finops/aws_cost_optimization.md](docs/finops/aws_cost_optimization.md)
- Demo ejecutiva: [docs/demo/executive_demo.md](docs/demo/executive_demo.md)

## 🛠️ Flujo Git
`<tipo>/[USxx-]<nombre>` → `develop` → `main` (solo `develop` o `hotfix/*`). La CI lo valida y la
protección de ramas se configura con `scripts/configure_branch_protection.sh`.
Conventional Commits: `feat:`, `fix:`, `docs:`, `chore:`, `refactor:`, `test:`, `perf:`.

## ⚠️ Pendientes conocidos
- **Rotar la contraseña de BD que estuvo en el código** (`config.py`, `docker-compose.yml`) y purgar
  el historial Git. Gitleaks la reportará mientras exista en el historial.
- Definiciones de tareas ECS para Airflow (webserver, scheduler y workers) en AWS.
- Autenticación JWT en la aplicación (hoy depende del ALB con OIDC).
- *Opt-out* de mensajería por paciente (requisito de la EIPD).
- Resultados de los benchmarks (ejecutar los scripts de `docs/performance/benchmarks.md`).
