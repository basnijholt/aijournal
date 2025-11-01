"""Shared configuration constants.

This module contains constants used across multiple modules:
- Infrastructure/environment configuration (Ollama, embeddings)
- Shared formats and protocols (timeouts, file suffixes)
- Cross-cutting paths and settings

Module-specific constants should remain in their respective modules.
"""

# ============================================================================
# LLM & Model Configuration
# ============================================================================
DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"
DEFAULT_MODEL_NAME = "gpt-oss:20b"
DEFAULT_LLM_RETRIES = 4

# ============================================================================
# Embedding Configuration
# ============================================================================
DEFAULT_EMBEDDING_MODEL = "embeddinggemma:300m"
DEFAULT_EMBED_DIM = 384
EMBED_TIMEOUT = 60.0

# ============================================================================
# Timeouts
# ============================================================================
DEFAULT_TIMEOUT_SECONDS = 120.0

# ============================================================================
# File Formats
# ============================================================================
MARKDOWN_SUFFIXES = {".md", ".markdown"}

# ============================================================================
# Shared Paths
# ============================================================================
PENDING_UPDATES_SUBDIR = "derived/pending/profile_updates"
