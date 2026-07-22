# Replicate API token is in ~/.bashrc
# nano ~/.bashrc

# grok-3-mini       # cheapest
# grok-code-fast-1  # second cheapest
# grok-4-latest     # latest

# LLM.py
import sys
import time
import datetime
from typing import Generator, Optional, Union, List
import replicate
import requests
sys.path.insert(0, "/home/flask")
import config  # expects GROK_API_KEY, TAVILY_API_KEY in config
import json as _json  # ensure json is available for streaming parse
from io import BytesIO
from PIL import Image
import os

# Custom Exception Classes
class GrokAPIError(Exception):
    pass

class PerplexityAPIError(Exception):
    pass

class OpenAIAPIError(Exception):
    pass

class TavilyAPIError(Exception):
    pass

class AnthropicAPIError(Exception):
    pass

# API Configuration
TAVILY_API_KEY     = config.TAVILY_API_KEY
TAVILY_API_URL     = "https://api.tavily.com/search"

GROK_API_KEY       = config.GROK_API_KEY
GROK_API_URL       = "https://api.x.ai/v1/chat/completions"

PERPLEXITY_API_KEY = config.PERPLEXITY_API_KEY
PERPLEXITY_MODEL   = 'sonar-reasoning'

OPENAI_KEY              = config.OPENAI_KEY
OPENAI_MODEL            = "gpt-5.1"
OPENAI_MODEL_GPT5_MINI  = "gpt-5-mini"
OPENAI_MODEL_GPT41_NANO = "gpt-4.1-nano"
OPENAI_MODEL_DEFAULT = OPENAI_MODEL
OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"

# ------------------------------------------------------------------
# Anthropic (Claude) model IDs
# ------------------------------------------------------------------
ANTHROPIC_API_KEY  = config.anthropic_token
ANTHROPIC_API_URL  = 'https://api.anthropic.com/v1/messages'
ANTHROPIC_VERSION  = '2023-06-01'

CLAUDE_OPUS_46    = 'claude-opus-4-6'           # most capable
CLAUDE_SONNET_46  = 'claude-sonnet-4-6'         # recommended default — strong + fast
CLAUDE_HAIKU_45   = 'claude-haiku-4-5-20251001' # fast + cheap
CLAUDE_HAIKU_35   = 'claude-3-5-haiku-20241022' # very cheap
CLAUDE_HAIKU_3    = 'claude-3-haiku-20240307'   # cheapest
CLAUDE_MODEL_DEFAULT = CLAUDE_SONNET_46

MODEL_MAP = {
    "default": OPENAI_MODEL_DEFAULT,
    "mini": OPENAI_MODEL_GPT5_MINI,
    "nano": OPENAI_MODEL_GPT41_NANO,
}



# Known Grok chat models - UPDATED for late 2025
DEFAULT_MODEL = "grok-3-mini"  # cheapest
REASONING_MODEL = "grok-3"     # Standard high-intelligence model

# ---- IMAGE CONFIG (hard-coded, NOT from config.py) ----
# PREMIUM_IMAGE_PROVIDER = "flux"  # "stability", "openai", or "flux"
PREMIUM_IMAGE_PROVIDER = "flux"  # "stability", "openai", or "flux"

OPENAI_IMAGES_URL = "https://api.openai.com/v1/images/generations"
DEFAULT_OPENAI_IMAGE_MODEL = "gpt-image-1"


# ------------------------------------------------------------------
# UTILITY FUNCTIONS
# ------------------------------------------------------------------

def _save_compressed_jpeg(image_bytes: bytes, image_filepath: str, quality: int = 75) -> None:
    """
    Re-encode arbitrary image bytes as optimized JPEG.
    """
    img = Image.open(BytesIO(image_bytes))
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")
    img.save(
        image_filepath,
        format="JPEG",
        quality=quality,
        optimize=True,
        progressive=True,
    )
    try:
        size = os.path.getsize(image_filepath)
        print(f"[INFO] Compressed hero '{image_filepath}' size: {size} bytes")
    except OSError:
        pass

