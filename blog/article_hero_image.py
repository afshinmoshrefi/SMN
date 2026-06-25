# article_hero_image.py
# V8: Final version with improved, prescriptive prompts for Forex and Bonds.

import os
import re
import sys
import json
import argparse
import random
from typing import Optional, Dict, Tuple, Any

sys.path.insert(0, "/home/flask")
import config
import AI_tools
from article_tools import get_article_image_paths
from blog_tools import get_company_name


#########################################################################
# ===== GLOBAL MODEL ROUTING SWITCH =====
# auto: sends all to standard except the premium list go to premium
# standard: sends all to standard      $$0.0066
# premium : sends all to premium route $0.06 if premium is set to flux pro 1.1 ultra
# Options: "auto", "standard", "premium"
generation_routing = "premium"
#########################################################################

# Image display aspect ratios (for CLS)
HERO_WIDTH_ATTR = 1536   # real rendered px (1536x640) hero (Flux) -- for CLS
HERO_HEIGHT_ATTR = 640

CUSTOM_MOTIFS = json.load(open("ticker_motif_custom.json"))
PREMIUM_IMAGE_TRIGGERS = {
    # "keywords": ["aerospace","airline"],
    "keywords": [],
    "symbols": [
        "BA",   # Boeing
        "LMT",  # Lockheed Martin
        "RTX",  # Raytheon
        "GD",   # General Dynamics
        "NOC",  # Northrop Grumman
        # "UAL",  # United Airlines
        "DAL",  # Delta Air Lines
        "AAL",  # American Airlines
        "JBLU", # JetBlue
        "LUV" ,  # Southwest Airlines
        
    ]
}
# airline tickers use only 1 motif at a time - while other tickers use all of them
AIRLINE_TICKERS = {"UAL", "DAL", "AAL", "JBLU", "LUV","ALGT", "SKYW", "SAVE", "HA"}
# ------------------------------
# High-Level Context Mapping
# ------------------------------
RESOURCE_TO_MARKET = {
    "0": "STOCK", "1": "STOCK", "2": "STOCK", "3": "STOCK", "4": "STOCK",
    "5": "INDEX", "6": "INDEX", "7": "FUTURES", "8": "FX", "9": "FX",
    "10": "BONDS", "11": "ETF", "12": "STOCK", "13": "STOCK", "14": "STOCK",
    "15": "STOCK", "16": "CRYPTO",
}

SENTIMENT_TO_MOOD = {
    "bullish": "subtle warm highlights, brighter midtones, forward motion, optimistic but professional",
    "bearish": "cooler blue palette, restrained contrast, dusk or fog cues, cautious mood",
    "neutral": "balanced muted newsroom palette, even contrast, calm composition",
}

BANNED_WORDS = {"people", "person", "logo", "brand", "text", "flag", "politician", "watermark", "weaponized imagery", "war scene"}

# ------------------------------
# Structured Prompt Generation Logic (V8)
# ------------------------------

def _has_banned_word(s: str) -> bool:
    return any(re.search(rf"\b{re.escape(w)}\b", s, re.IGNORECASE) for w in BANNED_WORDS)

def _robust_json_parse(raw_text: str) -> Dict:
    try:
        return json.loads(raw_text.strip())
    except json.JSONDecodeError:
        match = re.search(r"```json\s*(\{.*?\})\s*```", raw_text, re.DOTALL)
        if match:
            try: return json.loads(match.group(1))
            except json.JSONDecodeError: pass
        start, end = raw_text.find("{"), raw_text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try: return json.loads(raw_text[start:end+1])
            except json.JSONDecodeError: pass
    return {}

