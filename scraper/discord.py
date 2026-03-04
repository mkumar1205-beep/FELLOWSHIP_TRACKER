import os
import httpx
from dotenv import load_dotenv

load_dotenv()

async def send_discord_notification(program):
    DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
    if not DISCORD_WEBHOOK_URL:
        print("No webhook URL found")
        return

    message = {
        "content": f"""
 **New Fellowship Added!**
 **Name:** {program['name']}
 **Deadline:** {program['deadline']}
 **Apply:** {program['apply_link']}
"""
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(DISCORD_WEBHOOK_URL, json=message)
        print("Discord Response Code:", response.status_code)

