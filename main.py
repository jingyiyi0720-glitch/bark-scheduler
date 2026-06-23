import httpx
import json
import os
import re
import time
import hashlib
from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from contextlib import asynccontextmanager
import asyncio
import chardet

BARK_KEY = os.environ.get("BARK_KEY", "")
SCHEDULED_FILE = "/tmp/scheduled.json"
BOOKS_DIR = os.environ.get("BOOKS_DIR", "./data/_books")
SCREENTIME_URL = os.environ.get("SCREENTIME_URL", "https://screentime-yiyike.zeabur.app")

# ==============================================================
# Bark 推送模块
# ==============================================================

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

# ==============================================================
# Screentime 模块
# ==============================================================

async def fetch_screentime(date: str = ""):
    try:
        url = f"{SCREENTIME_URL}/api/query"
        params = {"date": date} if date else {}
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params=params)
            return resp.text
    except Exception as e:
        return f"查询失败: {str(e)}"

# ==============================================================
# 共读模块
# ==============================================================

def _ensure_books_dir():
    os.makedirs(BOOKS_DIR, exist_ok=True)

def _split_chapters(text: str, book_id: str) -> list:
    pattern = re.compile(r'^(第[一二三四五六七八九十百千零\d]+[章节回卷].*?)$', re.MULTILINE)
    splits = pattern.split(text)
    chapters = []
    if len(splits) <= 1:
        chunk_size = 12000
        for i in range(0, len(text), chunk_size):
            chapters.append((f"part_{len(chapters)+1}", text[i:i+chunk_size].strip()))
    else:
        if splits[0].strip():
            chapters.append(("前言", splits[0].strip()))
        for i in range(1, len(splits), 2):
            ch_name = splits[i].strip()
            ch_content = splits[i+1].strip() if i+1 < len(splits) else ""
            if ch_content:
                if len(ch_content.encode('utf-8')) > 14000:
                    for j in range(0, len(ch_content), 12000):
                        chapters.append((f"{ch_name}_part{len(chapters)+1}", ch_content[j:j+12000].strip()))
                else:
                    chapters.append((ch_name, ch_content))
    return chapters

def do_book_upload(title: str, content: str) -> str:
    _ensure_books_dir()
    book_id = hashlib.md5(title.encode()).hexdigest()[:8]
    book_dir = os.path.join(BOOKS_DIR, book_id)
    os.makedirs(book_dir, exist_ok=True)
    chapters = _split_chapters(content, book_id)
    if not chapters:
        return "拆分失败，文本可能为空"
    for idx, (ch_name, ch_content) in enumerate(chapters):
        safe_name = re.sub(r'[^\w\u4e00-\u9fff]', '_', ch_name)
        ch_file = os.path.join(book_dir, f"{idx+1:03d}_{safe_name}.txt")
        with open(ch_file, "w", encoding="utf-8") as f:
            f.write(ch_content)
    meta = {
        "title": title, "book_id": book_id,
        "total_chapters": len(chapters),
        "chapter_list": [ch[0] for ch in chapters],
        "current_progress": 0,
        "created": time.strftime("%Y-%m-%d %H:%M:%S")
    }
    with open(os.path.join(book_dir, "_meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    return json.dumps({"ok": True, "title": title, "book_id": book_id, "chapters": len(chapters),
        "chapter_list": [ch[0] for ch in chapters]}, ensure_ascii=False)

