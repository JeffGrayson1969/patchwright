from __future__ import annotations


def get_user(cursor, username):
    cursor.execute(f"SELECT * FROM users WHERE username = '{username}'")
    return cursor.fetchone()
