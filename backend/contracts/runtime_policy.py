"""Defaults exported into the generated runtime reference."""
from .transport import TransportPolicy

AGENT_TURN_SECONDS = 180.0
GENERATION_TURN_SECONDS = 300.0
MODEL_REPAIR_SECONDS = 120.0
MIN_MODEL_REPAIR_SECONDS = 30.0
REVIEW_UNIT_SECONDS = 60.0
GENERATION_VERIFY_REQUESTS = 2
GENERATION_VERIFY_OUTPUT_TOKENS = 2048
RENDERER_READ_SECONDS = 15
RENDERER_MUTATION_SECONDS = 30
MAX_AGENT_MESSAGE_CHARACTERS = 65_536
# JSON can encode one astral character as two six-byte Unicode escapes.
MAX_AGENT_MESSAGE_REQUEST_BYTES = MAX_AGENT_MESSAGE_CHARACTERS * 12 + 16_384


def runtime_policy_document():
    from dataclasses import asdict
    from backend.agent_runtime.jobs import EXECUTION_PROFILES, OUTAGE_WINDOW_SECONDS, MAX_RECOVERY_CYCLES
    return {"schema_version": 1, "transport": asdict(TransportPolicy()),
        "turn_seconds": AGENT_TURN_SECONDS, "review_unit_seconds": REVIEW_UNIT_SECONDS,
        "generation_turn_seconds": GENERATION_TURN_SECONDS, "model_repair_seconds": MODEL_REPAIR_SECONDS,
        "min_model_repair_seconds": MIN_MODEL_REPAIR_SECONDS,
        "generation_reserve": {"requests": GENERATION_VERIFY_REQUESTS, "output_tokens": GENERATION_VERIFY_OUTPUT_TOKENS},
        "renderer": {"read_seconds": RENDERER_READ_SECONDS, "mutation_seconds": RENDERER_MUTATION_SECONDS,
                     "max_message_characters": MAX_AGENT_MESSAGE_CHARACTERS},
        "profiles": EXECUTION_PROFILES, "outage_window_seconds": OUTAGE_WINDOW_SECONDS,
        "auto_recovery_cycles": MAX_RECOVERY_CYCLES,
        "deadline_codes": {"turn": "AGENT_TURN_LIMIT_EXCEEDED", "job": "AGENT_RUNTIME_LIMIT_EXCEEDED",
                           "provider": "PROVIDER_TIMEOUT", "verification_reserve": "AGENT_VERIFICATION_RESERVE_REACHED"}}
