"""Agent package.

Third-party warnings are silenced here, at the one point every entry script
imports through. They come from inside langchain and mcp on Python 3.14, say
nothing about this project, and are long enough to bury the agent's own output.
"""

import warnings

warnings.filterwarnings("ignore", message=".*Core Pydantic V1 functionality.*")
warnings.filterwarnings("ignore", message=".*IncompleteFieldDefinition.*")
warnings.filterwarnings("ignore", category=UserWarning, module="pydantic_settings.*")
