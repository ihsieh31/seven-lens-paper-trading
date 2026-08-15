"""Configuration failures that must prevent process startup."""


class ConfigurationError(ValueError):
    """Raised when configuration cannot prove that startup is safe."""
