import os
import secrets
import urllib.parse
import httpx
from typing import Dict, Any, Optional

class AmazonOAuth:
    def __init__(self):
        self.client_id = os.getenv("AMAZON_CLIENT_ID", "mock-amazon-client-id")
        self.client_secret = os.getenv("AMAZON_CLIENT_SECRET", "mock-amazon-client-secret")
        self.redirect_uri = os.getenv("AMAZON_REDIRECT_URI", "http://localhost:8000/auth/amazon/callback")
        self.auth_url = os.getenv("AMAZON_LWA_AUTHORIZATION_URL", "https://www.amazon.com/ap/oa")
        self.token_url = os.getenv("AMAZON_LWA_TOKEN_URL", "https://api.amazon.com/auth/o2/token")
        self.scope = os.getenv("AMAZON_DATA_PORTABILITY_SCOPE", "portability::physical_orders")
        self.mock_mode = os.getenv("AMAZON_MOCK_MODE", "true").lower() in ("1", "true", "yes")

    def generate_authorization_url(self, state: Optional[str] = None) -> tuple[str, str]:
        """
        Generates the Login with Amazon (LWA) OAuth 2.0 authorization URL.
        Returns (authorization_url, state).
        """
        if not state:
            state = secrets.token_urlsafe(32)

        params = {
            "client_id": self.client_id,
            "scope": self.scope,
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
            "state": state
        }
        
        query_string = urllib.parse.urlencode(params)
        full_url = f"{self.auth_url}?{query_string}"
        return full_url, state

    async def exchange_code_for_tokens(self, code: str) -> Dict[str, Any]:
        """
        Exchanges the authorization code for access and refresh tokens.
        Never logs or exposes tokens or secrets.
        """
        if self.mock_mode:
            return {
                "access_token": "mock_at_" + secrets.token_hex(16),
                "refresh_token": "mock_rt_" + secrets.token_hex(16),
                "token_type": "bearer",
                "expires_in": 3600,
                "scope": self.scope
            }

        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
            "client_id": self.client_id,
            "client_secret": self.client_secret
        }

        headers = {
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(self.token_url, data=payload, headers=headers)
            if response.status_code != 200:
                # Do not log secrets or response body if it contains sensitive tokens
                raise RuntimeError(f"Amazon token exchange failed with status {response.status_code}")
            return response.json()
