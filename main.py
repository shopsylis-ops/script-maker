from fastapi import FastAPI, Request

app = FastAPI()

@app.get("/")
def home():
    return {"message": "Script-maker service is running!"}

@app.post("/generate")
async def generate(request: Request):
    body = await request.json()
    topic = body.get("topic", "psychologie")
    # Ici, plus tard, tu pourras brancher Gemini ou un autre modèle
    return {"topic": topic, "script": f"Ton cerveau te joue un tour avec {topic}."}