def get_visual_concept(
    symbol: str, market: str, company_name: Optional[str],
    llm_model: str = "gpt-5-nano", retry_hint: Optional[str] = None
) -> Dict:
    name = company_name or symbol
    system = "You are a senior photo editor for Reuters. Your task is to classify a financial subject for an art director. Be specific and visual. Output valid JSON only."
    hint = f"\nHint: {retry_hint}" if retry_hint else ""

    if market == "FX":
        prompt = f"""
Classify the subject below for an art director.
Input:
- symbol: "{symbol}"
- name: "{name}"
- market_family: "{market}"
Rules:
- Output valid JSON only. No prose.
- "motifs" must create a scene combining abstract currency symbols with data. Examples: "translucent Euro and Dollar symbols overlapping", "glowing data streams flowing over a world map contour", "abstract digital currency exchange interface".
- Strictly avoid national flags, landmarks, or politicians.
Respond with a single JSON object with these exact keys: "sector", "entity_type", "motifs", "confidence".
"""
    elif market == "BONDS":
        prompt = f"""
Classify the subject below for an art director.
Input:
- symbol: "{symbol}"
- name: "{name}"
- market_family: "{market}"
Rules:
- Output valid JSON only. No prose.
- "motifs" must evoke government finance and economic stability. Examples: "stately marble columns of a central bank", "a subtle, glowing overlay of a yield curve chart", "formal facade of a treasury building", "abstract representation of sovereign debt".
Respond with a single JSON object with these exact keys: "sector", "entity_type", "motifs", "confidence".
"""
    else:
        prompt = f"""
Classify the subject below so an art director can choose the right editorial hero image.{hint}
Input:
- symbol: "{symbol}"
- name: "{name}"
- market_family: "{market}"
Rules:
- Output valid JSON only. No prose.
- "motifs" must be 3-5 concrete, real-world visual elements (e.g., "aircraft assembly line," "semiconductor wafer," "oil refinery towers"), not abstract buzzwords.
- Crypto motifs: modern finance and digital grids; strictly avoid sci-fi tropes or physical coins.
Respond with a single JSON object with these exact keys: "sector", "entity_type", "motifs", "confidence".
"""

    try:
        raw = AI_tools.send_openai_prompt(prompt, model=llm_model, system=system, reasoning_effort="minimal", stream=False, timeout=60)
        data = _robust_json_parse("".join(raw) if not isinstance(raw, str) else raw)
    except Exception as e:
        print(f"[WARN] Visual concept LLM call failed for {symbol}: {e}")
        data = {}

    motifs = data.get("motifs") if isinstance(data.get("motifs"), list) else []
    seen, clean_motifs = set(), []
    for m in (m.strip() for m in motifs if m):
        if m.lower() not in seen and not _has_banned_word(m):
            seen.add(m.lower())
            clean_motifs.append(m)

    if not clean_motifs:
        clean_motifs = {
            "STOCK": ["modern manufacturing line", "corporate glass architecture", "subtle market data reflections"],
            "FUTURES": ["industrial infrastructure", "storage tanks", "raw material textures"],
            "INDEX": ["financial district skyline", "abstract ticker reflections", "macro market grid"],
            "ETF": ["interlinked network lines", "world map contours", "diversified portfolio grid"],
            "FX": ["abstract euro & dollar motifs", "flowing data lines", "global finance grid"],
            "BONDS": ["marble civic columns", "yield curve lines", "government building facade"],
            "CRYPTO": ["digital asset grid", "circuitry patterns", "order-book abstractions"],
        }.get(market, ["clean financial skyline", "abstract data reflections"])

    sector_fallback = {"INDEX":"broad market", "ETF":"diversified", "FUTURES":"commodities", "FX":"currencies", "CRYPTO":"digital assets", "BONDS":"fixed income", "STOCK":"general"}.get(market)
    entity_fallback = {"INDEX":"index", "ETF":"etf", "FUTURES":"commodity", "FX":"currency", "CRYPTO":"crypto", "BONDS":"bond", "STOCK":"commercial"}.get(market)
    try: conf = float(data.get("confidence", 0.5))
    except (ValueError, TypeError): conf = 0.5

    return {
        "sector": (data.get("sector") or sector_fallback).strip().lower(),
        "entity_type": (data.get("entity_type") or entity_fallback).strip().lower(),
        "motifs": clean_motifs[:5],
        "confidence": max(0.0, min(1.0, conf)),
    }

