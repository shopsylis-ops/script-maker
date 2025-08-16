from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

# Autoriser CORS (important pour Hoppscotch, Postman, etc.)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tu peux restreindre à une URL précise si tu veux
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def home():
    return {"message": "Script-maker service is running!"}

@app.post("/generate")
async def generate(request: Request):
    body = await request.json()
    topic = body.get("topic", "psychologie")

    # Ici, plus tard, tu pourras brancher Gemini ou un autre modèle
    return {
        "topic": topic,
        "script": f"Ton cerveau te joue un tour avec {topic}."
    }
