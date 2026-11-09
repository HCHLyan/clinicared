# ADR 010: CI/CD con GitHub Actions y despliegue a ECS Fargate (Graviton)

* **Estado:** Aceptado
* **Fecha:** 2026-11-04
* **Historia:** US 05 – Tareas 1 a 3

## Decisión
* **Flujo Git:** ramas `<tipo>/[USxx-]<nombre>` hacia `develop`, y `develop`/`hotfix/*` hacia
  `main`. Se valida en CI (`branch-policy`) y con la protección de ramas
  (`scripts/configure_branch_protection.sh`): 2 aprobaciones en `main`, 1 en `develop`, revisión
  obligatoria de CODEOWNERS, historial lineal y checks requeridos.
* **CI (PR):** ruff (lint y format), mypy estricto sobre dominio y servicios, gitleaks (historial
  completo), Trivy (secretos, IaC y dependencias), pruebas unitarias con *gate* de cobertura del
  80 %, integración con PostgreSQL real, paridad Spark, validación de DAG (DagBag y convenciones),
  y build más escaneo Trivy de la imagen.
* **CD (`main`):** OIDC hacia un rol IAM restringido a `environment:production`, sin llaves de larga
  vida. La imagen `linux/arm64` se construye con SBOM y *provenance*. Las tareas ECS se renderizan
  y despliegan de forma secuencial (API, workers, relay), con *circuit breaker* y una prueba de
  humo que falla si el p95 de `/health/ready` supera 400 ms.
* **Graviton (ARM64):** reduce aproximadamente un 20 % el costo por vCPU-hora frente a x86 en
  Fargate (ver `docs/finops`). La imagen no tiene dependencias nativas incompatibles.
* **Una sola imagen** para API, workers y relay (el comando cambia por tarea). Así se versionan
  juntos y se elimina la deriva entre componentes.

## Requisitos previos (manuales, una vez)
1. Crear el entorno `production` en GitHub con revisores obligatorios.
2. Secretos `AWS_DEPLOY_ROLE_ARN` y `AWS_ACCOUNT_ID`; variables `RDS_ENDPOINT`,
   `MESSAGING_GATEWAY_URL`, `ARTIFACTS_BUCKET` y `API_BASE_URL`.
3. Ejecutar `terraform apply` en `infra/terraform/us04-06` sobre la base de US 03.