def _retry_concept(symbol, market, company_name, llm_model):
    hint = "Your previous motifs were too generic. Return 3-5 concrete, physical, industrial, or architectural motifs. Use industrial/place nouns only (e.g., lines, jigs, towers, tanks, wafers, conveyors)."
    return get_visual_concept(symbol, market, company_name, llm_model, retry_hint=hint)

def _is_weak_concept(concept: Dict) -> bool:
    generic_terms = {"solutions", "innovation", "leader", "business", "company", "technology", "platform", "services"}
    motifs = concept.get("motifs", [])
    if len(motifs) < 2: return True
    return any(term in " ".join(motifs).lower() for term in generic_terms)

def _sanitize_final_prompt(s: str, market: str, *, skip_banned: bool = False) -> str:
    """
    Sanitizes the final image prompt.
    - If skip_banned=True (custom motifs): do NOT remove banned words and do NOT add "no people / no text / no logos".
    - If skip_banned=False (LLM motifs): normal sanitization applies.
    """

    # Strip whitespace and quotes
    s = re.sub(r"[\r\n]+", " ", s or "").strip().strip("\"' ")

    # 1. Remove banned words ONLY when NOT using custom motifs
    if not skip_banned:
        for w in BANNED_WORDS:
            s = re.sub(rf"\b{re.escape(w)}(s?)\b", "", s, flags=re.IGNORECASE)

    # Collapse duplicate spaces
    s = re.sub(r"\s{2,}", " ", s).strip()

    # 2. Length control (safe truncate)
    if len(s) > 350:
        s = s[:350]
        last_break = max(s.rfind('.'), s.rfind(','), s.rfind(';'))
        if last_break != -1:
            s = s[:last_break]

    # 3. Add restrictions ONLY for non-custom motifs
    additions = []
    if not skip_banned:
        # if "16:9" not in s and "landscape" not in s:
        #     additions.append("wide 16:9 aspect ratio")
        if "no people" not in s.lower():
            additions.append("no people")
        if "no text" not in s.lower():
            additions.append("no text")
        if "no logos" not in s.lower():
            additions.append("no logos")
        if "no brand products" not in s.lower():
            additions.append("no brand products")
        if market == "FX" and "no flags" not in s.lower():
            additions.append("no flags or national landmarks")

    # Combine if needed
    if additions:
        s = s.rstrip(",; ") + ", " + ", ".join(additions)

    # Final cleanup
    s = s.replace(" ,", ",").replace(" ;", ";")
    s = re.sub(r'\s+,', ',', s)

    return s.strip()

def _time_hint(tc: str) -> str:
    tc = str(tc).lower()
    if "pre" in tc or "open" in tc: return "pre-market calm, cool early morning light"
    if "close" in tc or "bell" in tc: return "late-afternoon glow, longer shadows"
    if re.match(r"\d{4}-\d{2}-\d{2}", tc): return "contemporary market session lighting"
    return "neutral newsroom lighting"

def _build_final_prompt_from_custom(symbol, company_name, market, motifs_txt,
                                    sentiment, time_context, llm_model, temperature, concept):
    mood_description = SENTIMENT_TO_MOOD.get(sentiment.lower(), SENTIMENT_TO_MOOD["neutral"])
    lighting_hint = _time_hint(time_context)

    # Build final prompt WITHOUT calling an LLM and WITHOUT sanitizing banned words
    base_prompt = (
        f"{motifs_txt}, "
        f"with {lighting_hint}, in a photorealistic, clean, institutional, cinematic style, "
        f"{mood_description}, generous negative space"
    )

    final = _sanitize_final_prompt(base_prompt, market, skip_banned=True)
    return final, concept


