import json, threading, time
from pathlib import Path
import serial
from serial.tools import list_ports
from aiohttp import web, WSMsgType

BASE = Path(__file__).resolve().parent
WEB = BASE / "web"
SERIAL_PORT = "COM5"
BAUD_RATE = 115200
HOST, PORT = "127.0.0.1", 8765
stop_event = threading.Event()
lock = threading.Lock()
active_port = None
data = {"temperatures":[None]*4,"elapsed_seconds":0,"seconds":0,"minutes":0,"hours":0,
        "total_minutes":0,"total_hours":0,"days":0,"sample_counter":0,
        "connected":False,"port":None,"last_update":None,"error":None}

def choose_port():
    if SERIAL_PORT.strip(): return SERIAL_PORT.strip()
    ports = list(list_ports.comports())
    for p in ports:
        d = (p.description or "").lower()
        if any(k in d for k in ("pico","usb serial","usb-serial","uart","serial")): return p.device
    return ports[0].device if ports else None

def parse_line(line):
    parts = line.strip().split(",")
    if len(parts) < 12: return None
    try: v = [float(x.strip()) for x in parts[:12]]
    except ValueError: return None
    return {"temperatures":v[:4],"elapsed_seconds":int(v[4]),"seconds":int(v[5]),
            "minutes":int(v[6]),"hours":int(v[7]),"total_minutes":int(v[8]),
            "total_hours":int(v[9]),"days":int(v[10]),"sample_counter":int(v[11]),
            "connected":True,"port":active_port,"last_update":time.time(),"error":None}

def serial_worker():
    global active_port
    while not stop_event.is_set():
        dev = choose_port()
        if not dev:
            with lock:
                data.update(connected=False,port=None,error="Pico serial port not found")
            stop_event.wait(2); continue
        try:
            active_port = dev
            with serial.Serial(dev, BAUD_RATE, timeout=1) as ser:
                with lock: data.update(port=dev,error=None)
                while not stop_event.is_set():
                    raw = ser.readline()
                    if not raw: continue
                    parsed = parse_line(raw.decode("utf-8",errors="ignore"))
                    if parsed:
                        with lock: data.update(parsed)
        except (serial.SerialException,OSError) as exc:
            with lock: data.update(connected=False,error=str(exc))
            stop_event.wait(2)
        finally: active_port = None

async def index(request): return web.FileResponse(WEB/"index.html")
async def static(request):
    name=request.match_info["name"]
    if name not in ("style.css","app.js"): raise web.HTTPNotFound()
    return web.FileResponse(WEB/name)
async def health(request):
    with lock: out={**data,"temperatures":list(data["temperatures"])}
    return web.json_response(out)
async def ws_handler(request):
    ws=web.WebSocketResponse(heartbeat=20); await ws.prepare(request)
    try:
        while not ws.closed:
            with lock: out={**data,"temperatures":list(data["temperatures"])}
            await ws.send_str(json.dumps(out))
            try: msg=await ws.receive(timeout=0.8)
            except TimeoutError: continue
            if msg.type in (WSMsgType.CLOSE,WSMsgType.ERROR): break
    except (ConnectionResetError,RuntimeError): pass
    finally: pass
    return ws
async def startup(app):
    app["serial_thread"]=threading.Thread(target=serial_worker,daemon=True); app["serial_thread"].start()
async def cleanup(app):
    stop_event.set()
    t=app.get("serial_thread")
    if t and t.is_alive(): t.join(timeout=2)
def create_app():
    app=web.Application()
    app.router.add_get("/",index); app.router.add_get("/static/{name}",static)
    app.router.add_get("/ws",ws_handler); app.router.add_get("/health",health)
    app.on_startup.append(startup); app.on_cleanup.append(cleanup)
    return app
if __name__=="__main__":
    print(f"Furnace Dashboard: http://{HOST}:{PORT}")
    print("Close Serial Studio/Thonny serial connection before starting. Ctrl+C to stop.")
    web.run_app(create_app(),host=HOST,port=PORT)
