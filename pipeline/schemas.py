"""JSON schemas the agents must answer in. The CLI enforces them."""

DISTRICTS = ["Голосіївський", "Дарницький", "Деснянський", "Дніпровський",
             "Оболонський", "Печерський", "Подільський", "Святошинський",
             "Солом'янський", "Шевченківський"]

DISTRICTS_EN = {
    "Голосіївський": "Holosiivskyi", "Дарницький": "Darnytskyi",
    "Деснянський": "Desnianskyi", "Дніпровський": "Dniprovskyi",
    "Оболонський": "Obolonskyi", "Печерський": "Pecherskyi",
    "Подільський": "Podilskyi", "Святошинський": "Sviatoshynskyi",
    "Солом'янський": "Solomianskyi", "Шевченківський": "Shevchenkivskyi",
}

TARGETS = ["residential_highrise", "residential_house", "energy", "industrial",
           "warehouse", "commercial", "office", "education", "medical",
           "transport", "infrastructure", "cultural_religious", "vehicle",
           "open_area", "other"]
WEAPONS = ["drone", "jet_drone", "ballistic", "cruise_missile", "missile", "unknown"]
EFFECTS = ["direct_hit", "debris", "fire", "blast_damage", "destroyed", "crater"]

_nullable_int = {"type": ["integer", "null"], "minimum": 0}

READER = {
    "type": "object",
    "properties": {
        "impacts": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "post": {"type": "string"},
                "quote": {"type": "string"},
                "district": {"type": ["string", "null"], "enum": DISTRICTS + [None]},
                "address": {"type": ["string", "null"]},
                "place_en": {"type": "string"},
                "target": {"type": "string", "enum": TARGETS},
                "weapon": {"type": "string", "enum": WEAPONS},
                "effects": {"type": "array", "items": {"type": "string", "enum": EFFECTS}},
                "time": {"type": ["string", "null"]},
                "killed": _nullable_int,
                "injured": _nullable_int,
            },
            "required": ["post", "quote", "district", "address", "place_en",
                         "target", "weapon", "effects", "time", "killed", "injured"],
        }},
        "city_totals": {"type": "array", "items": {
            "type": "object",
            "properties": {"post": {"type": "string"}, "killed": _nullable_int,
                           "injured": _nullable_int, "quote": {"type": "string"}},
            "required": ["post", "killed", "injured", "quote"],
        }},
    },
    "required": ["impacts", "city_totals"],
}

MATCHER = {
    "type": "object",
    "properties": {
        "sites": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "claims": {"type": "array", "items": {"type": "string"}},
                "title_en": {"type": "string"},
                "summary_en": {"type": "string"},
                "district": {"type": ["string", "null"], "enum": DISTRICTS + [None]},
                "address": {"type": ["string", "null"]},
                "target": {"type": "string", "enum": TARGETS},
                "weapon": {"type": "string", "enum": WEAPONS},
                "time": {"type": ["string", "null"]},
            },
            "required": ["claims", "title_en", "summary_en", "district", "address",
                         "target", "weapon", "time"],
        }},
        "night_summary_en": {"type": "string"},
    },
    "required": ["sites", "night_summary_en"],
}

AUDITOR = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["approve", "revise"]},
        "issues": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": [
                    "wrong_merge", "missed_merge", "unsupported_summary",
                    "wrong_field", "not_kyiv_city", "not_this_attack", "lost_claim"]},
                "sites": {"type": "array", "items": {"type": "integer"}},
                "claims": {"type": "array", "items": {"type": "string"}},
                "explanation": {"type": "string"},
            },
            "required": ["kind", "sites", "claims", "explanation"],
        }},
    },
    "required": ["verdict", "issues"],
}