def generate_hero_prompt(
    resource_id: str, symbol: str, *, company_name: Optional[str] = None,
    sentiment: str = "neutral", time_context: str = "today", llm_model: str = "gpt-5-nano",
    temperature: float = 0.25
) -> Tuple[str, Dict]:
    market = _normalize_market(resource_id)
    sym = symbol.upper()

    # -------------------------
    # CUSTOM MOTIF OVERRIDE
    # -------------------------
    # Try market-specific key first (e.g. "CL|STOCK" vs "CL|FUTURES"), fall back to plain symbol
    market_key = f"{sym}|{market}"
    motif_key = market_key if market_key in CUSTOM_MOTIFS else sym
    if motif_key in CUSTOM_MOTIFS:
        motifs = CUSTOM_MOTIFS[motif_key]["motifs"]
        if not motifs:
            raise RuntimeError(f"CUSTOM_MOTIFS[{motif_key}] has empty motifs list")

        motifs_txt = random.choice(motifs)

        concept = {
            "sector": "custom",
            "entity_type": "custom",
            "motifs": motifs,
            "confidence": 1.0
        }

        print(f"[INFO] Using custom motif for {motif_key}: {motifs_txt}")

        return _build_final_prompt_from_custom(
            motif_key, company_name, market, motifs_txt,
            sentiment, time_context, llm_model, temperature, concept
        )

    # -------------------------
    # DEFAULT LLM-DRIVEN FLOW
    # -------------------------
    concept = get_visual_concept(symbol, market, company_name, llm_model)
    if _is_weak_concept(concept):
        print(f"[INFO] Weak concept for {symbol}, attempting one guided retry...")
        concept = _retry_concept(symbol, market, company_name, llm_model)

    print(f"[INFO] Phase 1 Structured Concept for {symbol}: {json.dumps(concept, indent=2)}")

    mood_description = SENTIMENT_TO_MOOD.get(sentiment.lower(), SENTIMENT_TO_MOOD["neutral"])
    motifs_txt = ", ".join(concept.get("motifs", []))
    system = (
        "You are a top-tier financial editorial art director for Bloomberg. "
        "You write final, detailed prompts for a Stable Diffusion 3.5 Large image generator. "
        "Never use words that could be interpreted as NSFW: bathing, sensual, intimate, "
        "moody, provocative, or anything similar."
    )

    art_director_prompt = f"""
Translate this structured brief into a final, cinematic hero-image prompt.
BRIEF:
- Subject: {company_name or symbol} ({symbol})
- Context: {market} ({concept['sector']})
- Core Visual Motifs: {motifs_txt}
- Mood Direction: {sentiment} ({mood_description})
- Lighting Hint: {_time_hint(time_context)}
HARD RULES FOR FINAL IMAGE:
- Style: Photorealistic, clean, institutional, cinematic.
- Composition: Wide cinematic landscape with generous negative space.
- Content Restrictions: Absolutely NO people, text, logos, flags, or recognizable brand products.
OUTPUT:
Return two to three descriptive sentences for the image generator. Describe the scene, lighting, and composition in vivid, specific detail. Each sentence should add a distinct visual layer (subject, atmosphere, composition/style).
"""

    final_prompt = AI_tools.send_openai_prompt(
        art_director_prompt,
        model=llm_model,
        system=system,
        reasoning_effort="minimal",
        stream=False,
        timeout=90,
    )

    sanitized_prompt = _sanitize_final_prompt(
        "".join(final_prompt) if not isinstance(final_prompt, str) else final_prompt,
        market
    )
    return sanitized_prompt, concept
# ------------------------------
# Utility and Execution Functions
# ------------------------------

def _normalize_market(resource_id: str) -> str:
    return RESOURCE_TO_MARKET.get(str(resource_id), "STOCK")

