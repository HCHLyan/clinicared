# ADR 001: Uso de PostgreSQL Contenerizado en Docker para la Capa Transaccional

* **Estado:** Aceptado
* **Fecha:** 2026-10-15

## Contexto
El proyecto ClínicaRed requiere una base de datos relacional robusta que garantice integridad referencial entre sedes, profesionales, pacientes y citas médicas. Asimismo, el equipo de desarrollo trabaja en entornos híbridos (Windows / WSL2 Ubuntu), lo que genera fricción por conflictos de puertos locales, codificación de caracteres y diferencias en la instalación nativa del motor.

## Decisión
Adoptar **PostgreSQL 15 (imagen `postgres:15-alpine`)** ejecutado mediante **Docker Compose**, automatizando el aprovisionamiento del esquema inicial a través del volumen `/docker-entrypoint-initdb.d/`.

## Consecuencias
* **Positivas:**
  * Paridad total del entorno de desarrollo sin depender de instalaciones locales en el sistema operativo anfitrión.
  * Inicialización automática del esquema (`01_init_schema.sql`) al levantar el contenedor.
  * Cumplimiento estricto de propiedades ACID e integridad referencial (Foreign Keys y restricciones `NOT NULL`).
* **Negativas / Riesgos:**
  * Requiere que Docker esté activo en el entorno de trabajo.
  * Necesidad de gestionar volúmenes persistentes (`pgdata`) para evitar pérdida de datos al reiniciar contenedores.