import os
import json
import time
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from .oauth import AmazonOAuth
from .client import AmazonDataPortabilityClient

class AmazonService:
    def __init__(self):
        self.oauth = AmazonOAuth()
        self.client = AmazonDataPortabilityClient()
        self.responses_dir = os.path.join(os.path.dirname(__file__), "..", "data", "responses")
        os.makedirs(self.responses_dir, exist_ok=True)

        # Explicitly in-memory prototype state (no database)
        self.app_state: Dict[str, Any] = {
            "connected": False,
            "connected_at": None,
            "tokens": None, # Never returned in responses or exposed in frontend
            "oauth_state": None,
            "active_query": None,
            "raw_response": None,
            "saved_file_path": None,
            "retrieval_metrics": None,
            "mock_mode": self.oauth.mock_mode
        }

    def reset_state(self):
        """Resets the in-memory session."""
        self.app_state["connected"] = False
        self.app_state["connected_at"] = None
        self.app_state["tokens"] = None
        self.app_state["oauth_state"] = None
        self.app_state["active_query"] = None
        self.app_state["raw_response"] = None
        self.app_state["saved_file_path"] = None
        self.app_state["retrieval_metrics"] = None

    def start_oauth(self) -> str:
        """Generates the authorization URL and records state."""
        auth_url, state = self.oauth.generate_authorization_url()
        self.app_state["oauth_state"] = state
        return auth_url

    async def handle_oauth_callback(self, code: str, state: Optional[str] = None) -> bool:
        """
        Validates state and exchanges code for tokens.
        Stores tokens transiently in memory.
        """
        expected_state = self.app_state.get("oauth_state")
        if expected_state and state and expected_state != state:
            raise ValueError("OAuth state parameter mismatch (possible CSRF)")

        tokens = await self.oauth.exchange_code_for_tokens(code)
        self.app_state["tokens"] = tokens
        self.app_state["connected"] = True
        self.app_state["connected_at"] = datetime.now(timezone.utc).isoformat()
        return True

    async def create_order_query(self) -> Dict[str, Any]:
        """Creates a new Data Portability query for physical orders."""
        if not self.app_state["connected"]:
            raise RuntimeError("Amazon account is not connected. Please authenticate first.")

        access_token = (self.app_state["tokens"] or {}).get("access_token", "mock_token")
        started_at = time.time()
        
        result = await self.client.create_query(access_token=access_token, dataset="PHYSICAL_ORDERS")
        query_id = result.get("queryId")

        self.app_state["active_query"] = {
            "queryId": query_id,
            "status": result.get("status", "IN_PROGRESS"),
            "dataset": result.get("dataset", "PHYSICAL_ORDERS"),
            "startedAt": started_at,
            "completedAt": None,
            "durationSeconds": None,
            "error": None
        }

        return self.app_state["active_query"]

    async def check_query_status(self) -> Dict[str, Any]:
        """Checks the status of the current active query and retrieves results when ready."""
        active = self.app_state.get("active_query")
        if not active:
            raise RuntimeError("No active Data Portability query exists.")

        if active.get("status") == "COMPLETED" and self.app_state.get("raw_response"):
            return {
                "activeQuery": active,
                "completed": True,
                "retrievalMetrics": self.app_state.get("retrieval_metrics")
            }

        access_token = (self.app_state["tokens"] or {}).get("access_token", "mock_token")
        query_id = active["queryId"]

        status_res = await self.client.get_query_status(access_token=access_token, query_id=query_id)
        current_status = status_res.get("status", "IN_PROGRESS")
        active["status"] = current_status

        if current_status == "COMPLETED":
            # Fetch results
            completed_time = time.time()
            active["completedAt"] = completed_time
            duration = round(completed_time - active["startedAt"], 2)
            active["durationSeconds"] = duration

            download_url = status_res.get("downloadUrl")
            raw_data = await self.client.fetch_query_results(
                access_token=access_token,
                query_id=query_id,
                download_url=download_url
            )

            self.app_state["raw_response"] = raw_data
            
            # Count records
            record_count = 0
            if isinstance(raw_data, dict):
                orders = raw_data.get("data", {}).get("orders") or raw_data.get("orders") or []
                record_count = len(orders) if isinstance(orders, list) else 1

            self.app_state["retrieval_metrics"] = {
                "queryId": query_id,
                "status": "COMPLETED",
                "recordCount": record_count,
                "durationSeconds": duration,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }

            # Save local JSON copy for debugging and inspection
            today_str = datetime.now().strftime("%Y-%m-%d")
            saved_filename = f"amazon-response-{today_str}-{query_id[-8:]}.json"
            saved_path = os.path.join(self.responses_dir, saved_filename)
            try:
                with open(saved_path, "w", encoding="utf-8") as f:
                    json.dump(raw_data, f, indent=2)
                self.app_state["saved_file_path"] = saved_path
            except Exception:
                pass

            return {
                "activeQuery": active,
                "completed": True,
                "retrievalMetrics": self.app_state.get("retrieval_metrics")
            }
        elif current_status in ("FAILED", "CANCELLED"):
            active["error"] = status_res.get("error", "Amazon Data Portability query failed.")
            return {
                "activeQuery": active,
                "completed": True,
                "error": active["error"]
            }

        return {
            "activeQuery": active,
            "completed": False,
            "message": status_res.get("message", "Waiting for Amazon to compile physical orders...")
        }

    def get_results_and_analysis(self) -> Dict[str, Any]:
        """
        Returns the raw Amazon response, parsed order list, and field discovery metadata.
        """
        raw = self.app_state.get("raw_response")
        if not raw:
            return {
                "rawResponse": None,
                "orders": [],
                "fieldDiscovery": [],
                "retrievalMetrics": None
            }

        orders = self._extract_orders(raw)
        field_discovery = self._analyze_fields(raw, orders)

        return {
            "rawResponse": raw,
            "orders": orders,
            "fieldDiscovery": field_discovery,
            "retrievalMetrics": self.app_state.get("retrieval_metrics"),
            "savedFilePath": self.app_state.get("saved_file_path")
        }

    def _extract_orders(self, raw: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Safely extracts flattened order & item records for table visualization."""
        extracted = []
        if not isinstance(raw, dict):
            return extracted

        orders_list = raw.get("data", {}).get("orders") or raw.get("orders") or []
        if not isinstance(orders_list, list):
            return extracted

        for order in orders_list:
            if not isinstance(order, dict):
                continue

            order_id = order.get("orderId") or order.get("order_id") or order.get("id") or "N/A"
            order_date = order.get("orderDate") or order.get("order_date") or order.get("date") or "N/A"
            order_status = order.get("orderStatus") or order.get("status") or "N/A"
            currency = order.get("currency") or (order.get("orderTotal") or {}).get("currency") or "USD"

            shipments = order.get("shipmentDetails") or order.get("shipments") or []
            first_shipment = shipments[0] if isinstance(shipments, list) and shipments else {}
            carrier = first_shipment.get("carrier") or "N/A"
            tracking_number = first_shipment.get("trackingNumber") or first_shipment.get("tracking_number") or "N/A"
            shipment_status = first_shipment.get("shipmentStatus") or "N/A"
            delivery_date = first_shipment.get("deliveryDate") or first_shipment.get("estimatedDelivery") or "N/A"

            items = order.get("items") or order.get("orderItems") or []
            if not items:
                extracted.append({
                    "orderId": order_id,
                    "orderDate": order_date,
                    "orderStatus": order_status,
                    "title": "N/A (No item details)",
                    "asin": "N/A",
                    "quantity": 1,
                    "price": (order.get("orderTotal") or {}).get("amount") or "N/A",
                    "currency": currency,
                    "carrier": carrier,
                    "trackingNumber": tracking_number,
                    "shipmentStatus": shipment_status,
                    "deliveryDate": delivery_date,
                    "rawOrder": order
                })
            else:
                for item in items:
                    unit_price = (item.get("unitPrice") or {}).get("amount") or item.get("price") or (item.get("totalPrice") or {}).get("amount") or "N/A"
                    extracted.append({
                        "orderId": order_id,
                        "orderDate": order_date,
                        "orderStatus": order_status,
                        "title": item.get("title") or item.get("productName") or item.get("name") or "Unknown Product",
                        "asin": item.get("asin") or item.get("ASIN") or "N/A",
                        "quantity": item.get("quantity") or 1,
                        "price": unit_price,
                        "currency": (item.get("unitPrice") or {}).get("currency") or currency,
                        "carrier": carrier,
                        "trackingNumber": tracking_number,
                        "shipmentStatus": shipment_status,
                        "deliveryDate": delivery_date,
                        "seller": item.get("seller") or "N/A",
                        "rawItem": item,
                        "rawOrder": order
                    })

        return extracted

    def _analyze_fields(self, raw: Dict[str, Any], orders: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Inspects the raw response to determine what fields Amazon actually provides.
        Explicitly answers Stage 1 research questions regarding tracking, product, and order info.
        """
        # Convert raw to string for nested recursive search
        raw_str = json.dumps(raw).lower()

        checks = [
            {
                "fieldName": "Order ID",
                "category": "Order Data",
                "keys": ["orderid", "order_id", "ordernumber"],
                "description": "Unique identifier for the consumer order"
            },
            {
                "fieldName": "Order Date",
                "category": "Order Data",
                "keys": ["orderdate", "order_date", "purchasedate"],
                "description": "Timestamp when the order was placed"
            },
            {
                "fieldName": "Order Status",
                "category": "Order Data",
                "keys": ["orderstatus", "order_status", "status"],
                "description": "Lifecycle state of the order (e.g. DELIVERED, CANCELLED)"
            },
            {
                "fieldName": "Order Total / Price",
                "category": "Order Data",
                "keys": ["ordertotal", "totalprice", "unitprice", "amount"],
                "description": "Financial price totals and currency"
            },
            {
                "fieldName": "Product Title / Name",
                "category": "Product Data",
                "keys": ["title", "productname", "itemdescription"],
                "description": "Name / description of the ordered item"
            },
            {
                "fieldName": "ASIN",
                "category": "Product Data",
                "keys": ["asin"],
                "description": "Amazon Standard Identification Number"
            },
            {
                "fieldName": "Quantity",
                "category": "Product Data",
                "keys": ["quantity", "qty"],
                "description": "Item count purchased"
            },
            {
                "fieldName": "Seller / Merchant",
                "category": "Product Data",
                "keys": ["seller", "merchant", "sellername"],
                "description": "Seller of record (e.g. Amazon.com Services LLC, third party)"
            },
            {
                "fieldName": "Tracking Number",
                "category": "Shipping Data",
                "keys": ["trackingnumber", "tracking_number", "trackingcode"],
                "description": "Package shipment tracking ID (e.g. TBA..., 1Z...)"
            },
            {
                "fieldName": "Carrier",
                "category": "Shipping Data",
                "keys": ["carrier", "carriercode", "shippingcarrier"],
                "description": "Logistics carrier (e.g. AMZL, UPS, USPS, FedEx)"
            },
            {
                "fieldName": "Shipment Status",
                "category": "Shipping Data",
                "keys": ["shipmentstatus", "shippingstatus", "deliverystatus"],
                "description": "Status of the physical shipment parcel"
            },
            {
                "fieldName": "Shipment Date",
                "category": "Shipping Data",
                "keys": ["shipmentdate", "shippingdate", "shippeddate"],
                "description": "Date/time the package departed the fulfillment center"
            },
            {
                "fieldName": "Delivery / Estimated Date",
                "category": "Shipping Data",
                "keys": ["deliverydate", "estimateddelivery", "delivereddate"],
                "description": "Actual delivery date or scheduled estimate"
            },
            {
                "fieldName": "Shipping Address",
                "category": "Shipping Data",
                "keys": ["shippingaddress", "recipientname", "postalcode"],
                "description": "Recipient address and postal jurisdiction"
            }
        ]

        results = []
        for check in checks:
            found = False
            sample_val = None

            # First search extracted orders
            for o in orders:
                for k in check["keys"]:
                    # Direct check
                    if k in o and o[k] not in ("N/A", None, ""):
                        found = True
                        sample_val = str(o[k])
                        break
                if found:
                    break

            # Fallback string pattern matching in raw JSON
            if not found:
                for k in check["keys"]:
                    if f'"{k}"' in raw_str:
                        found = True
                        sample_val = "(Present in nested raw payload)"
                        break

            results.append({
                "fieldName": check["fieldName"],
                "category": check["category"],
                "present": found,
                "description": check["description"],
                "sampleValue": sample_val if found else None
            })

        return results
