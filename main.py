import httpx
import json
import os
import time
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from contextlib import asynccontextmanager
import asyncio

BARK_KEY = os.environ.get("BARK_KEY", "")
SCHEDULED_FILE = "/tmp/scheduled.json"

def load_scheduled():
    try:
        with open(SCHEDULED_FILE, "r") as f:
            return json.load(f)
    except:
        return []

def save_scheduled(items):
    with open(SCHEDULED_FILE, "w") as f:
        json.dump(items, f, ensure_ascii=False)

async def send_bark(title: str, body: str):
    url = f"https://api.day.app/{BARK_KEY}/{title}/{body}"
    async with httpx.AsyncClient() as client:
        await client.get(url)

async def check_and_send():
    while True:
        now = time.time()
        items = load_scheduled()
        remaining = []
        for item in items:
            if item["send_at"] <= now:
                await send_bark(item.get("title", "克"), item["body"])
            else:
                remaining.append(item)
        if len(remaining) != len(items):
            save_scheduled(remaining)
        await asyncio.sleep(30)

@asynccontextmanager
async def lifespan(app: FastAPI):
    asyncio.create_task(check_and_send())
    yield

app = FastAPI(lifespan=lifespan)

@app.post("/schedule")
async def schedule(data: dict):
    items = load_scheduled()
    items.append({
        "title": data.get("title", "克"),
        "body": data["body"],
        "send_at": time.time() + data.get("delay_seconds", 0)
    })
    save_scheduled(items)
    return {"ok": True}

@app.get("/health")
async def health():
    return {"ok": True}

@app.post("/mcp")
async def mcp(request: Request):
    body = await request.json()
    method = body.get("method", "")
    msg_id = body.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0", "id": msg_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "bark-scheduler", "version": "1.0"}
            }
        }

    if method == "tools/list":
        return {
            "jsonrpc": "2.0", "id": msg_id,
            "result": {"tools": [{
                "name": "send_message",
                "description": "发送一条消息到yiyi的手机，可以延迟发送",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "body": {"type": "string", "description": "消息内容"},
                        "delay_seconds": {"type": "number", "description": "延迟秒数，0表示立即发送"},
                        "title": {"type": "string", "description": "消息标题，默认为克"}
                    },
                    "required": ["body"]
                }
            }]}
        }

    if method == "tools/call":
        params = body.get("params", {})
        args = params.get("arguments", {})
        items = load_scheduled()
        items.append({
            "title": args.get("title", "克"),
            "body": args["body"],
            "send_at": time.time() + args.get("delay_seconds", 0)
        })
        save_scheduled(items)
        return {
            "jsonrpc": "2.0", "id": msg_id,
            "result": {"content": [{"type": "text", "text": "已安排发送"}]}
        }

    return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