def generate_hero_image(
    hero_prompt: str, concept_brief: Dict, hero_output_path: str, symbol: str,
    *, width: int = 1200, height: int = 600, date: str = "",
) -> Optional[str]:
    """
    (MODIFIED) This is now the Smart Router.
    It decides which model to use based on the symbol and the sector from the concept_brief.
    """
    # --- Save audit files ---

    hero_image_folder = os.path.dirname(hero_output_path)
    base_name, _ = os.path.splitext(os.path.basename(hero_output_path))
    os.makedirs(hero_image_folder, exist_ok=True)
    prompt_path = os.path.join(hero_image_folder, f"{base_name}.txt")
    concept_path = os.path.join(hero_image_folder, f"{base_name}.concept.json")




    with open(prompt_path, 'w') as f: f.write(hero_prompt)
    with open(concept_path, 'w') as f: json.dump(concept_brief, f, indent=2)

    # --- Prepare common parameters for the generation call ---
    negative_prompt = (
        "people, person, text, logo, watermark, flags, politicians, "
        "blurry, low quality, cartoon, anime, sci-fi coins, "
        "distorted proportions, over-saturated colors, HDR artifacts, "
        "fantasy elements, unrealistic architecture, noise, grain"
    )
    # Include date so each article gets a fresh seed rather than the same image forever
    seed = hash(f"{concept_brief.get('entity_type','')}-{concept_brief.get('sector','')}-{base_name}-{date}") & 0xFFFFFFFF
    sector = concept_brief.get("sector", "").lower()

    # --- SMART ROUTER LOGIC ---
    use_premium_model = False
    # 1. Check if the symbol is in our explicit list
    if symbol.upper() in PREMIUM_IMAGE_TRIGGERS["symbols"]:
        use_premium_model = True
        print(f"[ROUTER] Match found for symbol '{symbol}'. Routing to PREMIUM model.")
    # 2. If not, check if any of our keywords are in the company's sector
    else:
        for keyword in PREMIUM_IMAGE_TRIGGERS["keywords"]:
            if keyword in sector:
                use_premium_model = True
                print(f"[ROUTER] Match found for keyword '{keyword}' in sector '{sector}'. Routing to PREMIUM model.")
                break

    # --- GLOBAL MODEL OVERRIDE ---
    if generation_routing == "standard":
        use_premium_model = False
        print("[ROUTER] Global override: forcing STANDARD model.")
    elif generation_routing == "premium":
        use_premium_model = True
        print("[ROUTER] Global override: forcing PREMIUM model.")

   # --- Execute the chosen model ---
    if use_premium_model:
        try:
            image_url = AI_tools.generate_AI_Image_premium(
                prompt=hero_prompt,
                image_filepath=hero_output_path,
                width=width,
                height=height,
                negative_prompt=negative_prompt,
                seed=seed,
                remove_background=False,
            )
        except Exception as e:
            if "nsfw" in str(e).lower() or "safety" in str(e).lower() or "content detected" in str(e).lower():
                print(f"[ROUTER] NSFW on premium — retrying with sanitized safe prompt on premium...")
                sector = concept_brief.get('sector', 'financial services')
                safe_prompt = (
                    f"Wide cinematic editorial photograph of institutional architecture and industrial infrastructure, "
                    f"{sector} industry context, clean modern composition with generous negative space, "
                    f"photorealistic professional photography style, neutral even lighting, "
                    f"no people, no text, no logos, no brand products"
                )
                image_url = AI_tools.generate_AI_Image_premium(
                    prompt=safe_prompt,
                    image_filepath=hero_output_path,
                    width=width,
                    height=height,
                    negative_prompt=negative_prompt,
                    seed=(seed + 1) & 0xFFFFFFFF,
                    remove_background=False,
                )
            else:
                raise
    else:
        print(f"[ROUTER] No match for '{symbol}' or its sector. Using DEFAULT model.")
        image_url = AI_tools.generate_AI_Image_sdxl(
            prompt=hero_prompt,
            image_filepath=hero_output_path,
            width=width,
            height=height,
            negative_prompt=negative_prompt,
            seed=seed,
        )
    
    return hero_output_path if image_url else None


