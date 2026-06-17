from enum import Enum


class UserState(Enum):
    MENU = "menu"
    STARTING_LLM = "starting_llm"
    CHATTING = "chatting"
    STARTING_DIFFUSION = "starting_diffusion"
    WAITING_PHOTO = "waiting_photo"
    WAITING_PROMPT = "waiting_prompt"
    GENERATING = "generating"


# Per-user state tracking
user_states: dict[int, UserState] = {}
user_photos: dict[int, str] = {}
polling_tasks: dict = {}
generation_tasks: dict = {}


def get_state(chat_id: int) -> UserState:
    """Get the current state for a user, defaulting to MENU."""
    return user_states.get(chat_id, UserState.MENU)


def set_state(chat_id: int, state: UserState):
    """Set the state for a user."""
    user_states[chat_id] = state
