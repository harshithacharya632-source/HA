
import openai
import os

async def ai(query):
    openai.api_key = os.environ.get("OPENAI_API_KEY")  # ✅ reads from environment
    response = openai.chat.completions.create(
        model="gpt-3.5-turbo",  # ✅ updated model
        messages=[
            {"role": "user", "content": query}
        ],
        max_tokens=100,
        n=1,
        temperature=0.9
    )
    return response.choices[0].message.content.strip()
     
async def ask_ai(client, m, message):
    try:
        question = message.text.split(" ", 1)[1]
        response = await ai(question)
        await m.edit(f"{response}")
    except Exception as e:
        error_message = f"An error occurred: {e}"
        await m.edit(error_message)
