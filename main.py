from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
import os, json, re
import google.generativeai as genai

app = FastAPI()

# --- CORS (tests depuis navigateur / Hoppscotch/Postman) ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tu peux restreindre à une URL précise si tu veux
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Config Gemini ---
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
MODEL_NAME = "gemini-1.5-flash"

# --- Guide "experts" + règles par style (FR OBLIGATOIRE) + VISUEL + CTA ---
STRUCTURE_GUIDE = r"""
Tu es un comité de 6 experts (neurosciences, scénariste TikTok, growth, montage, analyste data, éthique).
TA RÉPONSE DOIT ÊTRE EXCLUSIVEMENT EN FRANÇAIS et en JSON VALIDE (UTF-8), sans texte autour.

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
- Chaque 'point' contient un 'example' réel/plausible et un 'broll' concret.
- ≥ 1 'micro_action' réalisable en <10 s.
- 'proof' cite 1 source courte si utile : Auteur/Revue/Année (ex: "Tversky & Kahneman, Science, 1974").
- CTA OBLIGATOIRE : 'cta.text' demande explicitement un like ET de s'abonner/suivre la chaîne.
- 'caption' ≤ 8 mots.
- 'risk_flags' si promesse exagérée, source floue, vocabulaire médical excessif.
- Si conseil santé : 'disclaimer' court ("Ne remplace pas un avis professionnel").
- 'metrics_hypothesis' = raisons de performance (ex: "défi 10s", "question commentable").
- 'reuse_assets' = true si réutilisation possible.

SPÉCIFICITÉS PAR STYLE :
- docu : ton sérieux, visuels ciné (fond sombre, plans serrés), 'proof' OBLIGATOIRE, captions sobres.
- viral : rythme rapide, ≥ 1 "pattern_interrupt" par beat, ≥ 1 "micro_action" < 10 s.
- quiz :
   - hook = QUESTION + 3 options A/B/C dans le même champ 'text',
   - point(5-15) = révélation de la bonne réponse,
   - point(15-30) = fun fact + mini explication,
   - proof facultatif,
   - CTA = "Like si tu t’es trompé, abonne-toi pour d’autres quiz en français."
- Style demandé: {style}; Durée cible: {duration} s; Sujet: "{topic}".
NE JAMAIS SORTIR DU FORMAT JSON.
"""

# ----------------- Utils Gemini & JSON -----------------
def ask_gemini(prompt: str) -> str:
    model = genai.GenerativeModel(MODEL_NAME)
    resp = model.generate_content(prompt)
    return getattr(resp, "text", str(resp)).strip()

def force_json(txt: str):
    """Parse JSON ; si échec, tente d'extraire le bloc { ... }."""
    try:
        return json.loads(txt)
    except Exception:
        start = txt.find("{"); end = txt.rfind("}")
        if start != -1 and end != -1:
            return json.loads(txt[start:end+1])
        raise

# ----------------- Normalisation / garanties -----------------
def ensure_cta_like_follow(section):
    """Corrige le CTA pour exiger 'like' et 'abonne/suis' en FR."""
    if not section or section.get("type") != "cta":
        return {
            "type":"cta","time":"40-45",
            "text":"Si tu as appris un truc, mets un like et abonne-toi pour d’autres vidéos.",
            "caption":"Like + Abonne-toi"
        }
    text = section.get("text","").lower()
    if ("like" not in text) or (("abonne" not in text) and ("suis" not in text) and ("suivre" not in text)):
        section["text"] = "Si tu as appris un truc, mets un like et abonne-toi pour d’autres vidéos."
    section.setdefault("caption","Like + Abonne-toi")
    section.setdefault("time","40-45")
    return section

