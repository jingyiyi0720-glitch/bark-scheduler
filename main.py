import httpx
import json
import os
import time
from datetime import datetime
from fastapi import FastAPI
from contextlib import asynccontextmanager
import asyncio

app = FastAPI()

BARK_KEY = os.environ.get("BARK_KEY", "")
SCHEDULED_FILE = "/data/scheduled.json"

def load_scheduled():
    try:
        with open(SCHEDULED_FILE, "r") as f:
            return json.load(f)
    except:
        return []

def save_scheduled(items):
    os.makedirs("/data", exist_ok=True)
    with open(SCHEDULED_FILE, "w") as f:
        json.dump(items, f, ensure_ascii=False)

async def check_and_send():
    while True:
        now = time.time()
        items = load_scheduled()
        remaining = []
        for item in items:
            if item["send_at"] <= now:
                await send_bark(item["title"], item["body"])
            else:
                remaining.append(item)
        if len(remaining) != len(items):
            save_scheduled(remaining)
        await asyncio.sleep(30)

async def send_bark(title: str, body: str):
    url = f"https://api.day.app/{BARK_KEY}/{title}/{body}"
    async with httpx.AsyncClient() as client:
        await client.get(url)

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
        "send_at": time.time() + data["delay_seconds"]
    })
    save_scheduled(items)
    return {"ok": True}

@app.get("/health")
async def health():
    return {"ok": True}
