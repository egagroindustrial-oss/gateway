from fastapi import FastAPI, Request,Response, HTTPException
from fastapi.responses import JSONResponse
import httpx
import os
import asyncio
import uuid
from typing import Dict
from datetime import datetime
from dotenv import load_dotenv

app = FastAPI(
    title="Apps Script Gateway API",
    description="Gateway API para Apps Script con soporte para peticiones simultáneas",
    version="1.0.0"
)

load_dotenv()
APPSCRIPT_API_URL = os.getenv("APPSCRIPT_API_URL")
print(APPSCRIPT_API_URL)

request_locks: Dict[str, asyncio.Lock] = {}

http_client = httpx.AsyncClient(timeout=30.0)

async def close_http_client():
    await http_client.aclose()

@app.middleware("http")
async def startup_check(request: Request, call_next):
    if not APPSCRIPT_API_URL:
        return JSONResponse(
            status_code=500,
            content={"error": "APPSCRIPT_API_URL not configured"}
        )
    response = await call_next(request)
    return response

def get_request_id(request: Request) -> str:
    request_id = request.headers.get("X-Request-ID")
    if not request_id:
        request_id = str(uuid.uuid4())
    return request_id

async def get_request_lock(request_id: str) -> asyncio.Lock:
    if request_id not in request_locks:
        request_locks[request_id] = asyncio.Lock()
    return request_locks[request_id]

async def cleanup_old_locks():
    if len(request_locks) > 1000:
        keys_to_remove = list(request_locks.keys())[:-100]
        for key in keys_to_remove:
            del request_locks[key]



# Semaphore for limited concurrency (e.g., 3 concurrent requests)
MAX_CONCURRENT_REQUESTS = 3
sheets_semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)


@app.post("/sheets")
async def proxy_to_appscript(request: Request):
    request_id = get_request_id(request)
    start_time = datetime.now()
    body = await request.body()
    async with sheets_semaphore:
        try:
            response = await http_client.post(
                APPSCRIPT_API_URL,
                content=body,
            )
            processing_time = (datetime.now() - start_time).total_seconds()
            excluded_headers = {"content-encoding", "transfer-encoding", "connection"}
            headers = {k: v for k, v in response.headers.items() if k.lower() not in excluded_headers}
            headers["X-Request-ID"] = request_id
            headers["X-Processing-Time"] = f"{processing_time:.2f}s"
            return Response(
                content=response.content,
                status_code=response.status_code,
                headers=headers,
                media_type=response.headers.get("content-type")
            )
        except httpx.TimeoutException:
            raise HTTPException(
                status_code=408, 
                detail={
                    "error": "Timeout connecting to Apps Script",
                    "request_id": request_id
                }
            )
        except httpx.HTTPStatusError as e:
            raise HTTPException(
                status_code=e.response.status_code,
                detail={
                    "error": f"Apps Script returned error: {e.response.status_code}",
                    "request_id": request_id
                }
            )
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail={
                    "error": "Internal server error",
                    "message": str(e),
                    "request_id": request_id
                }
            )

if __name__ == "__main__":
    import uvicorn
    try:
        if not APPSCRIPT_API_URL:
            exit(1)
        uvicorn.run(app,host="0.0.0.0", port=10000)
    except KeyboardInterrupt:
        pass
