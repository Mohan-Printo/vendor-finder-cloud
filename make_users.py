"""
Password hash generator for Material Price & Vendor Finder.

HOW TO ADD OR CHANGE USERS:
1. Run this script:  python make_users.py
2. Type a username and password when asked
3. Copy the printed line into the USERS dict in app.py
4. Redeploy (git push)

IMPORTANT: The SALT here must match LOGIN_SALT in app.py / Render env vars.
If you set a custom LOGIN_SALT in Render, change SALT below to match.
"""

import hashlib

SALT = "mpvf-printo-2026"   # must match LOGIN_SALT in app.py / Render

def hash_pw(password):
    return hashlib.sha256((SALT + password).encode()).hexdigest()

print("\n=== User Hash Generator ===\n")
print("Add as many users as you want. Press Enter on empty username to finish.\n")

lines = []
while True:
    username = input("Username (or Enter to stop): ").strip().lower()
    if not username:
        break
    password = input(f"Password for '{username}': ").strip()
    if not password:
        print("  Skipped (empty password)\n")
        continue
    h = hash_pw(password)
    lines.append(f'    "{username}": "{h}",  # password: {password}')
    print("  Added!\n")

if lines:
    print("\n" + "="*60)
    print("Copy this into the USERS = {...} dict in app.py:")
    print("="*60)
    print("USERS = {")
    for l in lines:
        print(l)
    print("}")
    print("="*60 + "\n")
else:
    print("No users added.\n")
