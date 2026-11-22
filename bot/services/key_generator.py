"""Generates user-facing memorable codes like NOVA-3841."""
import random

from services import db

WORDS = [
    "ALPHA", "NOVA", "ECHO", "DELTA", "GHOST", "TITAN", "OMEGA",
    "SIGMA", "VIPER", "STORM", "BLAZE", "CYBER", "NEXUS", "PRISM",
    "VOID", "FLUX", "APEX", "CORE", "PULSE", "ZERO",
]


async def generate_unique_code(max_attempts: int = 10) -> str:
    for _ in range(max_attempts):
        code = f"{random.choice(WORDS)}-{random.randint(1000, 9999)}"
        if not await db.code_exists(code):
            return code
    raise RuntimeError("Could not generate unique key code after retries")
