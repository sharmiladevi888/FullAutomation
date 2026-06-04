from pathlib import Path
p = Path(r"E:/time now/continuity-studio/continuity-studio/app.py")
s = p.read_text(encoding="utf-8")
old = '''# --- Hack Auth Middleware ---
from fastapi import Request
from fastapi.responses import HTMLResponse

@app.middleware("http")
async def hack_auth_middleware(request: Request, call_next):
    # Public routes
    if request.url.path in ["/auth", "/api/auth", "/api/login"] or \
       request.url.path.startswith("/static") or \
       request.url.path.startswith("/data"):
        return await call_next(request)
    
    # Check for auth cookie
    email = request.cookies.get("hacker_access")
    if not email:
        return HTMLResponse(content="""`n`n`n        <!DOCTYPE html>`n`n`n        <html>`n`n`n        <head>`n`n`n            <title>Beta Live Access</title>`n`n`n            <style>`n`n`n                :root { --amber: #ff7a18; --bg: #0c0b0d; --panel: #1a181c; --ink: #f4eee9; }`n`n`n                body { `n`n`n                    margin: 0; padding: 0; overflow: hidden; height: 100vh;`n`n`n                    background: var(--bg); color: var(--ink); font-family: sans-serif;`n`n`n                }`n`n`n                /* The "Blurred Interface" backing */`n`n`n                .background-mock {`n`n`n                    position: fixed; inset: 0; z-index: -1;`n`n`n                    background: `n`n`n                        radial-gradient(1100px 600px at 85% -10%, rgba(255,122,24,.15), transparent 60%),`n`n`n                        radial-gradient(900px 500px at -5% 110%, rgba(70,194,182,.1), transparent 55%),`n`n`n                        #0c0b0d;`n`n`n                    filter: blur(20px); transform: scale(1.1);`n`n`n                }`n`n`n                .gate {`n`n`n                    display: flex; align-items: center; justify-content: center; height: 100vh;`n`n`n                    backdrop-filter: blur(8px); background: rgba(0,0,0,0.4);`n`n`n                }`n`n`n                .box { `n`n`n                    background: var(--panel); padding: 40px; border-radius: 20px; `n`n`n                    border: 1px solid #2c282f; text-align: center; width: 380px;`n`n`n                    box-shadow: 0 40px 100px rgba(0,0,0,0.8);`n`n`n                }`n`n`n                h1 { color: var(--amber); margin-bottom: 30px; font-size: 24px; font-weight: 800; }`n`n`n                .tabs { display: flex; background: #141215; border-radius: 10px; padding: 4px; margin-bottom: 25px; }`n`n`n                .tabs button { flex: 1; background: transparent; border: none; color: #9b938c; padding: 10px; border-radius: 7px; cursor: pointer; font-weight: 600; }`n`n`n                .tabs button.active { background: var(--amber); color: #000; }`n`n`n                `n`n`n                .fld { margin-bottom: 15px; text-align: left; }`n`n`n                .fld label { display: block; font-size: 11px; text-transform: uppercase; color: #6a6259; margin-bottom: 6px; letter-spacing: 0.1em; }`n`n`n                input { `n`n`n                    width: 100%; background: #141215; border: 1px solid #3a353f; color: #fff; `n`n`n                    padding: 12px 14px; border-radius: 8px; font-size: 15px; box-sizing: border-box;`n`n`n                }`n`n`n                input:focus { outline: none; border-color: var(--amber); }`n`n`n                `n`n`n                .btn { `n`n`n                    width: 100%; background: var(--amber); border: none; color: #000; `n`n`n                    padding: 14px; border-radius: 10px; font-weight: 800; cursor: pointer; `n`n`n                    font-size: 15px; margin-top: 10px; transition: 0.15s;`n`n`n                }`n`n`n                .btn:hover { filter: brightness(1.1); transform: translateY(-1px); }`n`n`n                .err { color: #ff5a4d; margin-top: 15px; font-size: 13px; display: none; }`n`n`n            </style>`n`n`n        </head>`n`n`n        <body>`n`n`n            <div class="background-mock"></div>`n`n`n            <div class="gate">`n`n`n                <div class="box">`n`n`n                    <h1>Beta Live Access</h1>`n`n                    `n`n`n                    <div class="tabs">`n`n`n                        <button id="t-login" class="active" onclick="setMode('login')">Login</button>`n`n`n                        <button id="t-register" onclick="setMode('register')">Register</button>`n`n`n                    </div>`n`n`n`n`n`n                    <div class="fld">`n`n`n                        <label>Gmail Address</label>`n`n`n                        <input type="email" id="email" placeholder="you@gmail.com">`n`n`n                    </div>`n`n`n                    <div class="fld" id="code-fld" style="display:none">`n`n`n                        <label>Access Code</label>`n`n`n                        <input type="text" id="code" placeholder="Enter beta code...">`n`n`n                    </div>`n`n`n`n`n`n                    <button class="btn" id="main-btn" onclick="submit()">Sign In</button>`n`n`n                    <div id="err" class="err">Error message</div>`n`n`n                </div>`n`n`n            </div>`n`n`n            <script>`n`n`n                let mode = 'login';`n`n`n                function setMode(m) {`n`n`n                    mode = m;`n`n`n                    document.getElementById('t-login').className = m === 'login' ? 'active' : '';`n`n`n                    document.getElementById('t-register').className = m === 'register' ? 'active' : '';`n`n`n                    document.getElementById('code-fld').style.display = m === 'register' ? 'block' : 'none';`n`n`n                    document.getElementById('main-btn').innerText = m === 'login' ? 'Sign In' : 'Claim Access';`n`n`n                    document.getElementById('err').style.display = 'none';`n`n`n                }`n`n`n`n`n`n                async function submit() {`n`n`n                    const email = document.getElementById('email').value.trim();`n`n`n                    const code = document.getElementById('code').value.trim();`n`n`n                    const err = document.getElementById('err');`n`n`n                    `n`n`n                    if(!email.endsWith('@gmail.com')) {`n`n`n                        err.innerText = "Only @gmail.com addresses allowed";`n`n`n                        err.style.display = 'block'; return;`n`n`n                    }`n`n`n`n`n`n                    try {`n`n`n                        const res = await fetch('/api/auth', {`n`n`n                            method: 'POST',`n`n`n                            headers: {'Content-Type': 'application/json'},`n`n`n                            body: JSON.stringify({ email, code, mode })`n`n`n                        });`n`n`n                        const data = await res.json();`n`n`n                        if (res.ok) { location.reload(); }`n`n`n                        else {`n`n`n                            err.innerText = data.detail || "Access denied";`n`n`n                            err.style.display = 'block';`n`n`n                        }`n`n`n                    } catch(e) { err.innerText = "Server error"; err.style.display = 'block'; }`n`n`n                }`n`n`n            </script>`n`n`n        </body>`n`n`n        </html>`n`n        """, status_code=401)
    
    # Inject user settings from vault if logged in
    vault = load_vault()
    user_data = vault.get(email, {})
    request.state.user_email = email
    request.state.settings = {
        "api_key": user_data.get("api_key", ""),
        "base_url": user_data.get("base_url", config.BASE_URL),
        "model": user_data.get("model", config.MODEL),
        "multi_image_edit": user_data.get("multi_image_edit", config.MULTI_IMAGE_EDIT),
        "claude_api_key": user_data.get("claude_api_key", ""),
        "claude_base_url": user_data.get("claude_base_url", config.CLAUDE_BASE_URL),
        "claude_model": user_data.get("claude_model", config.CLAUDE_MODEL),
        "elevenlabs_api_key": user_data.get("elevenlabs_api_key", config.ELEVENLABS_API_KEY),
        "elevenlabs_voice_id": user_data.get("elevenlabs_voice_id", config.ELEVENLABS_VOICE_ID),
        "elevenlabs_model": user_data.get("elevenlabs_model", config.ELEVENLABS_MODEL),
    }

    return await call_next(request)

@app.post("/api/auth")
async def api_auth(data: dict, response: Response):
    email = data.get("email", "").lower()
    code = data.get("code", "").upper()
    mode = data.get("mode", "login")
    
    if not email.endswith("@gmail.com"):
        raise HTTPException(400, "Gmail only")
    
    users = load_users()
    
    if mode == "register":
        if email in users:
            raise HTTPException(400, "User already registered. Please login.")
        
        codes = load_codes()
        if code not in codes or codes[code]["used"] >= codes[code]["limit"]:
            raise HTTPException(401, "Invalid or expired access code")
        
        # Claim code
        codes[code]["used"] += 1
        save_codes(codes)
        
        # Create user
        users[email] = {"code": code, "joined": time.time()}
        save_users(users)
    else:
        # Login
        if email not in users:
            raise HTTPException(401, "Account not found. Please register with a code.")

    # Grant access via email cookie
    response.set_cookie(key="hacker_access", value=email, max_age=86400 * 7)
    return {"ok": True}

app.mount("/data", StaticFiles(directory=config.DATA_DIR), name="data")

# Runtime settings resolver (uses request state)
def get_user_settings(request: Request):
    return request.state.settings
'''
new = '''# --- Public Mode Middleware (no login/code gate) ---
from fastapi import Request

@app.middleware("http")
async def hack_auth_middleware(request: Request, call_next):
    # Public mode: allow all and inject default settings
    email = request.cookies.get("hacker_access", "")
    vault = load_vault()
    user_data = vault.get(email, {})
    request.state.user_email = email
    request.state.settings = {
        "api_key": user_data.get("api_key", "") or os.environ.get("DEROUTER_API_KEY", ""),
        "base_url": user_data.get("base_url", config.BASE_URL),
        "model": user_data.get("model", config.MODEL),
        "multi_image_edit": user_data.get("multi_image_edit", config.MULTI_IMAGE_EDIT),
        "claude_api_key": user_data.get("claude_api_key", "") or os.environ.get("ANTHROPIC_API_KEY", ""),
        "claude_base_url": user_data.get("claude_base_url", config.CLAUDE_BASE_URL),
        "claude_model": user_data.get("claude_model", config.CLAUDE_MODEL),
        "elevenlabs_api_key": user_data.get("elevenlabs_api_key", getattr(config, "ELEVENLABS_API_KEY", "")),
        "elevenlabs_voice_id": user_data.get("elevenlabs_voice_id", getattr(config, "ELEVENLABS_VOICE_ID", "")),
        "elevenlabs_model": user_data.get("elevenlabs_model", getattr(config, "ELEVENLABS_MODEL", "")),
    }
    return await call_next(request)

app.mount("/data", StaticFiles(directory=config.DATA_DIR), name="data")

# Runtime settings resolver (uses request state)
def get_user_settings(request: Request):
    return request.state.settings
'''
if old in s:
    p.write_text(s.replace(old, new, 1), encoding="utf-8")
    print("OK")
else:
    print("FAIL")