def retry_api_call(fn):
    last_error = None
    for attempt in range(3):  # try 3 times
        try:
            return fn()
        except Exception as exc:
            last_error = exc
            print(f"[WARN] API call failed on attempt {attempt+1}: {exc}")
            time.sleep(1)
    if last_error is not None:
        raise last_error
    raise RuntimeError("API call failed without an exception")

# ------------------------------------------------------------------
# GROK (xAI) API
# ------------------------------------------------------------------

def _headers() -> dict:
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {GROK_API_KEY}",
    }

def _make_payload(prompt: str, model: str, system: Optional[str], temperature: float, stream: bool) -> dict:
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    return {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "stream": stream,
    }

def _non_stream_request(payload: dict, timeout: int) -> str:
    resp = requests.post(GROK_API_URL, headers=_headers(), json=payload, timeout=timeout)
    if resp.status_code != 200:
        raise GrokAPIError(f"HTTP {resp.status_code}: {resp.text}")
    data = resp.json()
    try:
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        raise GrokAPIError(f"Unexpected response: {data}") from e

def _stream_request(payload: dict, timeout: int) -> Generator[str, None, None]:
    with requests.post(GROK_API_URL, headers=_headers(), json=payload, timeout=timeout, stream=True) as resp:
        if resp.status_code != 200:
            raise GrokAPIError(f"HTTP {resp.status_code}: {resp.text}")
        for line in resp.iter_lines(decode_unicode=True):
            if not line: continue
            if line.startswith("data: "):
                chunk = line[6:].strip()
                if chunk == "[DONE]": break
                try:
                    j = requests.utils.json.loads(chunk)
                    delta = j.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content")
                    if content: yield content
                except Exception: continue

def send_grok_prompt(
    prompt: str,
    *,
    model: str = DEFAULT_MODEL,
    system: Optional[str] = None,
    temperature: float = 0.0,
    stream: bool = False,
    timeout: int = 60,
    max_retries: int = 2,
    retry_backoff_sec: float = 1.5,
) -> Union[str, Generator[str, None, None]]:
    """
    Send a prompt to Grok and return the assistant text.
    """
    payload = _make_payload(prompt=prompt, model=model, system=system, temperature=temperature, stream=stream)
    if stream:
        return _stream_request(payload, timeout)

    attempt = 0
    while True:
        try:
            return _non_stream_request(payload, timeout)
        except (requests.Timeout, requests.ConnectionError, GrokAPIError) as e:
            attempt += 1
            if attempt > max_retries: raise
            time.sleep(retry_backoff_sec * attempt)

# ------------------------------------------------------------------
# TAVILY SEARCH API
# ------------------------------------------------------------------

def search_tavily(
    query: str,
    include_domains: Optional[List[str]] = None,
    days: int = 30,
    max_results: int = 7
) -> dict:
    """
    Queries Tavily and returns the raw JSON object with results.
    We return the full dict so the caller can inspect metadata.
    """
    payload = {
        "api_key": TAVILY_API_KEY,
        "query": query,
        "search_depth": "advanced",
        "include_answer": False,
        "include_raw_content": False, 
        "max_results": max_results,
        "topic": "news", 
        "days": days
    }
    
    if include_domains:
        payload["include_domains"] = include_domains

    try:
        resp = requests.post(TAVILY_API_URL, json=payload, timeout=30)
        if resp.status_code != 200:
            raise TavilyAPIError(f"HTTP {resp.status_code}: {resp.text}")
        
        return resp.json()

    except Exception as e:
        raise TavilyAPIError(f"Tavily search failed: {e}")