# ------------------------------
# Consolidated Workflow Function (NEW)
# ------------------------------
def hero_image_workflow(
    resource_id: str,
    symbol: str,
    date: str,
    sentiment: str = "neutral",
    company_name: Optional[str] = None,
    llm_model: str = "gpt-5-nano",
    temperature: float = 0.25,
    width: int = 2176,
    height: int = 960,
    article_id: str = "",
) -> Dict[str, Any]:
    """
    This is the main entry point. It orchestrates the entire process.
    """
    # Step 1: Resolve company name
    name_to_use = (company_name or get_company_name(resource_id, symbol) or symbol)
    print(f"[INFO] Using name: '{name_to_use}' for symbol '{symbol}'")

    # Step 2: Determine output paths
    filename_symbol = symbol.replace('/', '_').replace('=', '_')
    # Include article_id in filename to prevent collisions when multiple articles
    # share the same symbol + pattern start date (same directory).
    if article_id:
        hero_filename = f"hero_{filename_symbol}_{article_id}.jpg"
    else:
        hero_filename = f"hero_{filename_symbol}.jpg"
    _, hero_path, _, hero_url = get_article_image_paths(resource_id, date, symbol, hero_filename)
    print(f"[INFO] Canonical image path set to: {hero_path}")


    # Step 3: Generate the creative prompt and the concept (which contains the sector)
    print(f"[INFO] Generating hero prompt for {symbol} ({name_to_use})...")
    hero_prompt, concept_brief = generate_hero_prompt(
        resource_id=resource_id, symbol=symbol, company_name=name_to_use,
        sentiment=sentiment, time_context=date, llm_model=llm_model, temperature=temperature
    )
    print("\n--- Final Hero Prompt ---\n" + hero_prompt + "\n-------------------------")

    # Step 4: Generate the image using the smart router
    print(f"[INFO] Generating hero image at path: {hero_path}")
    final_path = generate_hero_image(
        hero_prompt=hero_prompt,
        concept_brief=concept_brief,
        hero_output_path=hero_path,
        symbol=symbol,
        width=width,
        height=height,
        date=date,
    )

    # Step 5: Handle failure and return results
    if not final_path:
        print(f"[FATAL] Hero image generation failed for {symbol}. No image was created.")
        return { "error": "Image generation failed.", "image_path": None, "image_url": None, "prompt": hero_prompt, "concept": concept_brief }

    print(f"\n[SUCCESS] Saved hero image: {final_path}")
    print(f"[INFO] Audit files (prompt/concept) saved in: {os.path.dirname(final_path)}")
    return { "image_path": final_path, "image_url": hero_url, "prompt": hero_prompt, "concept": concept_brief }
# ------------------------------
# CLI
# ------------------------------
def _build_argparser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Generate a newsroom-style hero image prompt and render the image.")
    ap.add_argument("resource_id", help="resource_id from config (e.g., '2' for S&P 500 stocks)")
    ap.add_argument("symbol", help="Ticker or symbol (e.g., AAPL, CL, EUR/USD)")
    ap.add_argument("--company", default=None, help="Company or instrument display name (optional)")
    ap.add_argument("--sentiment", default="neutral", choices=["bullish", "bearish", "neutral"], help="Mood")
    ap.add_argument("--time", default="today", help="Time context string for lighting cues")
    ap.add_argument("--llm", default="gpt-5-nano", help="LLM model name")
    ap.add_argument("--temp", type=float, default=0.1, help="LLM temperature for Phase 2")
    ap.add_argument("--outdir", required=True, help="Folder to save the hero image")
    ap.add_argument("--name", default=None, help="Output filename (defaults to hero_{SYMBOL}.jpg)")
    ap.add_argument("--width", type=int, default=1200, help="Image width")
    ap.add_argument("--height", type=int, default=600, help="Image height")
    return ap

#--------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    # --- Smoketest for the consolidated hero image workflow (UPDATED) ---

    # Define the inputs for the workflow
    test_resource_id = "5"  
    test_symbol = "SPX"
    test_date = "2026-01-01"
    test_sentiment = "neutral"

    print(f"--- Starting Hero Image Workflow for {test_symbol} ---")

    # Call the single, consolidated function
    result = hero_image_workflow(
        resource_id=test_resource_id,
        symbol=test_symbol,
        date=test_date,
        sentiment=test_sentiment,
        # company_name is omitted to test the automatic lookup functionality
        # width=1200,
        # height=600
        width = 3136,
        height=1344
    )

    print("\n--- Workflow Complete ---")
    print(f"Final Image Path: {result['image_path']}")
    print(f"Final Image URL:  {result['image_url']}")
    print("-------------------------\n")
    
    # You can also access the other outputs for debugging or logging:
    # print("Concept Brief:")
    # print(json.dumps(result['concept'], indent=2))
    # print("\nFinal Prompt:")
    # print(result['prompt'])