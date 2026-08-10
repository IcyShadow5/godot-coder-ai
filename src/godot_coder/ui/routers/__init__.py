"""Route-group modules for the Studio FastAPI app.

Each group builds an APIRouter against the shared app.state (jobs,
generation, remote_access) and the resolved project root, so the route bodies
stay the same as they were inside create_app.
"""

from .chat import build_chat_router
from .corpus import build_corpus_router
from .remote import build_remote_router
from .system import build_system_router
from .training import build_training_router

__all__ = [
    "build_chat_router",
    "build_corpus_router",
    "build_remote_router",
    "build_system_router",
    "build_training_router",
]