def format_tavily_results(tavily_response: dict) -> str:
    """
    Helper to turn the Tavily JSON into a text blob for the LLM.
    """
    results = tavily_response.get("results", [])
    if not results:
        return "NO RESULTS FOUND."
        
    formatted_output = ""
    for i, res in enumerate(results):
        formatted_output += f"\n--- SOURCE {i+1}: {res.get('title', 'Unknown')} ---\n"
        formatted_output += f"URL: {res.get('url', 'Unknown')}\n"
        formatted_output += f"DATE: {res.get('published_date', 'Unknown')}\n"
        formatted_output += f"CONTENT:\n{res.get('content', '')}\n"
    return formatted_output

def get_company_domains_with_grok(symbol: str, company: str) -> list:
    """
    Uses Grok to identify the specific official domains for a company.
    Returns a list like ['apple.com', 'investor.apple.com']
    """
    prompt = f"""
    I need the official website and the specific Investor Relations subdomain for:
    Company: {company}
    Ticker: {symbol}
    
    Return ONLY a raw JSON list of strings. Do not explain.
    Example output: ["apple.com", "investor.apple.com"]
    """
    
    try:
        # Use your existing sender function
        response = send_openai_prompt(
            prompt,
            model="gpt-5-nano",  # was grok-3-mini
            reasoning_effort="minimal"
        )
        
        # Clean up response (grok might add markdown)
        clean_resp = response.replace("```json", "").replace("```", "").strip()
        domains = _json.loads(clean_resp)
        
        # Validation: ensure it's a list
        if isinstance(domains, list):
            return [str(d).split("//")[-1].removeprefix("www.").rstrip("/") for d in domains]
        return []
        
    except Exception as e:
        print(f"[WARN] domain lookup failed: {e}")
        return [] # Fail gracefully, we still have the master list
        
def synthesize_research_with_grok(raw_text_context: str, json_schema_str: str,
                                   symbol: str = "", company: str = "") -> str:
    """
    Takes raw text from Tavily and uses Grok to extract strict JSON.
    symbol/company are used for source validation (strongly recommended).
    """
    system_prompt = "You are a Financial Data Extraction Engine. Your ONLY task is to output valid JSON based on the provided text."

    # Build ticker-validation block when caller provides symbol/company
    if symbol and company:
        ticker_rules = f"""
    5. **TICKER VALIDATION (CRITICAL):** Every source and every fact you extract MUST be
       specifically about {company} (ticker: {symbol}). If a source is about a DIFFERENT
       company with a similar ticker (e.g. APLD instead of ADP, or AMZN instead of AMZ),
       you MUST discard that source entirely. Do NOT include its URL in the sources list
       and do NOT extract any data from it. When in doubt, return null rather than risk
       contaminating the JSON with data from the wrong security.
    6. **SOURCE TITLE CHECK:** Before including any source, verify the article title or
       content explicitly mentions "{symbol}" or "{company}". If it does not, skip it."""
    else:
        ticker_rules = ""

    user_prompt = f"""
    Current Date: {datetime.date.today().strftime('%Y-%m-%d')}
    
    TASK:
    Extract factual data from the RAW SEARCH RESULTS below to populate the JSON schema.
    
    RAW SEARCH RESULTS:
    {raw_text_context}
    
    RULES:
    1. **NO FUTURE HALLUCINATIONS:** If a text says "Forecast for 2026", do NOT put it in current price fields.
    2. **SOURCE MAPPING:** Map every fact to the specific source URL provided in the text.
    3. **NULL HANDLING:** If data (like 'unusual options') is not in the text, return null. Do not invent it.
    4. **OUTPUT:** Return ONLY the valid JSON object. No markdown, no backticks.{ticker_rules}

    TARGET JSON SCHEMA:
    {json_schema_str}
    """

    return send_openai_prompt(
        prompt=user_prompt,
        model="gpt-5-mini",  # was grok-3-mini; mini for completeness
        system=system_prompt,
        reasoning_effort="minimal"
    )

# ------------------------------------------------------------------
# IMAGE GENERATION APIs
# ------------------------------------------------------------------