def default_visual_style(style: str):
    if style == "docu":
        return {"luminosity":"sombre","contrast":"fort","color_palette":"froides",
                "transitions":["fondu enchaîné","cut sec"],"effects":["texte animé discret"],
                "overall_style":"ciné sombre"}
    if style == "viral":
        return {"luminosity":"clair","contrast":"moyen","color_palette":"saturées",
                "transitions":["cut sec","zoom rapide"],"effects":["texte animé","glitch léger"],
                "overall_style":"pop colorée"}
    # quiz
    return {"luminosity":"neutre","contrast":"moyen","color_palette":"neutres",
            "transitions":["cut sec","pop-in réponses"],"effects":["texte animé","split screen"],
            "overall_style":"sobre éducatif"}

def suggest_hashtags(topic: str, style: str):
    """Hashtags FR/EN pertinents TikTok/Shorts/Reels (max ~8)."""
    base = ["#psychologie", "#cerveau", "#science", "#neurosciences", "#apprendre", "#fyp", "#pourtoi"]
    if style == "viral":
        extra = ["#viral", "#shorts", "#tiktokfr", "#buzz"]
    elif style == "docu":
        extra = ["#documentaire", "#culture", "#connaissance", "#éducation"]
    else:
        extra = ["#quiz", "#jeu", "#challenge", "#test"]
    topic_tag = "#" + re.sub(r"[^a-z0-9]", "", topic.lower())
    tags = base + extra + [topic_tag]
    seen, out = set(), []
    for t in tags:
        if t not in seen and t:
            out.append(t); seen.add(t)
    return out[:8]

def normalize_sections(data, style: str, duration: int, topic_for_tags: str):
    """Assure sections minimales, CTA FR, visual_style, durée bornée, hashtags."""
    secs = data.get("sections", [])
    types = [s.get("type") for s in secs]

    # Hook
    if "hook" not in types:
        secs.insert(0, {"type":"hook","time":"0-5","text":"Ton cerveau te joue des tours.",
                        "caption":"Ton cerveau te trompe","broll":"plan serré visage",
                        "pattern_interrupt":"cut rapide"})
    # Minimum 2 points
    if types.count("point") < 2:
        secs.append({"type":"point","time":"5-15","text":"Exemple simple et concret.",
                     "caption":"Tu l’as vécu ?","broll":"texte animé","example":"situation quotidienne"})
        secs.append({"type":"point","time":"15-30","text":"Mini action à tester maintenant.",
                     "caption":"Teste-le","broll":"mains + téléphone","micro_action":"essaie pendant 10s"})
    # Proof
    if "proof" not in types:
        secs.append({"type":"proof","time":"30-40","text":"Observation étayée.","source":"—"})
    # CTA
    cta_idx = next((i for i,s in enumerate(secs) if s.get("type")=="cta"), None)
    secs.append(ensure_cta_like_follow(None)) if cta_idx is None else \
        secs.__setitem__(cta_idx, ensure_cta_like_follow(secs[cta_idx]))

    # Spécifique "quiz"
    if style == "quiz":
        for s in secs:
            if s.get("type") == "hook":
                if not re.search(r"\bA\)?\b.*\bB\)?\b.*\bC\)?\b", s.get("text",""), re.I|re.S):
                    s["text"] = (s.get("text","Question ?") + " A) Option A  B) Option B  C) Option C").strip()
                break
        secs[-1]["text"] = "Like si tu t’es trompé, et abonne-toi pour d’autres quiz en français."
        secs[-1]["caption"] = "Like + Abonne-toi"

    data["sections"] = secs

    # Visual style
    if "visual_style" not in data or not isinstance(data.get("visual_style"), dict):
        data["visual_style"] = default_visual_style(style)

    # Durée bornée 30–60
    data["duration_sec"] = max(30, min(int(data.get("duration_sec", duration)), 60))

    # Hashtags
    title_or_topic = data.get("title") or topic_for_tags
    data["hashtags"] = suggest_hashtags(title_or_topic, style)

    # Champs complémentaires par défaut
    data.setdefault("disclaimer", "Contenu éducatif. Ne remplace pas un avis professionnel.")
    data.setdefault("risk_flags", [])
    data.setdefault("metrics_hypothesis", ["hook fort", "micro-action <10s", "question commentable"])
    data.setdefault("reuse_assets", True)
    return data