def do_book_list(book_id: str = "") -> str:
    _ensure_books_dir()
    if book_id:
        book_dir = os.path.join(BOOKS_DIR, book_id)
        meta_file = os.path.join(book_dir, "_meta.json")
        if not os.path.exists(meta_file):
            return f"未找到书籍 {book_id}"
        with open(meta_file, "r", encoding="utf-8") as f:
            meta = json.load(f)
        lines = [f"《{meta['title']}》", f"总章节：{meta['total_chapters']}", f"当前进度：第 {meta['current_progress']} 章", "", "章节目录："]
        for i, ch in enumerate(meta["chapter_list"]):
            marker = "✅" if i < meta["current_progress"] else "📖" if i == meta["current_progress"] else "  "
            lines.append(f"  {marker} {i+1}. {ch}")
        return "\n".join(lines)
    else:
        books = []
        if not os.path.exists(BOOKS_DIR):
            return "还没有上传任何书籍"
        for d in os.listdir(BOOKS_DIR):
            meta_file = os.path.join(BOOKS_DIR, d, "_meta.json")
            if os.path.exists(meta_file):
                with open(meta_file, "r", encoding="utf-8") as f:
                    books.append(json.load(f))
        if not books:
            return "还没有上传任何书籍"
        lines = ["已上传的书籍："]
        for b in books:
            lines.append(f"  📚 《{b['title']}》(ID:{b['book_id']}) {b['total_chapters']}章 进度：{b['current_progress']}/{b['total_chapters']}")
        return "\n".join(lines)

