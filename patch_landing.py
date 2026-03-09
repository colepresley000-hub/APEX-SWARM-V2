"""
Patch: Add landing page to main.py
Serves landing.html at / and moves dashboard to /dashboard

Run:
  python3 patch_landing.py
"""

import sys

# Read landing page
try:
    with open("landing.html", "r") as f:
        landing_html = f.read()
    print(f"✅ Read landing.html ({len(landing_html)} chars)")
except FileNotFoundError:
    print("❌ landing.html not found")
    sys.exit(1)

with open("main.py", "r") as f:
    content = f.read()

# Add LANDING_HTML variable and update routes
# Find the dashboard route section
old_routes = '''@app.get("/", response_class=HTMLResponse)
@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    return HTMLResponse(DASHBOARD_HTML.replace("__VERSION__", VERSION))'''

new_routes = '''@app.get("/", response_class=HTMLResponse)
async def landing_page():
    return HTMLResponse(LANDING_HTML.replace("__VERSION__", VERSION))


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    return HTMLResponse(DASHBOARD_HTML.replace("__VERSION__", VERSION))'''

if old_routes in content:
    content = content.replace(old_routes, new_routes)
    print("✅ Updated routes: / = landing, /dashboard = command center")
else:
    print("⚠️ Could not find exact route pattern, trying alternative...")
    # Try to find and update
    if '@app.get("/", response_class=HTMLResponse)' in content and 'async def dashboard' in content:
        content = content.replace(
            '@app.get("/", response_class=HTMLResponse)\n@app.get("/dashboard", response_class=HTMLResponse)',
            '@app.get("/", response_class=HTMLResponse)\nasync def landing_page():\n    return HTMLResponse(LANDING_HTML.replace("__VERSION__", VERSION))\n\n\n@app.get("/dashboard", response_class=HTMLResponse)'
        )
        print("✅ Routes updated via alternative method")
    else:
        print("❌ Could not update routes")
        sys.exit(1)

# Add LANDING_HTML before DASHBOARD_HTML
landing_var = f'\nLANDING_HTML = r"""{landing_html}"""\n\n'

if "DASHBOARD_HTML = r" in content:
    content = content.replace("DASHBOARD_HTML = r", landing_var + "DASHBOARD_HTML = r", 1)
    print("✅ Added LANDING_HTML variable")
else:
    print("❌ Could not find DASHBOARD_HTML")
    sys.exit(1)

with open("main.py", "w") as f:
    f.write(content)

# Verify
with open("main.py", "r") as f:
    verify = f.read()

checks = [
    ("Playfair" in verify, "Landing page fonts"),
    ("LANDING_HTML" in verify, "LANDING_HTML variable"),
    ("landing_page" in verify, "Landing route function"),
    ("never sleeps" in verify, "Hero copy"),
]

all_pass = True
for check, label in checks:
    status = "✅" if check else "❌"
    print(f"  {status} {label}")
    if not check:
        all_pass = False

if all_pass:
    print("\n✅ All checks passed!")
else:
    print("\n⚠️ Some checks failed")

print("\nNext:")
print("  git add -A")
print("  git commit -m 'v5: landing page + command center'")
print("  railway up")
