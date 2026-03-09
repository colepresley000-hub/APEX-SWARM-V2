"""
Patch: Replace APEX SWARM dashboard with v5 Command Center.
Run from your apex-swarm-v2 directory:
  python3 patch_dashboard_v5.py
"""

import sys

# Read dashboard HTML
try:
    with open("dashboard_v5.html", "r") as f:
        html = f.read()
    print(f"✅ Read dashboard_v5.html ({len(html)} chars)")
except FileNotFoundError:
    print("❌ dashboard_v5.html not found in current directory")
    sys.exit(1)

# Read main.py
with open("main.py", "r") as f:
    main = f.read()

# Find the dashboard section
start_marker = 'DASHBOARD_HTML = r"""<!DOCTYPE html>'
end_marker = '# ─── ENTRYPOINT'

if start_marker not in main:
    print("❌ Could not find DASHBOARD_HTML in main.py")
    sys.exit(1)

if end_marker not in main:
    print("❌ Could not find ENTRYPOINT marker in main.py")
    sys.exit(1)

start_idx = main.index(start_marker)
end_idx = main.index(end_marker)

new_section = f'DASHBOARD_HTML = r"""{html}"""\n\n\n'
new_main = main[:start_idx] + new_section + main[end_idx:]

with open("main.py", "w") as f:
    f.write(new_main)

# Verify
with open("main.py", "r") as f:
    verify = f.read()

if "Playfair" in verify:
    print("✅ Dashboard v5 patched successfully")
    print(f"   main.py: {len(verify)} chars")
else:
    print("❌ Patch may have failed — 'Playfair' not found")

print("\nNext:")
print("  git add -A")
print("  git commit -m 'v5: Command Center dashboard'")
print("  railway up")