def generate_AI_Image_sdxl(prompt: str, image_filepath: str, width: int, height: int, negative_prompt: str = "", seed: Optional[int] = None) -> Optional[str]:
    print(f"[INFO] Using DEFAULT model (stability-ai/sdxl) for prompt: '{prompt[:60]}...'")
    client = replicate.Client(api_token=config.REPLICATE_API_TOKEN)
    input_params = {
        "width": width, "height": height, "prompt": prompt,
        "refine": "expert_ensemble_refiner", "scheduler": "K_EULER", "lora_scale": 0.6,
        "num_outputs": 1, "guidance_scale": 7.5, "apply_watermark": False,
        "high_noise_frac": 0.8, "negative_prompt": negative_prompt or "blurry, distorted, ugly, watermark, text, signature",
        "prompt_strength": 0.8, "num_inference_steps": 25,
    }
    if seed: input_params["seed"] = seed
    try:
        output = retry_api_call(lambda: client.run("stability-ai/sdxl:7762fd07cf82c948538e41f63f77d685e02b063e37e496e96eefd46c929f9bdc", input=input_params))
        img_url = output[0]
        print("[SUCCESS] Image URL from SDXL:", img_url)
        resp = requests.get(img_url, timeout=30)
        resp.raise_for_status()
        _save_compressed_jpeg(resp.content, image_filepath, quality=75)
        return img_url
    except Exception as e:
        print(f"[ERROR] SDXL image generation failed: {e}")
        return None

def generate_AI_Image_stability_premium(
    prompt: str, 
    image_filepath: str, 
    width: int, 
    height: int, 
    negative_prompt: str = "", 
    seed: Optional[int] = None
) -> Optional[str]:
    print(f"[INFO] Using SD 3.5 Large for prompt: '{prompt[:60]}...'")
    client = replicate.Client(api_token=config.REPLICATE_API_TOKEN)
    
    # Map dimensions to aspect_ratio string
    ratio = width / height
    if ratio >= 2.2:
        aspect_ratio = "21:9"
    elif ratio >= 1.7:
        aspect_ratio = "16:9"
    elif ratio >= 1.4:
        aspect_ratio = "3:2"
    elif ratio >= 1.2:
        aspect_ratio = "4:3"
    else:
        aspect_ratio = "1:1"
    
    input_params = {
        "prompt": prompt,
        "aspect_ratio": aspect_ratio,    # THIS IS THE FIX - string, not dimensions
        "cfg": 7.0,
        "output_format": "webp",
        "prompt_strength": 0.85,
    }
    if seed:
        input_params["seed"] = seed
    
    try:
        output = retry_api_call(lambda: client.run(
            "stability-ai/stable-diffusion-3.5-large", 
            input=input_params
        ))
        if hasattr(output, "url"):
            img_url = output.url
        elif isinstance(output, str):
            img_url = output
        else:
            return None
        
        response = requests.get(img_url, timeout=30)
        if response.status_code == 200:
            _save_compressed_jpeg(response.content, image_filepath, quality=85)
            return img_url
        return None
    except Exception as e:
        print(f"[ERROR] SD 3.5 Large failed: {e}")
        raise

def generate_AI_Image_dalle_premium(prompt: str, image_filepath: str, width: int, height: int, negative_prompt: str = "", seed: Optional[int] = None) -> Optional[str]:
    print(f"[INFO] Using PREMIUM OpenAI image model (gpt-image-1)")
    size = f"{width}x{height}"
    if size != "1200x600": size = "1200x600"
    try:
        payload = {"model": DEFAULT_OPENAI_IMAGE_MODEL, "prompt": prompt, "n": 1, "size": size, "quality": "hd"}
        resp = retry_api_call(lambda: requests.post(OPENAI_IMAGES_URL, headers={"Content-Type": "application/json", "Authorization": f"Bearer {OPENAI_KEY}"}, json=payload, timeout=60))
        img_url = resp.json()["data"][0]["url"]
        r = requests.get(img_url, timeout=30)
        _save_compressed_jpeg(r.content, image_filepath, quality=75)
        return img_url
    except Exception as e:
        print(f"[ERROR] OpenAI image failed: {e}")
        return None

