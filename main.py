from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
import os, json, re, io, csv
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

# --- Guide "experts" + règles par style (FR OBLIGATOIRE) + VISUEL + CTA ---
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
- Chaque 'point' contient un 'example' réel/plausible et un 'broll' concret.
- ≥ 1 'micro_action' réalisable en <10 s.
- 'proof' cite 1 source courte si utile : Auteur/Revue/Année (ex: "Tversky & Kahneman, Science, 1974").
- **CTA OBLIGATOIRE** : 'cta.text' doit demander explicitement un **like** ET de **s'abonner/suivre la chaîne**.
- 'caption' ≤ 8 mots.
- 'risk_flags' si promesse exagérée, source floue, vocabulaire médical excessif.
- Si conseil santé : 'disclaimer' court ("Ne remplace pas un avis professionnel").
- 'metrics_hypothesis' = raisons de performance (ex: "défi 10s", "question commentable").
- 'reuse_assets' = true si réutilisation possible.

SPÉCIFICITÉS PAR STYLE :
- docu : ton sérieux, visuels ciné (fond sombre, plans serrés), 'proof' OBLIGATOIRE, captions sobres.
- viral : rythme rapide, ≥ 1 "pattern_interrupt" par beat, ≥ 1 "micro_action" < 10 s.
- quiz :
   - hook = QUESTION + 3 options **A/B/C** dans le même champ 'text',
   - point(5-15) = révélation de la bonne réponse,
   - point(15-30) = fun fact + mini explication,
   - proof facultatif,
   - CTA = "Like si tu t’es trompé, abonne-toi pour d’autres quiz en français."
