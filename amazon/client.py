import os
import json
import time
import uuid
import httpx
from typing import Dict, Any, Optional

class AmazonDataPortabilityClient:
    def __init__(self):
        self.base_url = os.getenv(
            "AMAZON_DATA_PORTABILITY_BASE_URL",
            "https://api.amazon.com/data-portability"
        ).rstrip("/")
        self.mock_mode = os.getenv("AMAZON_MOCK_MODE", "true").lower() in ("1", "true", "yes")
        self._mock_queries: Dict[str, Dict[str, Any]] = {}

    async def create_query(self, access_token: str, dataset: str = "PHYSICAL_ORDERS") -> Dict[str, Any]:
        """
        Creates a Data Portability query for the specified dataset.
        Amazon's Data Portability workflow is asynchronous.
        """
        if self.mock_mode:
            query_id = f"dp-query-{uuid.uuid4().hex[:12]}-mock"
            self._mock_queries[query_id] = {
                "queryId": query_id,
                "dataset": dataset,
                "status": "IN_PROGRESS",
                "createdAt": time.time(),
                "pollCount": 0
            }
            return {
                "queryId": query_id,
                "status": "IN_PROGRESS",
                "dataset": dataset
            }

        url = f"{self.base_url}/v1/queries"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        body = {
            "dataset": dataset
        }

        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(url, json=body, headers=headers)
            if response.status_code not in (200, 201, 202):
                raise RuntimeError(
                    f"Failed to create Data Portability query: HTTP {response.status_code} - {response.text}"
                )
            return response.json()

    async def get_query_status(self, access_token: str, query_id: str) -> Dict[str, Any]:
        """
        Checks the status of an asynchronous Data Portability query.
        Returns the current query metadata and status (e.g. IN_PROGRESS, COMPLETED, FAILED).
        """
        if self.mock_mode:
            query_state = self._mock_queries.get(query_id)
            if not query_state:
                # Default mock query fallback
                return {
                    "queryId": query_id,
                    "status": "COMPLETED",
                    "dataset": "PHYSICAL_ORDERS",
                    "progress": "100%"
                }

            query_state["pollCount"] += 1
            elapsed = time.time() - query_state["createdAt"]

            # Simulate asynchronous progression across 2-3 poll cycles (or ~2 seconds)
            if elapsed > 1.8 or query_state["pollCount"] >= 2:
                query_state["status"] = "COMPLETED"
                return {
                    "queryId": query_id,
                    "status": "COMPLETED",
                    "dataset": query_state["dataset"],
                    "pollCount": query_state["pollCount"],
                    "durationSeconds": round(elapsed, 2)
                }
            else:
                return {
                    "queryId": query_id,
                    "status": "IN_PROGRESS",
                    "dataset": query_state["dataset"],
                    "pollCount": query_state["pollCount"],
                    "message": "Amazon is processing the physical order dataset..."
                }

        url = f"{self.base_url}/v1/queries/{query_id}"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json"
        }

        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(url, headers=headers)
            if response.status_code != 200:
                raise RuntimeError(
                    f"Failed to get query status: HTTP {response.status_code} - {response.text}"
                )
            return response.json()

    async def fetch_query_results(
        self,
        access_token: str,
        query_id: str,
        download_url: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Fetches the completed records from Amazon Data Portability API.
        Preserves the raw JSON response without alteration.
        """
        if self.mock_mode:
            mock_file_path = os.path.join(os.path.dirname(__file__), "..", "data", "mock_amazon_response.json")
            if os.path.exists(mock_file_path):
                with open(mock_file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    # Update the mock queryId in the response to match active query
                    if isinstance(data, dict) and "meta" in data:
                        data["meta"]["queryId"] = query_id
                    return data
            return {
                "meta": {
                    "queryId": query_id,
                    "status": "COMPLETED",
                    "recordCount": 0
                },
                "data": {"orders": []}
            }

        # If a pre-signed downloadUrl is provided in the query status response
        fetch_target = download_url or f"{self.base_url}/v1/queries/{query_id}/results"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json"
        } if not download_url else {}

        async with httpx.AsyncClient(timeout=45.0) as client:
            response = await client.get(fetch_target, headers=headers)
            if response.status_code != 200:
                raise RuntimeError(
                    f"Failed to fetch Data Portability results: HTTP {response.status_code} - {response.text}"
                )
            return response.json()
