# ADR 009: Seudonimización (RN-09) y auditoría inmutable (RN-10)

* **Estado:** Aceptado
* **Fecha:** 2026-11-04

## RN-09
* `patient_key = sha256(pepper || "|" || documento_normalizado)`. Se implementa igual en Python
  (`src/domain/privacy.py`) y en Spark (`sha2`). La paridad está verificada en
  `tests/unit/test_spark_parity.py`.
* **¿Por qué no HMAC?** HMAC-SHA256 es la construcción canónica, pero Spark no tiene `hmac`
  nativo: usarlo exige una UDF Python, que serializa fila a fila entre la JVM y Python y es mucho más lenta que una función nativa.
  Para seudonimización el riesgo relevante es el ataque de diccionario sobre el espacio de
  documentos, que es pequeño, y eso lo mitiga el *pepper* secreto de 64 caracteres guardado en
  Secrets Manager con KMS. La debilidad de *length extension* de SHA-256 no aplica, porque nadie
  necesita falsificar seudónimos.
* **Minimización en origen:** el rol `clinicared_etl` solo tiene `SELECT` sobre las columnas `id`,
  `national_id`, `date_of_birth` y `sex` de `patients` (GRANT por columna). Los diagnósticos crudos
  de F1 se reducen a un booleano en Silver y nunca se escriben en ninguna zona analítica. El
  control QC-03 verifica que no haya columnas sensibles en Gold.
* **Generalización:** la edad se publica en bandas, nunca como fecha de nacimiento.
* Los mensajes a pacientes no incluyen especialidad ni profesional (un SMS en pantalla bloqueada
  puede revelar salud).

## RN-10
* `AuditMiddleware` registra todo acceso a `/patients`, `/appointments` y `/waitlist`, con actor,
  acción, recurso, `request_id`, IP y código HTTP.
* **Fail-closed:** si no se puede auditar, la respuesta es 503 y no se entregan datos.
* **Inmutabilidad:** triggers que bloquean `UPDATE`, `DELETE` y `TRUNCATE`, `REVOKE` a los roles de
  la aplicación, y encadenamiento de hashes (`prev_hash`, `row_hash`) verificable con
  `audit_log_verify_chain()`.
* El actor viene de `x-amzn-oidc-identity`, que inyecta el ALB autenticado (OIDC) y que el ALB
  elimina si llega desde el cliente.

## Riesgos residuales
* En escrituras, la auditoría ocurre después del commit del negocio. Si falla, la operación quedó
  hecha pero el cliente recibe 503 (el reintento es idempotente). Mover la auditoría a la misma
  transacción requiere pasar la conexión al middleware; está planificado.
* Un superusuario de BD puede desactivar triggers. Mitigación: exportar `audit_log` a S3 con Object
  Lock (modo *compliance*). Está en el backlog de US 06.