- Style demandé: {style}; Durée cible: {duration} s; Sujet: "{topic}".
NE JAMAIS SORTIR DU FORMAT JSON.
"""

# ----------------- Gemini helpers -----------------
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
    return {"luminosity":"neutre","contrast":"moyen","color_palette":"neutres",
            "transitions":["cut sec","pop-in réponses"],"effects":["texte animé","split screen"],
            "overall_style":"sobre éducatif"}

def suggest_hashtags(topic: str, style: str):
    base = ["#psychologie", "#cerveau", "#science", "#neurosciences", "#apprendre", "#fyp", "#pourtoi"]
    if style == "viral":
        extra = ["#viral", "#shorts", "#tiktokfr", "#buzz"]
    elif style == "docu":
        extra = ["#documentaire", "#culture", "#connaissance", "#éducation"]
    else:
        extra = ["#quiz", "#jeu", "#challenge", "#test"]
    topic_tag = "#" + re.sub(r"[^a-z0-9]", "", topic.lower())
    seen, out = set(), []
    for t in base + extra + [topic_tag]:
        if t not in seen and t:
            out.append(t); seen.add(t)
    return out[:8]

def normalize_sections(data, style: str, duration: int, topic_for_tags: str):
    secs = data.get("sections", [])
    types = [s.get("type") for s in secs]

    if "hook" not in types:
        secs.insert(0, {"type":"hook","time":"0-5","text":"Ton cerveau te joue des tours.",
                        "caption":"Ton cerveau te trompe","broll":"plan serré visage",
                        "pattern_interrupt":"cut rapide"})
    if types.count("point") < 2:
        secs.append({"type":"point","time":"5-15","text":"Exemple simple et concret.",
                     "caption":"Tu l’as vécu ?","broll":"texte animé","example":"situation quotidienne"})
        secs.append({"type":"point","time":"15-30","text":"Mini action à tester maintenant.",
                     "caption":"Teste-le","broll":"mains + téléphone","micro_action":"essaie pendant 10s"})
    if "proof" not in types:
        secs.append({"type":"proof","time":"30-40","text":"Observation étayée.","source":"—"})

    cta_idx = next((i for i,s in enumerate(secs) if s.get("type")=="cta"), None)
    secs.append(ensure_cta_like_follow(None)) if cta_idx is None else \
        secs.__setitem__(cta_idx, ensure_cta_like_follow(secs[cta_idx]))

    if style == "quiz":
        for s in secs:
            if s.get("type") == "hook":
                if not re.search(r"\bA\)?\b.*\bB\)?\b.*\bC\)?\b", s.get("text",""), re.I|re.S):
                    s["text"] = (s.get("text","Question ?") + " A) Option A  B) Option B  C) Option C").strip()
                break
        secs[-1]["text"] = "Like si tu t’es trompé, et abonne-toi pour d’autres quiz en français."
        secs[-1]["caption"] = "Like + Abonne-toi"

    data["sections"] = secs

    if "visual_style" not in data or not isinstance(data.get("visual_style"), dict):
        data["visual_style"] = default_visual_style(style)

    data["duration_sec"] = max(30, min(int(data.get("duration_sec", duration)), 60))

    title_or_topic = data.get("title") or topic_for_tags
    data["hashtags"] = suggest_hashtags(title_or_topic, style)

    data.setdefault("disclaimer", "Contenu éducatif. Ne remplace pas un avis professionnel.")
    data.setdefault("risk_flags", [])
    data.setdefault("metrics_hypothesis", ["hook fort", "micro-action <10s", "question commentable"])
    data.setdefault("reuse_assets", True)
    return data

# ----------------- Utils export -----------------
def _parse_time_range(t: str):
    """'5-12' -> (00:00:05,000, 00:00:12,000) pour SRT."""
    try:
        start_s, end_s = [int(float(x)) for x in re.split(r"[-–]", t.strip())[:2]]
    except Exception:
        start_s, end_s = 0, 3
    def fmt(s):
        h = s // 3600; m = (s % 3600) // 60; sec = s % 60
        return f"{h:02}:{m:02}:{sec:02},000"
    return fmt(start_s), fmt(end_s)

def build_srt(sections):
    lines, idx = [], 1
    for s in sections:
        if "time" not in s: continue
        start, end = _parse_time_range(s["time"])
        text = s.get("caption") or s.get("text") or ""
        text = re.sub(r"\s+", " ", text).strip()
        if not text: continue
        lines += [str(idx), f"{start} --> {end}", text, ""]
        idx += 1
    return "\n".join(lines).strip() + "\n"

def build_voiceover(sections, title):
    parts = [f"Titre: {title}", ""]
    for s in sections:
        t = s.get("type")
        if t in ("hook","point","proof","cta"):
            txt = s.get("text","").strip()
            if txt:
                parts.append(txt)
    parts.append("")
    parts.append("👉 Si tu as appris un truc, mets un like et abonne-toi à Synaptik Minutes.")
    return "\n".join(parts).strip() + "\n"

def build_shotlist_csv(sections):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["time_start","time_end","type","action","broll","notes"])
    for s in sections:
        ts = s.get("time","0-0")
        start, end = _parse_time_range(ts)
        writer.writerow([
            start, end, s.get("type",""),
            s.get("caption","") or s.get("text","")[:60],
            s.get("broll",""),
            s.get("pattern_interrupt","") or s.get("micro_action","")
        ])
    return buf.getvalue()

def build_storyboard_md(script):
    s = script
    lines = [f"# {s.get('title','Storyboard')}", ""]
    vs = s.get("visual_style", {})
    if vs:
        lines += ["**Style visuel** :",
                  f"- Luminosité : {vs.get('luminosity','')}",
                  f"- Contraste : {vs.get('contrast','')}",
                  f"- Palette : {vs.get('color_palette','')}",
                  f"- Transitions : {', '.join(vs.get('transitions',[]))}",
                  f"- Effets : {', '.join(vs.get('effects',[]))}",
                  f"- Global : {vs.get('overall_style','')}", ""]
    for sec in s.get("sections", []):
        lines += [f"## {sec.get('type','').upper()}  ({sec.get('time','')})",
                  f"- **Texte** : {sec.get('text','')}",
                  f"- **Caption** : {sec.get('caption','')}",
                  f"- **B-roll** : {sec.get('broll','')}",
                  f"- **Notes** : {sec.get('pattern_interrupt','') or sec.get('micro_action','')}", ""]
    lines += ["---", f"Hashtags : {' '.join(s.get('hashtags', []))}"]
    return "\n".join(lines).strip() + "\n"

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

    try:
        data = force_json(raw)
    except Exception:
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
        "raw": raw
    }

@app.post("/improve")
async def improve(request: Request):
    payload = await request.json()
    script = payload.get("script")
    if not script:
        return {"error": "Body must include 'script' (JSON de ton script)"}
    style = script.get("style", "viral")
    duration = int(script.get("duration_sec", 45))
    topic = script.get("title", "psychologie")

    prompt = (
        "Tu es un comité d'experts qui optimise le script ci-dessous pour maximiser la rétention et l'engagement. "
        "Renvoie EXCLUSIVEMENT le JSON du script amélioré, en FRANÇAIS, même schéma. "
        "CTA doit demander like + abonnement. "
        f"Style: {style}, Durée cible: {duration}s.\n\n"
        f"Script à améliorer:\n```json\n{json.dumps(script, ensure_ascii=False)}\n```"
    )
    raw = ask_gemini(prompt)
    try:
        improved = force_json(raw)
    except Exception:
        improved = script  # fallback

    improved = normalize_sections(improved, style, duration, topic_for_tags=topic)
    return {"improved_script": improved, "raw": raw}

@app.post("/lint")
async def lint(request: Request):
    payload = await request.json()
    script = payload.get("script", payload)
    style = script.get("style", "viral")
    duration = int(script.get("duration_sec", 45))

    issues = []
    cta = next((s for s in script.get("sections", []) if s.get("type")=="cta"), None)
    if not cta or not re.search(r"like", cta.get("text",""), re.I) or \
       not re.search(r"(abonne|suis|suivre)", cta.get("text",""), re.I):
        issues.append("CTA incomplet : il doit demander like + abonnement (FR).")

    for s in script.get("sections", []):
        cap = s.get("caption","")
        if cap and len(cap.split()) > 8:
            issues.append(f"Caption trop longue: '{cap}'")

    if "visual_style" not in script or not isinstance(script.get("visual_style"), dict):
        issues.append("visual_style manquant : luminosity/contrast/palette/transitions/effects/overall_style.")
    if "hashtags" not in script or not script.get("hashtags"):
        issues.append("hashtags manquants.")

    fixed = normalize_sections(script, style, duration, topic_for_tags=script.get("title","topic"))
    return {"issues": list(set(issues)), "fixed_script": fixed}

@app.post("/export")
async def export_assets(request: Request):
    """
    Transforme un script en livrables texte.
    Body:
    {
      "script": {...},
      "formats": ["storyboard","captions","voiceover","shotlist"]  // optionnel, par défaut tout
    }
    """
    payload = await request.json()
    script = payload.get("script")
    if not script:
        return {"error": "Body must include 'script'."}
    formats = payload.get("formats", ["storyboard","captions","voiceover","shotlist"])

    # Normaliser au cas où
    style = script.get("style","viral")
    duration = int(script.get("duration_sec", 45))
    script = normalize_sections(script, style, duration, topic_for_tags=script.get("title","topic"))

    out = {}
    if "captions" in formats:
        out["captions_srt_filename"] = "captions.srt"
        out["captions_srt"] = build_srt(script.get("sections", []))
    if "voiceover" in formats:
        out["voiceover_txt_filename"] = "voiceover.txt"
        out["voiceover_txt"] = build_voiceover(script.get("sections", []), script.get("title","Voix off"))
    if "shotlist" in formats:
        out["shotlist_csv_filename"] = "shotlist.csv"
        out["shotlist_csv"] = build_shotlist_csv(script.get("sections", []))
    if "storyboard" in formats:
        out["storyboard_md_filename"] = "storyboard.md"
        out["storyboard_md"] = build_storyboard_md(script)

    return {"exports": out, "meta": {"title": script.get("title"), "style": style, "duration_sec": duration}}
