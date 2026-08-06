"""
manage_admin.py — one-off CLI helper for SwiftProSys / Swift Optimizer

Run this ON THE MACHINE that already runs admin_dashboard.py (same folder,
so it can `import db` and reuse the exact same DB connection settings and
password hashing as the real app). Do NOT edit passwords with a raw SQL
UPDATE — the app stores bcrypt/sha256 hashes, not plaintext, and a raw
UPDATE will lock you out because login.py won't be able to verify it.

Usage:
    1. Copy this file into the SwiftProSys/ folder (next to db.py).
    2. Run:  python manage_admin.py
    3. Follow the menu.
"""

import getpass
import sys

import db  # the app's own db.py — reuses MYSQL_CONFIG, hash_password(), etc.


def list_admins():
    conn = db.get_conn()
    c = conn.cursor(dictionary=True)
    c.execute("SELECT id, username, full_name, role, branch, is_active FROM users WHERE role IN ('admin','super_admin')")
    rows = c.fetchall()
    conn.close()
    return rows


def change_admin_credentials():
    admins = list_admins()
    if not admins:
        print("No admin accounts found.")
        return

    print("\nExisting admin account(s):")
    for a in admins:
        print(f"  [{a['id']}] {a['username']}  ({a['full_name']})")

    try:
        target_id = int(input("\nEnter the ID of the admin to update: ").strip())
    except ValueError:
        print("Invalid ID.")
        return

    match = next((a for a in admins if a["id"] == target_id), None)
    if not match:
        print("No admin with that ID.")
        return

    new_username = input(f"New username [{match['username']}] (Enter to keep): ").strip()
    new_username = new_username or match["username"]

    new_full_name = input(f"New full name [{match['full_name']}] (Enter to keep): ").strip()
    new_full_name = new_full_name or match["full_name"]

    new_password = getpass.getpass("New password (leave blank to keep current): ").strip()

    conn = db.get_conn()
    c = conn.cursor()
    try:
        # Username uniqueness check (excluding this row)
        c.execute(
            "SELECT id FROM users WHERE LOWER(username)=LOWER(%s) AND id<>%s",
            (new_username, target_id)
        )
        if c.fetchone():
            print("That username is already taken by another account.")
            conn.close()
            return

        if new_password:
            hashed = db.hash_password(new_password)
            algo = "bcrypt" if db._HAS_BCRYPT else "sha256"
            c.execute(
                "UPDATE users SET username=%s, full_name=%s, password=%s, password_algo=%s WHERE id=%s",
                (new_username, new_full_name, hashed, algo, target_id)
            )
        else:
            c.execute(
                "UPDATE users SET username=%s, full_name=%s WHERE id=%s",
                (new_username, new_full_name, target_id)
            )
        conn.commit()
        print(f"\n✔ Admin account [{target_id}] updated. Username is now '{new_username}'.")
    except Exception as e:
        conn.rollback()
        print(f"Error: {e}")
    finally:
        conn.close()


def add_admin():
    username = input("New admin username: ").strip()
    full_name = input("New admin full name: ").strip()
    password = getpass.getpass("New admin password: ").strip()
    role = input("Role — 'admin' or 'super_admin' [admin]: ").strip().lower() or "admin"
    if role not in ("admin", "super_admin"):
        print("Role must be 'admin' or 'super_admin'.")
        return

    branch = None
    if role == "admin":
        print(f"Branches: {', '.join(db.BRANCHES)}")
        branch = input("Branch: ").strip()
        if branch not in db.BRANCHES:
            print("Invalid branch.")
            return

    if not username or not full_name or not password:
        print("All fields are required.")
        return

    conn = db.get_conn()
    c = conn.cursor()
    try:
        c.execute("SELECT id FROM users WHERE LOWER(username)=LOWER(%s)", (username,))
        if c.fetchone():
            print("Username already exists.")
            conn.close()
            return

        hashed = db.hash_password(password)
        algo = "bcrypt" if db._HAS_BCRYPT else "sha256"
        c.execute(
            "INSERT INTO users (username, password, role, full_name, password_algo, branch) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (username, hashed, role, full_name, algo, branch)
        )
        conn.commit()
        print(f"\n✔ New {role} '{username}' created successfully.")
    except Exception as e:
        conn.rollback()
        print(f"Error: {e}")
    finally:
        conn.close()


def main():
    print("=== SwiftProSys Admin Account Manager ===")
    print("1) Change an existing admin's username/password")
    print("2) Add a new admin account")
    print("3) List admin accounts")
    print("0) Exit")

    choice = input("\nChoose an option: ").strip()
    if choice == "1":
        change_admin_credentials()
    elif choice == "2":
        add_admin()
    elif choice == "3":
        for a in list_admins():
            print(f"  [{a['id']}] {a['username']}  ({a['full_name']})")
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
