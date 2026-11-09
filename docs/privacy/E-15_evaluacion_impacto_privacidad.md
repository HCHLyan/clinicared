# E-15 · Evaluación de Impacto en Privacidad (EIPD): ClínicaRed Citas

**Marco normativo de referencia:** Ley 1581 de 2012 y Decreto 1377 de 2013 (Habeas Data,
Colombia), Ley 23 de 1981 y Resolución 1995 de 1999 (historia clínica). Esta evaluación debe ser
revisada y firmada por el Oficial de Protección de Datos y por el área jurídica. Este documento es
el insumo técnico.

## 1. Descripción del tratamiento
| Elemento | Detalle |
|---|---|
| Finalidad | Recordar citas, reducir inasistencias, reasignar cupos liberados y analizar la operación |
| Titulares | ~400 000 pacientes (incluye menores de edad) y sus acudientes |
| Datos | Identificación, contacto, fecha de nacimiento, citas, asistencia, **condiciones crónicas (dato sensible)** |
| Encargados | AWS (infraestructura, región us-east-1) y proveedor de mensajería (SMS/WhatsApp) |
| Transferencia internacional | Sí (AWS us-east-1). Requiere declaración de conformidad o cláusulas contractuales |

## 2. Flujos y controles

| Flujo | Datos | Control técnico | Evidencia |
|---|---|---|---|
| API a OLTP | Identificación, contacto | TLS (ALB), RDS cifrado, roles de mínimo privilegio | `02_us04_schema.sql` (GRANT/REVOKE) |
| Accesos a datos de paciente | Todos | Auditoría inmutable, encadenada y fail-closed (RN-10) | `AuditMiddleware`, `audit_log_verify_chain()` |
| OLTP a mensajería | Teléfono, fecha/hora, sede | Minimización: sin especialidad ni profesional | `services/templates.py`, CA-09 |
| Menores | Contacto del acudiente | Solo al acudiente registrado (RN-11) | CA-09, OC-04 |
| OLTP/F1 a lake | Documento, fecha de nacimiento, sexo, diagnóstico | GRANT por columna al rol ETL; diagnóstico reducido a booleano en Silver; Gold seudonimizado | RN-09, QC-03, `test_spark_parity.py` |
| Lake | Gold | S3 SSE-KMS (CMK), bloqueo público, solo TLS, cuarentena expira a 90 días | `lake.tf` |
| Secretos | Credenciales, pepper | Secrets Manager + KMS; sin secretos en el código (gitleaks y Trivy en CI) | `security.tf`, `ci.yml` |
| Logs | Metadatos de solicitudes | Sin cuerpos de solicitud ni datos de paciente; retención de 90 días, KMS | `logging_config.py`, `observability.tf` |

## 3. Matriz de riesgos (probabilidad × impacto, de 1 a 5)

| # | Riesgo | P | I | Inherente | Controles | Residual |
|---|---|---|---|---|---|---|
| 1 | Reidentificación en Gold por cruce de cuasi-identificadores (banda de edad, sexo, ciudad, sede) | 3 | 4 | 12 | Bandas de edad; sin fechas de nacimiento; acceso a Gold solo por rol BI | 6. Aplicar k-anonimato ≥ 5 en reportes públicos |
| 2 | Fuga del *pepper* que permita revertir seudónimos por diccionario | 2 | 5 | 10 | KMS, acceso solo del rol EMR, rotación con plan de re-keying | 4 |
| 3 | Mensaje a un menor o a un tercero | 3 | 4 | 12 | RN-11, sin envío si falta acudiente, OC-04 | 3 |
| 4 | Revelación de salud por el contenido del SMS | 3 | 3 | 9 | Plantillas sin especialidad | 2 |
| 5 | Mensajes duplicados o fuera de horario (queja o reputación) | 3 | 2 | 6 | ADR 005, RN-04 al planificar y al enviar, OC-02 y OC-03 | 1 |
| 6 | Acceso indebido de un empleado | 3 | 4 | 12 | Auditoría inmutable, revisión mensual con `clinicared_auditor` | 6. Implementar alertas de patrones anómalos |
| 7 | Credencial histórica en Git | 4 | 4 | 16 | Rotar y purgar el historial (R-04 del runbook) | **Abierto hasta la rotación** |
| 8 | API sin autenticación propia (depende del ALB OIDC) | 3 | 5 | 15 | ALB con OIDC obligatorio en producción; simuladores deshabilitados en producción | 6. Agregar validación JWT en la aplicación (backlog) |

## 4. Derechos de los titulares
* **Acceso y rectificación:** por los endpoints de pacientes (auditados).
* **Supresión:** en OLTP con anonimización lógica (las citas se conservan por obligación de
  historia clínica). En Gold, el seudónimo no se puede vincular sin el pepper; la siguiente carga
  completa elimina al paciente de `dim_patient`.
* **Oposición a recordatorios:** pendiente agregar `patients.messaging_opt_out` y respetarlo en el
  planificador (backlog; es necesario antes del lanzamiento).

## 5. Conclusión técnica
El tratamiento es viable con los controles implementados. Se deben cerrar antes de salir a
producción los riesgos 7 (credencial en Git) y 8 (autenticación), además del *opt-out* de
mensajería.
