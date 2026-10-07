"""
Kokoro female voice catalog.
All voices ship with kokoro-82m-int8 — no extra downloads needed.
"""

FEMALE_VOICES = [
    # ── American English ─────────────────────────────────────────────────────
    {
        "id":          "af_heart",
        "name":        "Heart",
        "accent":      "American",
        "style":       "Warm, conversational",
        "best_for":    "General avatar, customer-facing",
        "sample_text": "Hi, I'm Beryl. How can I help you today?",
    },
    {
        "id":          "af_bella",
        "name":        "Bella",
        "accent":      "American",
        "style":       "Bright, energetic",
        "best_for":    "Upbeat interactions, demos",
        "sample_text": "Great to meet you — let's get started!",
    },
    {
        "id":          "af_sarah",
        "name":        "Sarah",
        "accent":      "American",
        "style":       "Clear, professional",
        "best_for":    "Business, formal presentations",
        "sample_text": "I've reviewed your request and I'm ready to assist.",
    },
    {
        "id":          "af_nicole",
        "name":        "Nicole",
        "accent":      "American",
        "style":       "Calm, measured",
        "best_for":    "Long-form narration, onboarding",
        "sample_text": "Welcome. Take your time — I'm here whenever you need me.",
    },
    {
        "id":          "af_sky",
        "name":        "Sky",
        "accent":      "American",
        "style":       "Youthful, approachable",
        "best_for":    "Casual chat, Gen-Z tone",
        "sample_text": "Okay so here's the deal — it's actually pretty simple.",
    },
    # ── British English ───────────────────────────────────────────────────────
    {
        "id":          "bf_emma",
        "name":        "Emma",
        "accent":      "British",
        "style":       "Polished, articulate",
        "best_for":    "Premium brand voice, executive assistant",
        "sample_text": "Lovely to speak with you. Shall we begin?",
    },
    {
        "id":          "bf_isabella",
        "name":        "Isabella",
        "accent":      "British",
        "style":       "Elegant, confident",
        "best_for":    "Luxury, editorial, storytelling",
        "sample_text": "The world is full of extraordinary stories. Let me tell you one.",
    },
]

# voice_id → metadata dict for O(1) lookup
BY_ID = {v["id"]: v for v in FEMALE_VOICES}

DEFAULT_VOICE = "af_heart"
