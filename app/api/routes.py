from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from app.services.rag_service import ingest_dummy_data, generate_chat_response
from app.services.adapters.postgres_adapter import sync_postgres_to_ai

from app.services.gemini_service import generate_whatsapp_response
from app.services.whatsapp_service import send_whatsapp_message, send_whatsapp_image
from fastapi import Request, Response
import os

router = APIRouter()

# --- EXISTING ROUTES ---
@router.get("/test-wa")
def test_whatsapp():
    """Manually test if WhatsApp sending works"""
    test_number = "918698338343" # Your number from the logs
    print(f"Sending manual test to {test_number}...")
    res = send_whatsapp_message(test_number, "Hello! This is a direct test message from your server (No AI). If you see this, your WhatsApp API is WORKING!")
    return res

@router.get("/check-env")
def check_env():
    """Checks if the required environment variables are set without exposing them"""
    required_vars = [
        "GOOGLE_GEMINI_API_KEY",
        "WHATSAPP_ACCESS_TOKEN",
        "WHATSAPP_PHONE_NUMBER_ID",
        "WHATSAPP_BUSINESS_ACCOUNT_ID",
        "WHATSAPP_VERIFY_TOKEN",
        "PGHOST",
        "PGPORT",
        "PGUSER",
        "PGPASSWORD",
        "PGDATABASE"
    ]
    status = {}
    for var in required_vars:
        val = os.getenv(var)
        status[var] = {
            "configured": val is not None and len(val.strip()) > 0
        }

    return {
        "status": "ok",
        "environment": status,
        "is_vercel": os.getenv("VERCEL") == "1"
    }

# --- WHATSAPP WEBHOOK ROUTES ---

@router.get("/webhook/whatsapp")
def verify_whatsapp_webhook(request: Request):
    """Verifies the webhook with Meta"""
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")
    
    verify_token = os.getenv("WHATSAPP_VERIFY_TOKEN", "secret_homved")
    
    print(f"[VERIFY] Received mode: {mode}, token: {token}, challenge: {challenge}")
    print(f"[VERIFY] Expected token: {verify_token}")
    
    if mode == "subscribe" and token == verify_token:
        print("WEBHOOK_VERIFIED")
        return Response(content=challenge, media_type="text/plain")
    
    print("WEBHOOK_VERIFICATION_FAILED")
    return Response(status_code=403)

@router.post("/webhook/whatsapp")
async def handle_whatsapp_webhook(request: Request):
    """Receives and processes WhatsApp messages"""
    body = await request.json()
    print(f"[WEBHOOK_JSON] {body}")
    
    # Check if it's a message event
    try:
        changes_value = body["entry"][0]["changes"][0]["value"]
        if "messages" in changes_value:
            message = changes_value["messages"][0]
            from_number = message["from"]
            
            # Extract message text robustly
            message_type = message.get("type", "text")
            text_body = ""
            if message_type == "text" and "text" in message:
                text_body = message["text"].get("body", "")
            elif message_type == "interactive" and "interactive" in message:
                interactive = message["interactive"]
                if interactive.get("type") == "button_reply":
                    text_body = interactive.get("button_reply", {}).get("title", "")
                elif interactive.get("type") == "list_reply":
                    text_body = interactive.get("list_reply", {}).get("title", "")
            elif message_type == "button" and "button" in message:
                text_body = message["button"].get("text", "")
            else:
                # Fallback to get any text or type
                text_body = message.get("text", {}).get("body", "")
            
            if not text_body:
                print(f"[RECV] Message ignored or empty. Type: {message_type}")
                return {"status": "empty_message"}
                
            safe_text = text_body.encode('ascii', 'ignore').decode('ascii')
            print(f"[RECV] Received WhatsApp message from {from_number}: {safe_text}")
            
            # 1. Get AI Answer from Gemini + DB
            result = generate_whatsapp_response(text_body)
            ai_text = result["text"]
            image_url = result["image_url"]
            
            # 2. Send back to WhatsApp
            if image_url:
                send_whatsapp_image(from_number, image_url, caption=ai_text)
            else:
                send_whatsapp_message(from_number, ai_text)
                
            return {"status": "success"}
    except Exception as e:
        import traceback
        safe_err = str(e).encode('ascii', errors='ignore').decode('ascii')
        print(f"Webhook Error: {safe_err}")
        traceback.print_exc()
        return {
            "status": "error",
            "message": "An internal error occurred while processing the request."
        }

class ChatRequest(BaseModel):
    app_id: str
    question: str
    language: str = "en-IN"

class IngestRequest(BaseModel):
    app_id: str

class IngestPostgresRequest(BaseModel):
    app_id: str
    table_name: str
    text_columns: list[str]
    metadata_columns: list[str] = []

@router.post("/ingest")
def ingest_data(request: IngestRequest):
    result = ingest_dummy_data(request.app_id)
    return result

@router.post("/chat")
def chat(request: ChatRequest):
    return StreamingResponse(
        generate_chat_response(request.app_id, request.question, request.language),
        media_type="text/plain"
    )

@router.post("/ingest/postgres")
def ingest_postgres(request: IngestPostgresRequest):
    result = sync_postgres_to_ai(
        request.app_id, 
        request.table_name, 
        request.text_columns, 
        request.metadata_columns
    )
    return result
