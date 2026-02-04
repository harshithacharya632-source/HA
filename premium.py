# premium.py
# GOFLIX Premium User System

# =========================
# ADD PREMIUM USER IDs HERE
# =========================
PREMIUM_USERS = {
    123456789,  # replace with real Telegram user_id
}

# =========================
# CHECK PREMIUM
# =========================
def is_premium(user_id: int) -> bool:
    return user_id in PREMIUM_USERS

# =========================
# FORMAT USER NAME
# =========================
def premium_name(user):
    if not user:
        return "UNKNOWN"

    name = user.first_name.upper()

    if is_premium(user.id):
        return f"👑 GOFLIX PREMIUM USER 👑 | {name}"
    else:
        return user.first_name
