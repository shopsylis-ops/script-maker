from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
import os, json, re
import google.generativeai as genai

app = FastAPI()

# CORS (tests depuis navigateur / Hoppscotch)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Config Gemini ---
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
MODEL_NAME = "gemini-1.5-flash"

# --- Guide "experts" + règles par style (FR OBLIGATOIRE) + VISUEL ---
STRUCTURE_GUIDE = r"""
Tu es un comité de 6 experts (neurosciences, scénariste TikTok, growth, montage, analyste data, éthique).
TA RÉPONSE DOIT ÊTRE EXCLUSIVEMENT EN **FRANÇAIS** et en **JSON VALIDE** (UTF-8), sans texte autour.

Schéma JSON attendu :
{
  "title": string,
  "style": "viral" | "docu" | "quiz",
  "duration_sec": number,
  "sections": [
    { "type":"hook",  "time":"0-5",  "text": string, "caption": string, "broll": string, "pattern_interrupt": string },
    { "type":"point", "time":"5-15", "text": string, "caption": string, "broll": string, "example": string },
    { "type":"point", "time":"15-30","text": string, "caption": string, "broll": string, "micro_action": string },
    { "type":"proof", "time":"30-40","text": string, "source": string },
    { "type":"cta",   "time":"40-45","text": string, "caption": string }
  ],
  "visual_style": {
    "luminosity": "sombre|clair|neutre",
    "contrast": "fort|moyen|doux",
    "color_palette": "froides|chaudes|saturées|neutres",
    "transitions": [string],
    "effects": [string],
    "overall_style": "ciné sombre|pop colorée|sobre éducatif"
  },
  "disclaimer": string,
  "risk_flags": [string],
  "metrics_hypothesis": [string],
  "reuse_assets": true
}

RÈGLES COMMUNES :
- FRANÇAIS OBLIGATOIRE. Phrases courtes, vocabulaire simple (expliquer tout jargon en ≤ 3 mots).
- Hook < 8 s, ≤ 12 mots, curiosité + enjeu personnel.
- Chaque 'point' contient un 'example' réel/plausible et un 'broll' concret (plan/objet/texte animé).
- Ajoute AU MOINS une 'micro_action' réalisable en <10 s (favorise sauvegardes).
- 'proof' cite 1 source courte si utile : Auteur/Revue/Année (ex: "Tversky & Kahneman, Science, 1974").
- **CTA OBLIGATOIRE** : 'cta.text' doit demander explicitement un **like** ET de **s'abonner/suivre la chaîne**.
  Exemples FR :
  - "Si tu as appris un truc, mets un like et abonne-toi pour d’autres hacks."
  - "Like si tu t’es reconnu, et suis la chaîne pour décoder ton cerveau."
- 'caption' ≤ 8 mots, verbes d’action.
- 'risk_flags' si promesse exagérée, source floue, vocabulaire médical excessif.
- Si conseil santé : 'disclaimer' court ("Ne remplace pas un avis professionnel").
- 'metrics_hypothesis' = pourquoi la vidéo peut performer (ex: "défi 10s", "question commentable").
- 'reuse_assets' = true si réutilisation de plans possible.

SPÉCIFICITÉS PAR STYLE :
- docu : ton sérieux, visuels ciné (fond sombre, plans serrés), 'proof' OBLIGATOIRE (Auteur/Revue/Année), captions sobres.
- viral : rythme rapide, ≥ 1 "pattern_interrupt" par beat, ≥ 1 "micro_action" < 10 s.
- quiz :
   - hook = QUESTION + 3 options **A/B/C** dans le même champ 'text',
   - point(5-15) = révélation de la bonne réponse (claire),
   - point(15-30) = fun fact + mini explication,
   - proof facultatif,
   - CTA = "Like si tu t’es trompé, abonne-toi pour d’autres quiz en français."
- Style demandé: {style}; Durée cible: {duration} s; Sujet: "{topic}".
NE JAMAIS SORTIR DU FORMAT JSON.
"""

def ask_gemini(prompt: str) -> str:
    model = genai.GenerativeModel(MODEL_NAME)
    resp = model.generate_content(prompt)
    return getattr(resp, "text", str(resp)).strip()

def force_json(txt: str):
    """Parse JSON ; si échec, tente d'extraire le bloc { ... }."""
    try:
        return json.loads(txt)
    except Exception:
        start = txt.find("{")
        end = txt.rfind("}")
        if start != -1 and end != -1:
            return json.loads(txt[start:end+1])
        raise

def ensure_cta_like_follow(section):
    """Corrige le CTA pour exiger 'like' et 'abonne/suis' en FR."""
    if not section or section.get("type") != "cta":
        return {
            "type":"cta",
            "time":"40-45",
            "text":"Si tu as appris un truc, mets un like et abonne-toi pour d’autres vidéos.",
            "caption":"Like + Abonne-toi"
        }
    text = section.get("text","").lower()
    if ("like" not in text) or (("abonne" not in text) and ("suis" not in text) and ("suivre" not in text)):
        section["text"] = "Si tu as appris un truc, mets un like et abonne-toi pour d’autres vidéos."
    if not section.get("caption"):
        section["caption"] = "Like + Abonne-toi"
    if not section.get("time"):
        section["time"] = "40-45"
    return section