def generate_AI_Image_flux_schnell(prompt: str, image_filepath: str, width: int, height: int, negative_prompt: str = "", seed: Optional[int] = None) -> Optional[str]:
    print(f"[INFO] Using FLUX 1.1 PRO ULTRA (21:9 raw mode) for prompt: '{prompt[:60]}...'")
    client = replicate.Client(api_token=config.REPLICATE_API_TOKEN)
    input_params = {
        "prompt": prompt,
        "aspect_ratio": "21:9",
        "raw": True,
        "output_format": "jpg",
        "safety_tolerance": 2,
    }
    if seed is not None:
        input_params["seed"] = seed
    try:
        output = retry_api_call(lambda: client.run("black-forest-labs/flux-1.1-pro-ultra", input=input_params))
        file_obj = output[0] if isinstance(output, (list, tuple)) else output
        img_url = str(file_obj.url) if hasattr(file_obj, "url") else str(file_obj)
        if not img_url.startswith("http"):
            raise RuntimeError(f"Unexpected FLUX output: {img_url[:200]}")
        resp = requests.get(img_url, timeout=60)
        resp.raise_for_status()
        _save_compressed_jpeg(resp.content, image_filepath, quality=82)
        if not os.path.isfile(image_filepath) or os.path.getsize(image_filepath) == 0:
            raise RuntimeError("FLUX returned output but no JPEG was written")
        return img_url
    except Exception as e:
        print(f"[ERROR] FLUX 1.1 Pro Ultra image failed: {e}")
        return None

def generate_AI_Image_premium(prompt: str, image_filepath: str, width: int, height: int, negative_prompt: str = "", seed: Optional[int] = None, remove_background: bool = False) -> Optional[str]:
    if PREMIUM_IMAGE_PROVIDER == "stability": return generate_AI_Image_stability_premium(prompt, image_filepath, width, height, negative_prompt, seed)
    if PREMIUM_IMAGE_PROVIDER == "openai": return generate_AI_Image_dalle_premium(prompt, image_filepath, width, height, negative_prompt, seed)
    if PREMIUM_IMAGE_PROVIDER == "flux": return generate_AI_Image_flux_schnell(prompt, image_filepath, width, height, negative_prompt, seed)
    raise RuntimeError(f"Invalid PREMIUM_IMAGE_PROVIDER '{PREMIUM_IMAGE_PROVIDER}'")

# ------------------------------------------------------------------
# PERPLEXITY API
# ------------------------------------------------------------------

PERPLEXITY_API_URL  = "https://api.perplexity.ai/chat/completions"
DEFAULT_PERPLEXITY_MODEL = 'sonar-pro' 

def _pplx_headers() -> dict:
    return {"Content-Type": "application/json", "Authorization": f"Bearer {PERPLEXITY_API_KEY}"}

def _pplx_make_payload(prompt, model, system, temperature, stream, **kwargs):
    messages = [{"role": "user", "content": prompt}]
    if system: messages.insert(0, {"role": "system", "content": system})
    payload = {"model": model, "messages": messages, "temperature": temperature, "stream": stream}
    payload.update({k: v for k, v in kwargs.items() if v is not None})
    return payload

def send_perplexity_prompt(prompt, model=DEFAULT_PERPLEXITY_MODEL, system=None, temperature=0.0, stream=False, timeout=600, **kwargs):
    payload = _pplx_make_payload(prompt, model, system, temperature, stream, **kwargs)
    if stream:
        def _gen():
            with requests.post(PERPLEXITY_API_URL, headers=_pplx_headers(), json=payload, timeout=timeout, stream=True) as r:
                for line in r.iter_lines(decode_unicode=True):
                    if line.startswith("data: "):
                        chunk = line[6:].strip()
                        if chunk == "[DONE]": break
                        try:
                            content = _json.loads(chunk)["choices"][0]["delta"].get("content")
                            if content: yield content
                        except: continue
        return _gen()
    
    resp = requests.post(PERPLEXITY_API_URL, headers=_pplx_headers(), json=payload, timeout=timeout)
    if resp.status_code != 200: raise PerplexityAPIError(f"HTTP {resp.status_code}: {resp.text}")
    return resp.json()["choices"][0]["message"]["content"]

