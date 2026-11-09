# Arquitectura v2: ClínicaRed Citas (US 04–06)

```mermaid
flowchart LR
  subgraph Fuentes
    F1[F1 Pacientes] --- F3[F3 Admisiones] --- F5[F5 Festivos] --- F6[F6 Clima] --- F7[F7 Sobrecupo]
  end
  subgraph AWS_VPC["AWS VPC (subredes privadas)"]
    ALB[ALB + OIDC] --> API[FastAPI ECS<br/>RN-05/06/07, auditoría RN-10]
    API --> PG[(RDS PostgreSQL<br/>messages, outbox, audit_log)]
    API -->|outbox| RELAY[Outbox relay]
    RELAY --> MQ{{Amazon MQ RabbitMQ<br/>prioridad · retry TTL · DLQ}}
    AF[Airflow CeleryExecutor<br/>broker RabbitMQ vhost airflow] -->|RN-01, RN-02/03/04/11, RN-05| PG
    AF -->|encola vencidos| MQ
    MQ --> W1[Worker recordatorios]
    MQ --> W2[Worker lista de espera]
    W1 & W2 -->|claim atómico| PG
    W1 & W2 -->|Idempotency-Key| PROV[Proveedor SMS/WhatsApp]
    PROV -->|webhooks firmados| API
    AF --> EMR[EMR Serverless Spark]
    EMR -->|JDBC rol ETL| PG
    EMR --> S3[(S3 lake KMS<br/>landing/silver/gold)]
  end
  F3 & F5 & F6 & F1 & F7 --> S3
  S3 --> ATH[Athena] --> PBI[Power BI]
  API & W1 & W2 & RELAY -->|logs JSON + EMF| CW[CloudWatch<br/>alarmas → SNS]
```

| Capa | Paquete | Responsabilidad |
|---|---|---|
| Dominio | `src/domain` | Reglas RN-01..RN-11 puras (sin E/S), llaves de idempotencia, calendario, privacidad |
| Servicios | `src/services` | Casos de uso: despacho, webhooks, citas, lista de espera, riesgo, admisiones y orquestación |
| Adaptadores | `src/repositories`, `src/gateways`, `src/messaging` | PostgreSQL, proveedor HTTP, RabbitMQ |
| Entradas | `src/api`, `src/workers`, `dags/`, `spark/jobs` | HTTP, consumidores, DAGs y jobs |

Decisiones: ADR 004 a ADR 010.
