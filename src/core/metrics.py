"""CloudWatch metrics through the Embedded Metric Format (EMF).

EMF documents written to stdout are converted into CloudWatch metrics by
CloudWatch Logs itself, so services need neither the ``PutMetricData`` IAM
permission nor a synchronous network call on the hot path.
"""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Mapping
from typing import Literal

Unit = Literal["Milliseconds", "Seconds", "Count", "None"]


class MetricsEmitter:
    """Emit EMF metric documents.

    Args:
        namespace: CloudWatch namespace.
        service: Value of the ``Service`` dimension added to every metric.
    """

    def __init__(self, namespace: str, service: str) -> None:
        self._namespace = namespace
        self._service = service

    def emit(
        self,
        name: str,
        value: float,
        unit: Unit = "Count",
        dimensions: Mapping[str, str] | None = None,
        properties: Mapping[str, object] | None = None,
    ) -> None:
        """Write one metric datum.

        Args:
            name: Metric name.
            value: Metric value.
            unit: CloudWatch unit.
            dimensions: Extra dimensions (keep cardinality low).
            properties: Non-dimension context searchable in Logs Insights.
        """
        dims = {"Service": self._service, **(dimensions or {})}
        document: dict[str, object] = {
            "_aws": {
                "Timestamp": int(time.time() * 1000),
                "CloudWatchMetrics": [
                    {
                        "Namespace": self._namespace,
                        "Dimensions": [list(dims.keys())],
                        "Metrics": [{"Name": name, "Unit": unit}],
                    }
                ],
            },
            name: value,
            **dims,
            **(properties or {}),
        }
        sys.stdout.write(json.dumps(document, default=str) + "\n")
        sys.stdout.flush()