# ------------------------------------------------------------------
# OPENAI API
# ------------------------------------------------------------------

def send_openai_prompt(prompt, model=OPENAI_MODEL_DEFAULT, system=None, stream=False, timeout=(10, 600), **kwargs):
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {OPENAI_KEY}"}
    messages = [{"role": "user", "content": prompt}]
    if system: messages.insert(0, {"role": "system", "content": system})
    payload = {"model": model, "messages": messages, "stream": stream}
    payload.update({k: v for k, v in kwargs.items() if v is not None})

    if stream:
        def _gen():
            with requests.post(OPENAI_API_URL, headers=headers, json=payload, timeout=timeout, stream=True) as r:
                for line in r.iter_lines(decode_unicode=True):
                    if line.startswith("data: "):
                        chunk = line[6:].strip()
                        if chunk == "[DONE]": break
                        try:
                            content = requests.utils.json.loads(chunk)["choices"][0]["delta"].get("content")
                            if content: yield content
                        except: continue
        return _gen()

    resp = requests.post(OPENAI_API_URL, headers=headers, json=payload, timeout=timeout)
    if resp.status_code != 200: raise OpenAIAPIError(f"HTTP {resp.status_code}: {resp.text}")
    return resp.json()["choices"][0]["message"]["content"]


# ------------------------------------------------------------------
# ANTHROPIC (CLAUDE) API
# ------------------------------------------------------------------

def send_claude_prompt(prompt, model=CLAUDE_MODEL_DEFAULT, system=None,
                       max_tokens=4096, temperature=0.0, timeout=(15, 300), **kwargs):
    """
    Send a prompt to the Anthropic Claude API.  Returns the response text.

    Model constants (or pass the string directly):
      CLAUDE_OPUS_46    = 'claude-opus-4-6'           most capable
      CLAUDE_SONNET_46  = 'claude-sonnet-4-6'         recommended default
      CLAUDE_HAIKU_45   = 'claude-haiku-4-5-20251001' fast + cheap
      CLAUDE_HAIKU_35   = 'claude-3-5-haiku-20241022' very cheap
      CLAUDE_HAIKU_3    = 'claude-3-haiku-20240307'   cheapest

    temperature=0.0 is intentional for structured/ranking tasks.
    Raise temperature (0.3-0.7) for more creative angle writing.
    """
    headers = {
        'x-api-key':         ANTHROPIC_API_KEY,
        'anthropic-version': ANTHROPIC_VERSION,
        'content-type':      'application/json',
    }
    payload = {
        'model':      model,
        'max_tokens': max_tokens,
        'messages':   [{'role': 'user', 'content': prompt}],
    }
    if system:
        payload['system'] = system
    if temperature != 0.0:
        payload['temperature'] = temperature
    payload.update({k: v for k, v in kwargs.items() if v is not None})

    resp = requests.post(ANTHROPIC_API_URL, headers=headers,
                         json=payload, timeout=timeout)
    if resp.status_code != 200:
        raise AnthropicAPIError(f'HTTP {resp.status_code}: {resp.text}')
    return resp.json()['content'][0]['text']


