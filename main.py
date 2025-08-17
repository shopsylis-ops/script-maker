from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
import os
import google.generativeai as genai

app = FastAPI()

# Autoriser CORS (important pour Hoppscotch, Postman, etc.)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tu peux restreindre à une URL précise si tu veux
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configurer Gemini avec la clé API
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

@app.get("/")
def home():
    return {"message": "Script-maker service is running!"}

@app.post("/generate")
async def generate(request: Request):
    body = await request.json()
    topic = body.get("topic", "psychologie")
    style = body.get("style", "youtube court")
    duration = body.get("duration_sec", 60)

    prompt = f"""
    Génère un script vidéo pédagogique en français sur le sujet : {topic}.
    - Style : {style}
    - Durée approximative : {duration} secondes
    - Donne le texte narratif découpé en scènes avec des timecodes
    - Ajoute des idées de visuels et des captions
    - Format JSON : 
    {{
        "title": "...",
        "scenes": [
            {{"time": "0-5s", "narration": "...", "caption": "...", "visual": "..."}},
            ...
        ]
    }}
    """

    model = genai.GenerativeModel("gemini-1.5-flash")
    response = model.generate_content(prompt)

    return {"topic": topic, "script": response.text}
