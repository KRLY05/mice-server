from enum import Enum
from .config import WORKFLOWS, DEFAULT_WORKFLOW


class UserState(Enum):
    MENU = "menu"
    STARTING_LLM = "starting_llm"
    CHATTING = "chatting"
    STARTING_DIFFUSION = "starting_diffusion"
    SELECTING_WORKFLOW = "selecting_workflow"
    WAITING_PHOTO = "waiting_photo"
    WAITING_PROMPT = "waiting_prompt"
    GENERATING = "generating"


# Per-user state tracking
user_states: dict[int, UserState] = {}
user_photos: dict[int, str] = {}
user_workflows: dict[int, str] = {}
polling_tasks: dict = {}
generation_tasks: dict = {}


def get_state(chat_id: int) -> UserState:
    """Get the current state for a user, defaulting to MENU."""
    return user_states.get(chat_id, UserState.MENU)


def set_state(chat_id: int, state: UserState):
    """Set the state for a user."""
    user_states[chat_id] = state


def get_workflow(chat_id: int) -> str:
    """Get the active workflow name for a user, defaulting to DEFAULT_WORKFLOW."""
    return user_workflows.get(chat_id, DEFAULT_WORKFLOW)


def get_workflow_path(chat_id: int) -> str:
    """Get the file path for the user's active workflow."""
    wf_name = get_workflow(chat_id)
    return WORKFLOWS.get(wf_name, WORKFLOWS[DEFAULT_WORKFLOW])


def set_workflow(chat_id: int, workflow_name: str):
    """Set the active workflow for a user."""
    user_workflows[chat_id] = workflow_name