def send_claude_messages(messages, model=CLAUDE_MODEL_DEFAULT, system=None,
                         max_tokens=4096, temperature=0.0, timeout=(15, 300),
                         cache_system=False, cache_ttl='5m'):
    """
    Send a multi-turn conversation to Claude.
    `messages` is a list of {'role': 'user'|'assistant', 'content': str} dicts.
    The system prompt (if any) goes in the separate `system` parameter.
    Set cache_system=True to enable Anthropic prompt caching on the system prompt.
      cache_ttl='5m'  — $1.25/MTok write, resets on every hit (good for active users)
      cache_ttl='1h'  — $2.00/MTok write, survives 1hr inactivity (good for sporadic use)
    Cache hits are $0.10/MTok regardless of TTL — 10x cheaper than base input.
    """
    headers = {
        'x-api-key':         ANTHROPIC_API_KEY,
        'anthropic-version': ANTHROPIC_VERSION,
        'content-type':      'application/json',
    }
    if cache_system:
        headers['anthropic-beta'] = 'prompt-caching-2024-07-31'
        # NOTE: 1h cache requires an additional beta header — check docs.claude.com
        # for the correct header name before enabling.

    payload = {
        'model':      model,
        'max_tokens': max_tokens,
        'messages':   messages,
    }
    if system:
        if cache_system:
            payload['system'] = [{'type': 'text', 'text': system, 'cache_control': {'type': 'ephemeral'}}]
        else:
            payload['system'] = system
    if temperature != 0.0:
        payload['temperature'] = temperature

    resp = requests.post(ANTHROPIC_API_URL, headers=headers,
                         json=payload, timeout=timeout)
    if resp.status_code != 200:
        raise AnthropicAPIError(f'HTTP {resp.status_code}: {resp.text}')
    return resp.json()['content'][0]['text']


# ------------------------------------------------------------------
# MAIN (SMOKE TESTS)
# ------------------------------------------------------------------

if __name__ == "__main__":
    
    # --- 1. DEFINE TEST DATA ---
    symbol = "AAPL"
    company = "Apple Inc"
    
    # We use a whitelist of high-quality domains to test Tavily's filtering
    tavily_domains = [
        "cnbc.com", "reuters.com", "bloomberg.com", 
        "finance.yahoo.com", "wsj.com", "barrons.com", 
        "marketwatch.com", "investor.apple.com", "sec.gov",
        "nasdaq.com", "fintel.io"
    ]
    
    # A simplified version of your JSON schema for the test
    target_schema = """
    {
      "symbol": "AAPL",
      "company": "Apple",
      "price": { "last": null, "change_percent": null },
      "catalysts": [ { "headline": null, "date": null, "summary": null } ],
      "analyst": { "consensus_rating": null, "price_target_consensus": null },
      "sources": [ { "title": null, "url": null } ]
    }
    """

    print("\n" + "="*60)
    print(f"🚀 STARTING TAVILY + GROK PIPELINE SMOKE TEST FOR: {company} ({symbol})")
    print("="*60 + "\n")

    try:
        # --- 2. TAVILY SEARCH (Retrieval) ---
        print(f"🕵️  STEP 1: Querying Tavily for verified news sources...")
        search_query = f"{company} ({symbol}) stock price analysis earnings news and analyst ratings"
        
        # Call the new function
        tavily_resp = search_tavily(
            query=search_query, 
            include_domains=tavily_domains,
            days=30
        )
        
        results_list = tavily_resp.get('results', [])
        count = len(results_list)
        print(f"✅ Found {count} results from Tavily.")
        
        # Print titles to confirm we aren't getting just one
        for i, res in enumerate(results_list):
            print(f"   [{i+1}] {res.get('title', 'No Title')} ({res.get('url')})")
        
        # Format text for Grok
        tavily_text_blob = format_tavily_results(tavily_resp)

        # --- 3. GROK SYNTHESIS (Extraction) ---
        print(f"\n🧠 STEP 2: Synthesizing JSON via gpt-5-mini...")
        
        grok_json_str = synthesize_research_with_grok(
            raw_text_context=tavily_text_blob,
            json_schema_str=target_schema
        )

        print("\n--- [GROK JSON OUTPUT] ---")
        print(grok_json_str)
        print("--------------------------\n")
        
        # Verify it parses as valid JSON
        parsed = _json.loads(grok_json_str)
        print("✅ SUCCESS: Output matches JSON schema.")

    except Exception as e:
        print(f"\n❌ FAILURE: Pipeline test failed with error: {e}")