# ----------------- ROUTES -----------------
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
- Le bloc 'visual_style' doit être cohérent avec le style demandé.
RENVOIE UNIQUEMENT LE JSON.
"""
    raw = ask_gemini(prompt)

    # Parsing + normalisation
    try:
        data = force_json(raw)
    except Exception:
        # Fallback minimal si JSON invalide
        data = {
            "title": f"{topic} ({duration}s)", "style": style, "duration_sec": duration,
            "sections": [
                {"type":"hook","time":"0-5","text":f"Et si {topic} te trompait ?",
                 "caption":topic,"broll":"gros plan visage","pattern_interrupt":"zoom rapide"},
                {"type":"point","time":"5-15","text":"Exemple concret.","caption":"Tu l’as vécu ?",
                 "broll":"texte animé","example":"situation quotidienne"},
                {"type":"point","time":"15-30","text":"Action à tester.","caption":"Teste-le",
                 "broll":"mains + téléphone","micro_action":"essaie 10 s"},
                {"type":"proof","time":"30-40","text":"Observation étayée.","source":"—"},
                {"type":"cta","time":"40-45","text":"Si tu as appris un truc, mets un like et abonne-toi.",
                 "caption":"Like + Abonne-toi"}
            ],
            "visual_style": default_visual_style(style),
        }

    data.setdefault("title", f"{topic} ({duration}s)")
    data["style"] = style
    data["duration_sec"] = duration
    data = normalize_sections(data, style, duration, topic_for_tags=topic)

    return {
        "topic": topic,
        "style": style,
        "duration_sec": data["duration_sec"],
        "script": data,
        "raw": raw  # utile pour debug
    }

@app.post("/lint")
async def lint(request: Request):
    """
    Valide/corrige un script JSON.
    Retourne: issues (liste) + fixed_script (normalisé).
    """
    payload = await request.json()
    script = payload.get("script", payload)
    style = script.get("style", "viral")
    duration = int(script.get("duration_sec", 45))

    issues = []

    # CTA présent + FR like/abo
    cta = next((s for s in script.get("sections", []) if s.get("type")=="cta"), None)
    if not cta or not re.search(r"like", cta.get("text",""), re.I) or \
       not re.search(r"(abonne|suis|suivre)", cta.get("text",""), re.I):
        issues.append("CTA incomplet : il doit demander like + abonnement (FR).")

    # captions <= 8 mots
    for s in script.get("sections", []):
        cap = s.get("caption","")
        if cap and len(cap.split()) > 8:
            issues.append(f"Caption trop longue: '{cap}'")

    # visual_style complet ?
    if "visual_style" not in script or not isinstance(script.get("visual_style"), dict):
        issues.append("visual_style manquant : luminosity/contrast/palette/transitions/effects/overall_style.")

    # hashtags présents ?
    if "hashtags" not in script or not script.get("hashtags"):
        issues.append("hashtags manquants.")

    fixed = normalize_sections(script, style, duration, topic_for_tags=script.get("title","topic"))
    return {"issues": list(set(issues)), "fixed_script": fixed}

@app.post("/improve")
async def improve(request: Request):
    """
    Améliore un script existant avec le comité d'experts.
    Retourne une version optimisée (CTA/captions/micro-actions/style/hashtags).
    """
    body = await request.json()
    script = body.get("script")

    if not script:
        return {"error": "Aucun script fourni. Envoie { \"script\": { ...ton JSON... } }"}

    style = script.get("style", "viral")
    duration = int(script.get("duration_sec", 45))

    prompt = f"""{STRUCTURE_GUIDE}

Améliore ce script déjà généré pour le rendre plus percutant, clair et engageant,
en respectant STRICTEMENT le schéma JSON attendu :
{json.dumps(script, ensure_ascii=False, indent=2)}

RENVOIE UNIQUEMENT LE JSON.
"""
    raw = ask_gemini(prompt)
    try:
        data = force_json(raw)
    except Exception:
        data = script  # fallback

    data = normalize_sections(data, style, duration, topic_for_tags=script.get("title","topic"))
    return {"improved_script": data, "raw": raw}
