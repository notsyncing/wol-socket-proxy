import asyncio
from typing import Any

from redfish.rest.v1 import HttpClient


async def perform_ipmi_action(ipmi_client: HttpClient, url: str, body: dict[str, Any]) -> None:
    r = await asyncio.to_thread(ipmi_client.post, url, body=body)

    if r.is_processing:
        task = r.monitor(ipmi_client)

        while task.is_processing:
            retry_time = task.retry_after
            task_status = task.dict["TaskState"]

            if task_status not in {"Running", "Completed"}:
                raise ValueError(f"Bad task status from IPMI: {task_status}")

            await asyncio.sleep(retry_time or 5)
            task = r.monitor(ipmi_client)