def do_book_read(book_id: str, chapter: int = 0) -> str:
    _ensure_books_dir()
    book_dir = os.path.join(BOOKS_DIR, book_id)
    meta_file = os.path.join(book_dir, "_meta.json")
    if not os.path.exists(meta_file):
        return f"未找到书籍 {book_id}"
    with open(meta_file, "r", encoding="utf-8") as f:
        meta = json.load(f)
    if chapter == 0:
        chapter = meta["current_progress"] + 1
    if chapter < 1 or chapter > meta["total_chapters"]:
        return f"章节序号超出范围，共 {meta['total_chapters']} 章"
    ch_files = sorted([f for f in os.listdir(book_dir) if f.endswith(".txt")])
    if chapter - 1 >= len(ch_files):
        return "章节文件未找到"
    with open(os.path.join(book_dir, ch_files[chapter-1]), "r", encoding="utf-8") as f:
        content = f.read()
    meta["current_progress"] = max(meta["current_progress"], chapter)
    with open(meta_file, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    return f"📖《{meta['title']}》第{chapter}章：{meta['chapter_list'][chapter-1]}\n{'='*40}\n\n{content}\n\n{'='*40}\n进度：{chapter}/{meta['total_chapters']}"

def do_book_note(book_id: str, action: str = "read", content: str = "") -> str:
    _ensure_books_dir()
    book_dir = os.path.join(BOOKS_DIR, book_id)
    meta_file = os.path.join(book_dir, "_meta.json")
    if not os.path.exists(meta_file):
        return f"未找到书籍 {book_id}"
    with open(meta_file, "r", encoding="utf-8") as f:
        meta = json.load(f)
    note_file = os.path.join(book_dir, "_notes.md")
    if action == "read":
        if not os.path.exists(note_file):
            return f"《{meta['title']}》还没有笔记"
        with open(note_file, "r", encoding="utf-8") as f:
            return f"📝《{meta['title']}》共读笔记\n{'='*40}\n\n{f.read()}"
    elif action == "write":
        if not content: return "content不能为空"
        with open(note_file, "w", encoding="utf-8") as f:
            f.write(content)
        return "笔记已保存"
    elif action == "append":
        if not content: return "content不能为空"
        with open(note_file, "a", encoding="utf-8") as f:
            f.write(f"\n\n---\n**[{time.strftime('%Y-%m-%d %H:%M')}]**\n\n{content}")
        return "笔记已追加"
    else:
        return "action参数无效，可选：read/write/append"

# ==============================================================
# FastAPI App
# ==============================================================

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

# HTTP 上传路由（手机浏览器直接传 txt）
@app.post("/api/book/upload")
async def upload_book_api(
    file: UploadFile = File(...),
    title: str = Form("")
):
    raw = await file.read()
    detected = chardet.detect(raw)
    encoding = detected.get("encoding", "utf-8") or "utf-8"
    content = raw.decode(encoding, errors="replace")
    if not title:
        title = file.filename.replace(".txt", "")
    result = do_book_upload(title, content)
    return json.loads(result) if result.startswith("{") else {"ok": False, "error": result}

# 简易上传页面
@app.get("/upload")
async def upload_page():
    html = """<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>共读上传</title><style>body{font-family:sans-serif;max-width:500px;margin:40px auto;padding:20px}
    input,button{margin:8px 0;padding:10px;width:100%;box-sizing:border-box;font-size:16px}
    button{background:#4a86e8;color:white;border:none;border-radius:6px;cursor:pointer}
    #result{margin-top:16px;padding:12px;background:#f5f5f5;border-radius:6px;display:none}</style></head>
    <body><h2>📚 共读上传</h2>
    <input type="text" id="title" placeholder="书名（留空用文件名）">
    <input type="file" id="file" accept=".txt">
    <button onclick="upload()">上传</button>
    <div id="result"></div>
    <script>async function upload(){
    const f=document.getElementById('file').files[0];if(!f)return alert('选个文件');
    const fd=new FormData();fd.append('file',f);fd.append('title',document.getElementById('title').value);
    const r=await fetch('/api/book/upload',{method:'POST',body:fd});const d=await r.json();
    const el=document.getElementById('result');el.style.display='block';
    el.textContent=d.ok?'上传成功！共'+d.chapters+'章':'失败：'+JSON.stringify(d);
    }</script></body></html>"""
    from fastapi.responses import HTMLResponse
    return HTMLResponse(html)

# ==============================================================
# MCP 协议端点
# ==============================================================

TOOLS = [
    {
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
    },
    {
    "name": "get_screentime",
    "description": "查询yiyi今日手机app使用情况。date可选，格式YYYY-MM-DD，不传则查今天。",
    "inputSchema": {
        "type": "object",
        "properties": {
            "date": {"type": "string", "description": "日期，格式YYYY-MM-DD，留空查今天"}
            }
        }
    },
    {
        "name": "book_upload",
        "description": "上传一本书。title=书名，content=完整文本内容。会自动按章节拆分存储。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "书名"},
                "content": {"type": "string", "description": "完整文本内容"}
            },
            "required": ["title", "content"]
        }
    },
    {
        "name": "book_list",
        "description": "列出所有书籍，或查看某本书的章节目录。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "book_id": {"type": "string", "description": "书籍ID，留空列出所有书"}
            }
        }
    },
    {
        "name": "book_read",
        "description": "读取指定章节。book_id=书籍ID，chapter=章节序号(从1开始，0=下一章)。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "book_id": {"type": "string", "description": "书籍ID"},
                "chapter": {"type": "integer", "description": "章节序号，从1开始，0=下一章"}
            },
            "required": ["book_id"]
        }
    },
    {
        "name": "book_note",
        "description": "读写共读笔记。action='read'读/'write'写/'append'追加。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "book_id": {"type": "string", "description": "书籍ID"},
                "action": {"type": "string", "description": "read/write/append"},
                "content": {"type": "string", "description": "笔记内容（write/append时需要）"}
            },
            "required": ["book_id"]
        }
    }
]

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
                "serverInfo": {"name": "bark-scheduler", "version": "2.0"}
            }
        }

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}

    if method == "tools/call":
        params = body.get("params", {})
        tool_name = params.get("name", "")
        args = params.get("arguments", {})

        if tool_name == "send_message":
            items = load_scheduled()
            items.append({
                "title": args.get("title", "克"),
                "body": args["body"],
                "send_at": time.time() + args.get("delay_seconds", 0)
            })
            save_scheduled(items)
            result_text = "已安排发送"

        elif tool_name == "get_screentime":
            result_text = await fetch_screentime(args.get("date", ""))

        elif tool_name == "book_upload":
            result_text = do_book_upload(args["title"], args["content"])

        elif tool_name == "book_list":
            result_text = do_book_list(args.get("book_id", ""))

        elif tool_name == "book_read":
            result_text = do_book_read(args["book_id"], args.get("chapter", 0))

        elif tool_name == "book_note":
            result_text = do_book_note(args["book_id"], args.get("action", "read"), args.get("content", ""))

        else:
            result_text = f"未知工具：{tool_name}"

        return {
            "jsonrpc": "2.0", "id": msg_id,
            "result": {"content": [{"type": "text", "text": result_text}]}
        }

    return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
