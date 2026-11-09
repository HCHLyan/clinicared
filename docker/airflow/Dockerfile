# Airflow 3.3.2 on Python 3.13 (Debian bookworm base).
# AIRFLOW_VERSION / PYTHON_VERSION drive both the base image and the constraints URL,
# so they can never drift apart.
ARG AIRFLOW_VERSION=3.3.2
ARG PYTHON_VERSION=3.13
FROM apache/airflow:${AIRFLOW_VERSION}-python${PYTHON_VERSION}
ARG AIRFLOW_VERSION
ARG PYTHON_VERSION

USER root
# Java 17 is required by spark-submit (PySpark 4.2 supports Java 17/21; bookworm ships 17).
# The symlink keeps JAVA_HOME valid on both amd64 and arm64 builds.
RUN apt-get update && apt-get install -y --no-install-recommends openjdk-17-jre-headless zip \
    && ln -s "/usr/lib/jvm/java-17-openjdk-$(dpkg --print-architecture)" /usr/lib/jvm/java-17 \
    && apt-get clean && rm -rf /var/lib/apt/lists/*
ENV JAVA_HOME=/usr/lib/jvm/java-17

USER airflow
COPY requirements-airflow.txt requirements-spark.txt /tmp/
RUN pip install --no-cache-dir -r /tmp/requirements-airflow.txt -r /tmp/requirements-spark.txt \
    --constraint "https://raw.githubusercontent.com/apache/airflow/constraints-${AIRFLOW_VERSION}/constraints-${PYTHON_VERSION}.txt" \
    && pip check
