# ADR 002: Estrategia de Poblado de Datos Idempotente e Inyección Controlada de Defectos

* **Estado:** Aceptado
* **Fecha:** 2026-10-15

## Contexto
Para desarrollar y validar tanto el backend como los pipelines de datos de ClínicaRed, es necesario contar con datos sintéticos realistas (pacientes, médicos, citas) sin exponer información médica privada (PII). Además, se requiere probar cómo responde la arquitectura ante datos corruptos o incompletos.

## Decisión
Implementar un generador de datos en Python (`generate_seed.py`) utilizando `Faker` y `psycopg2` bajo dos principios de diseño:
1. **Idempotencia:** Limpieza controlada (`TRUNCATE ... RESTART IDENTITY CASCADE`) previa a cada inserción para garantizar que múltiples ejecuciones produzcan un estado consistente y predecible.
2. **Inyección Parametrizable de Defectos:** Inclusión de una tasa configurable de anomalías para evaluar el comportamiento de las restricciones del esquema transaccional (`NOT NULL`, `UNIQUE`) frente a las validaciones del pipeline analítico.

## Consecuencias
* **Positivas:**
  * Cualquier desarrollador puede recrear el estado exacto de la base de datos con un solo comando.
  * Permite alternar entre un modo de validación estricta en origen (0% defectos) y pruebas de resiliencia de datos.
* **Negativas / Riesgos:**
  * El script de *seeding* es destructivo por diseño, por lo que su ejecución debe restringirse exclusivamente a entornos de desarrollo y pruebas (`DEV`/`QA`).