def default_visual_style(style: str):
    if style == "docu":
        return {
            "luminosity": "sombre",
            "contrast": "fort",
            "color_palette": "froides",
            "transitions": ["fondu enchaîné","cut sec"],
            "effects": ["texte animé discret"],
            "overall_style": "ciné sombre"
        }
    if style == "viral":
        return {
            "luminosity": "clair",
            "contrast": "moyen",
            "color_palette": "saturées",
            "transitions": ["cut sec","zoom rapide"],
            "effects": ["texte animé","glitch léger"],
            "overall_style": "pop colorée"
        }
    # quiz
    return {
        "luminosity": "neutre",
        "contrast": "moyen",
        "color_palette": "neutres",
        "transitions": ["cut sec","pop-in réponses"],
        "effects": ["texte animé","split screen"],
        "overall_style": "sobre éducatif"
    }

def normalize_sections(data, style: str, duration: int):
    """Assure la présence des sections, du CTA FR, et d'un visual_style par défaut."""
    secs = data.get("sections", [])
    types = [s.get("type") for s in secs]

    # Hook
    if "hook" not in types:
        secs.insert(0, {"type":"hook","time":"0-5","text":"Ton cerveau te joue des tours.","caption":"Ton cerveau te trompe","broll":"plan serré visage","pattern_interrupt":"cut rapide"})

    # Points (au moins 2)
    if types.count("point") < 2:
        secs.append({"type":"point","time":"5-15","text":"Exemple simple et concret.","caption":"Tu l’as vécu ?","broll":"texte animé","example":"situation quotidienne"})
        secs.append({"type":"point","time":"15-30","text":"Mini action à tester maintenant.","caption":"Teste-le","broll":"mains + téléphone","micro_action":"essaie pendant 10s"})

    # Proof
    if "proof" not in types:
        secs.append({"type":"proof","time":"30-40","text":"Observation étayée.","source":"—"})

    # CTA
    cta_idx = next((i for i,s in enumerate(secs) if s.get("type")=="cta"), None)
    if cta_idx is None:
        secs.append(ensure_cta_like_follow(None))
    else:
        secs[cta_idx] = ensure_cta_like_follow(secs[cta_idx])

    # Ajustements spécifiques "quiz"
    if style == "quiz":
        # hook doit contenir A/B/C
        for s in secs:
            if s.get("type") == "hook":
                if not re.search(r"\bA\)?\b.*\bB\)?\b.*\bC\)?\b", s.get("text",""), flags=re.IGNORECASE | re.DOTALL):
                    s["text"] = (s.get("text","Question ?") + " A) Option A  B) Option B  C) Option C").strip()
                break
        # CTA quiz dédié
        secs[-1]["text"] = "Like si tu t’es trompé, et abonne-toi pour d’autres quiz en français."
        secs[-1]["caption"] = "Like + Abonne-toi"

    data["sections"] = secs

    # Visual style : si manquant, injecter un preset selon le style
    if "visual_style" not in data or not isinstance(data.get("visual_style"), dict):
        data["visual_style"] = default_visual_style(style)

    # Bornes simples sur la durée (30–60)
    if duration < 30: data["duration_sec"] = 30
    if duration > 60: data["duration_sec"] = 60

    return data

@app.get("/")
def home():
    return {"message": "Script-maker service is running!"}

@app.post("/generate")
async def generate(request: Request):
    body = await request.json()
    topic = body.get("topic", "psychologie")
    style = body.get("style", "viral")  # viral | docu | quiz
    duration = int(body.get("duration_sec", 45))

    prompt = f"""{STRUCTURE_GUIDE}

Sujet: "{topic}"
Style: {style}
Durée cible: {duration} secondes

Contraintes supplémentaires:
- Tous les champs texte doivent être en FRANÇAIS.
- Le 'cta' doit demander explicitement un like ET de s'abonner/suivre la chaîne.
- Le bloc 'visual_style' doit être rempli de manière cohérente avec le style demandé.
RENVOIE UNIQUEMENT LE JSON.
"""
    # Appel modèle
    model = genai.GenerativeModel(MODEL_NAME)
    resp = model.generate_content(prompt)
    raw = getattr(resp, "text", str(resp)).strip()

    # Parsing + normalisation
    try:
        data = force_json(raw)
    except Exception:
        # Fallback minimal si JSON invalide
        data = {
            "title": f"{topic} ({duration}s)",
            "style": style,
            "duration_sec": duration,
            "sections": [
                {"type":"hook","time":"0-5","text":f"Et si {topic} te trompait chaque jour ?","caption":topic,"broll":"gros plan visage","pattern_interrupt":"zoom rapide"},
                {"type":"point","time":"5-15","text":"Exemple simple et concret.","caption":"Tu l’as vécu ?","broll":"texte animé","example":"situation quotidienne"},
                {"type":"point","time":"15-30","text":"Mini action à tester maintenant.","caption":"Teste-le","broll":"mains + téléphone","micro_action":"essaie 10 s"},
                {"type":"proof","time":"30-40","text":"Observation étayée.","source":"—"},
                {"type":"cta","time":"40-45","text":"Si tu as appris un truc, mets un like et abonne-toi pour d’autres vidéos.","caption":"Like + Abonne-toi"}
            ],
            "visual_style": default_visual_style(style),
            "disclaimer":"Ne remplace pas un avis professionnel",
            "risk_flags":[],
            "metrics_hypothesis":["défi 10s","question commentable"],
            "reuse_assets": True
        }

    # champs minimum
    data.setdefault("title", f"{topic} ({duration}s)")
    data["style"] = style
    data["duration_sec"] = duration

    # normaliser selon règles + style + visuel
    data = normalize_sections(data, style, duration)

    return {
        "topic": topic,
        "style": style,
        "duration_sec": data["duration_sec"],
        "script": data,
        "raw": raw  # pour debug si besoin
    }
