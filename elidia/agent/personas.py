import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class DomainPersona:
    name: str
    slug: str
    system_prompt: str
    greeting: str = ""
    icon: str = ""
    preferred_model: str = ""
    preferred_tools: list[str] = field(default_factory=list)
    temperature: float = 0.7


BUILT_IN_PERSONAS: dict[str, DomainPersona] = {
    "coder": DomainPersona(
        name="Coder",
        slug="coder",
        icon=">>",
        system_prompt=(
            "You are an expert software engineer. You write clean, correct, production-quality code. "
            "You read existing code before modifying it. You prefer editing over rewriting. "
            "You use tools to read files, run tests, and verify your work."
        ),
        greeting="Ready to code. What are we building?",
        preferred_model="claude-sonnet-5",
        preferred_tools=["file_read", "file_write", "file_edit", "file_grep", "command_exec", "git_status", "git_diff"],
        temperature=0.3,
    ),
    "researcher": DomainPersona(
        name="Researcher",
        slug="researcher",
        icon="??",
        system_prompt=(
            "You are a thorough research assistant. You search the web, read documentation, "
            "cross-reference sources, and synthesize findings into clear, structured answers. "
            "Cite your sources."
        ),
        greeting="What would you like me to research?",
        preferred_model="deepseek-chat",
        preferred_tools=["web_search", "http_fetch", "file_read"],
        temperature=0.5,
    ),
    "analyst": DomainPersona(
        name="Data Analyst",
        slug="analyst",
        icon="##",
        system_prompt=(
            "You are a data analyst. You examine data, find patterns, create visualizations, "
            "and provide actionable insights. You use code to process and analyze data."
        ),
        greeting="Share your data or describe what you'd like to analyze.",
        preferred_model="deepseek-chat",
        preferred_tools=["file_read", "command_exec", "file_write"],
        temperature=0.4,
    ),
    "writer": DomainPersona(
        name="Writer",
        slug="writer",
        icon="~~",
        system_prompt=(
            "You are a skilled writer and editor. You help with drafting, editing, "
            "proofreading, and creative writing. You adapt your tone and style to the task."
        ),
        greeting="What would you like to write?",
        preferred_model="gpt-5",
        preferred_tools=["web_search", "file_read", "file_write"],
        temperature=0.8,
    ),
    "devops": DomainPersona(
        name="DevOps Engineer",
        slug="devops",
        icon="!!",
        system_prompt=(
            "You are a DevOps engineer. You help with deployment, CI/CD, infrastructure, "
            "Docker, Kubernetes, monitoring, and system administration. "
            "You always check system state before making changes."
        ),
        greeting="What infrastructure or deployment task do you need help with?",
        preferred_model="claude-sonnet-5",
        preferred_tools=["command_exec", "file_read", "file_write", "file_edit", "git_status"],
        temperature=0.3,
    ),
}


class PersonaEngine:
    """Manages domain personas for the agent."""

    def __init__(self) -> None:
        logger.debug("Entered into PersonaEngine.__init__")
        self._personas: dict[str, DomainPersona] = dict(BUILT_IN_PERSONAS)
        self._active: DomainPersona | None = None

    def list_personas(self) -> list[DomainPersona]:
        logger.debug("Entered into list_personas")
        return list(self._personas.values())

    def get(self, slug: str) -> DomainPersona | None:
        logger.debug(f"Entered into get: slug={slug}")
        return self._personas.get(slug)

    def activate(self, slug: str) -> DomainPersona | None:
        logger.debug(f"Entered into activate: slug={slug}")
        persona = self._personas.get(slug)
        if persona:
            self._active = persona
        return persona

    def deactivate(self) -> None:
        logger.debug("Entered into deactivate")
        self._active = None

    @property
    def active(self) -> DomainPersona | None:
        return self._active

    def register(self, persona: DomainPersona) -> None:
        logger.debug(f"Entered into register: slug={persona.slug}")
        self._personas[persona.slug] = persona

    def get_system_prompt_overlay(self) -> str | None:
        logger.debug("Entered into get_system_prompt_overlay")
        if not self._active:
            return None
        return self._active.system_prompt
