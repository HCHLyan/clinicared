# Optimización de costos AWS (US 06)

> Los montos en USD se deben calcular con **AWS Pricing Calculator** para la región y la fecha de
> compra. Este documento fija el dimensionamiento y las palancas; no contiene precios.

## Dimensionamiento base (producción)

| Recurso | Configuración | Justificación |
|---|---|---|
| API (Fargate ARM64) | 2–4 tareas × 0,5 vCPU / 1 GiB, autoescalado por CPU 60 % | p95 ≤ 400 ms; horario operativo de 07:00 a 20:00 |
| Workers de recordatorios | 1–3 tareas × 0,25 vCPU / 0,5 GiB, escalado por `QueueDepth` | Carga en ráfagas cada 10 min |
| Worker de lista de espera y outbox relay | 1 tarea × 0,25 vCPU / 0,5 GiB cada uno | Volumen bajo |
| Amazon MQ RabbitMQ | `mq.m5.large` multi-AZ en producción; `mq.t3.micro` single en dev | Es el componente fijo más caro: evaluar 1 broker compartido por entorno no productivo |
| EMR Serverless | ARM64, auto-stop a los 10 min, tope de 32 vCPU | Pago por uso, con ~1 job diario |
| S3 lake | Lifecycle: landing a IA a los 30 días y a Glacier IR a los 180; cuarentena expira a los 90 | Datos crudos casi no se releen |
| CloudWatch Logs | Retención de 90 días; métricas vía EMF (sin `PutMetricData`) | Evita llamadas por métrica |

## Palancas aplicadas
1. **Graviton (ARM64)** en Fargate y EMR Serverless.
2. **Mensajería segmentada por riesgo (RN-03):** es la palanca de mayor impacto. LOW no recibe
   mensajes y MEDIUM recibe 1. El ahorro frente a un envío masivo se cuantifica con
   `python -m src.analytics.historical_simulation --from-db` (US 06 T3).
3. **Parquet + partition projection:** Athena cobra por datos escaneados. Las consultas filtradas
   por `year_month` leen solo esas particiones.
4. **Sin NAT para tráfico AWS:** usar *VPC endpoints* de S3 (gateway, sin costo), Secrets Manager,
   ECR y Logs. Verificar que existan en la base de US 03.
5. **Escalado a cero fuera de horario** en entornos no productivos: *scheduled scaling* de ECS de
   20:00 a 07:00.
6. **Savings Plans (Compute)** para la base estable de Fargate, una vez haya 3 meses de uso real.
7. **ECR lifecycle** de 30 imágenes.

## Verificación mensual
* Cost Explorer agrupado por la etiqueta `Project=clinicared-citas` y por servicio.
* Presupuesto AWS Budgets con alerta al 80 % y 100 % (crear en la cuenta de facturación).
* KPI unitario: **costo de mensajería por inasistencia evitada** (consulta PN-04 más la
  simulación), y costo de plataforma por cita atendida.
