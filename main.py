import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from amazon.service import AmazonService

app = FastAPI(
    title="Amazon Data Portability Prototype API",
    description="Stage 1 Exploratory Prototype for Amazon Data Portability Integration",
    version="1.0.0"
)

# Enable CORS for Vite frontend
frontend_url = os.getenv("FRONTEND_URL", "http://localhost:5173")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[frontend_url, "http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

service = AmazonService()

@app.get("/")
async def root():
    return {
        "service": "Amazon Data Portability Prototype Backend",
        "status": "online",
        "mock_mode": service.oauth.mock_mode
    }

@app.get("/api/status")
async def get_status():
    """Returns the current connection, active query, and mock status."""
    return {
        "connected": service.app_state["connected"],
        "connectedAt": service.app_state["connected_at"],
        "mockMode": service.app_state["mock_mode"],
        "activeQuery": service.app_state["active_query"],
        "hasResults": service.app_state["raw_response"] is not None,
        "savedFilePath": service.app_state.get("saved_file_path")
    }

@app.get("/auth/amazon/start")
async def auth_start(request: Request, direct: bool = False):
    """
    Initiates OAuth 2.0 flow with Login with Amazon (LWA).
    If direct=true or in mock mode, provides immediate redirect or URL.
    """
    try:
        auth_url = service.start_oauth()
        
        # If running in mock mode and direct callback is preferred
        if service.oauth.mock_mode:
            mock_code = "mock_auth_code_12345"
            state = service.app_state["oauth_state"]
            # Automatically redirect to callback for seamless prototype demo
            return RedirectResponse(url=f"/auth/amazon/callback?code={mock_code}&state={state}")

        return RedirectResponse(url=auth_url)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start Amazon authorization: {str(e)}")

@app.get("/auth/amazon/callback")
async def auth_callback(code: str = Query(None), state: str = Query(None), error: str = Query(None)):
    """
    OAuth 2.0 callback endpoint from Amazon Login.
    Receives code and state, exchanges code for tokens, stores in memory, and redirects to frontend.
    """
    if error:
        return RedirectResponse(url=f"{frontend_url}?error={error}")

    if not code:
        return RedirectResponse(url=f"{frontend_url}?error=missing_authorization_code")

    try:
        await service.handle_oauth_callback(code=code, state=state)
        return RedirectResponse(url=f"{frontend_url}?connected=true")
    except Exception as e:
        return RedirectResponse(url=f"{frontend_url}?error={str(e)}")

@app.post("/api/query/create")
async def create_query():
    """Creates an asynchronous Data Portability query for physical orders."""
    try:
        result = await service.create_order_query()
        return {
            "success": True,
            "query": result
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/query/status")
async def query_status():
    """Checks progress of active Data Portability query."""
    try:
        status_info = await service.check_query_status()
        return status_info
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/query/results")
async def query_results():
    """Returns the raw Amazon response, parsed orders, and field discovery report."""
    try:
        results = service.get_results_and_analysis()
        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/reset")
async def reset_session():
    """Resets prototype memory state."""
    service.reset_state()
    return {"success": True, "message": "Prototype state reset successfully."}

@app.post("/api/toggle-mock")
async def toggle_mock():
    """Toggles mock mode for testing."""
    new_mode = not service.app_state["mock_mode"]
    service.app_state["mock_mode"] = new_mode
    service.oauth.mock_mode = new_mode
    service.client.mock_mode = new_mode
    return {"mockMode": new_mode}

from fastapi.responses import HTMLResponse

@app.get("/privacy", response_class=HTMLResponse)
async def privacy_policy():
    return """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>Privacy Notice - Order Data Explorer</title>
        <style>
            body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; max-width: 800px; margin: 40px auto; padding: 20px; line-height: 1.6; color: #1e293b; background: #f8fafc; }
            .card { background: white; padding: 32px; border-radius: 12px; border: 1px solid #e2e8f0; box-shadow: 0 4px 6px -1px rgb(0 0 0 / 0.1); }
            h1 { color: #0f172a; border-bottom: 1px solid #e2e8f0; padding-bottom: 12px; }
            h2 { color: #334155; margin-top: 24px; }
            .badge { background: #fef3c7; color: #92400e; padding: 4px 8px; border-radius: 6px; font-size: 12px; font-weight: bold; }
        </style>
    </head>
    <body>
        <div class="card">
            <h1>Privacy Notice <span class="badge">Login with Amazon Compliant</span></h1>
            <p><strong>Last Updated:</strong> August 21, 2026</p>
            <h2>1. Overview</h2>
            <p>This Privacy Notice describes how Order Data Explorer accesses, uses, and protects customer information retrieved via Login with Amazon (LWA) and the Amazon Data Portability API.</p>
            <h2>2. Data Collection & Use</h2>
            <p>With your explicit OAuth authorization, we access your Amazon physical order history (Order ID, purchase timestamps, item names, ASINs, quantities, prices, and carrier/shipment status if provided). We never ask for or store your Amazon account password.</p>
            <h2>3. Storage & Security</h2>
            <p>Tokens are held transiently in server memory during your active session. We do not sell or share customer data with third parties.</p>
            <h2>4. Revocation</h2>
            <p>You can revoke access at any time through your Amazon Account settings under <em>Login with Amazon Applications</em>.</p>
        </div>
    </body>
    </html>
    """

@app.get("/terms", response_class=HTMLResponse)
async def terms_of_service():
    return """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>Terms of Service - Order Data Explorer</title>
        <style>
            body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; max-width: 800px; margin: 40px auto; padding: 20px; line-height: 1.6; color: #1e293b; background: #f8fafc; }
            .card { background: white; padding: 32px; border-radius: 12px; border: 1px solid #e2e8f0; box-shadow: 0 4px 6px -1px rgb(0 0 0 / 0.1); }
            h1 { color: #0f172a; border-bottom: 1px solid #e2e8f0; padding-bottom: 12px; }
            h2 { color: #334155; margin-top: 24px; }
            .disclaimer { background: #eff6ff; border-left: 4px solid #3b82f6; padding: 12px 16px; margin: 16px 0; font-size: 14px; }
        </style>
    </head>
    <body>
        <div class="card">
            <h1>Terms of Service</h1>
            <div class="disclaimer">
                <strong>Trademark Disclaimer:</strong> Amazon, Login with Amazon, and all related logos are trademarks of Amazon.com, Inc. or its affiliates. This application is an independent developer integration and is not endorsed or sponsored by Amazon.
            </div>
            <h2>1. Use of Application</h2>
            <p>Order Data Explorer provides tools to analyze and inspect datasets retrieved through authorized Amazon APIs. Users agree to authorize only accounts they own or are authorized to manage.</p>
            <h2>2. Disclaimer of Warranties</h2>
            <p>The service is provided on an "as is" and "as available" basis without warranties of any kind.</p>
        </div>
    </body>
    </html>
    """
