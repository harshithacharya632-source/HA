# Don't Remove Credit @VJ_Botz
# Subscribe YouTube Channel For Amazing Bot @Tech_VJ
# Ask Doubt on telegram @KingVJ01
import openai
import os

async def ai(query):
    openai.api_key = os.environ.get("OPENAI_API_KEY")
    
    try:
        # New version syntax (openai >= 1.0.0)
        client = openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": query}],
            max_tokens=100,
            temperature=0.9
        )
        return response.choices[0].message.content.strip()
    except Exception:
        # Old version syntax (openai < 1.0.0)
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": query}],
            max_tokens=100,
            temperature=0.9
        )
        return response.choices[0].message["content"].strip()

async def ask_ai(client, m, message):
    try:
        question = message.text.split(" ", 1)[1]
        response = await ai(question)
        await m.edit(f"{response}")
    except Exception as e:
        error_message = f"An error occurred: {e}"
        await m.edit(error_message)
