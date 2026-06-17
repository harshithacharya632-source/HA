
import os
from openai import AsyncOpenAI

openai_client = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY"))  # ✅ renamed to avoid conflict

async def ai(query):
    response = await openai_client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": query}],
        max_tokens=100,
        temperature=0.9
    )
    return response.choices[0].message.content.strip()

async def ask_ai(client_tg, m, message):
    try:
        question = message.text.split(" ", 1)[1]
        response = await ai(question)
        await m.edit(f"{response}")
    except Exception as e:
        error_message = f"An error occurred: {e}"
        await m.edit(error_message)
