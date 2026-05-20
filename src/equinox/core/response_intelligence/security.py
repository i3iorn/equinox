"""Security & Compliance analyzers.

DEPRECATED: This module is maintained for backward compatibility only.
New code should import directly from the analyzers package:

    from equinox.core.response_intelligence.analyzers import (
        MissingSecurityHeadersAnalyzer,
        CookieFlagsAnalyzer,
        PIILeakDetectionAnalyzer,
        CORSMisconfigAnalyzer,
        JWTDecodeAnalyzer,
        SensitiveDataCachingAnalyzer,
    )
"""

# Re-export all analyzers for backward compatibility
from equinox.core.response_intelligence.analyzers import (
    CookieFlagsAnalyzer,
    CORSMisconfigAnalyzer,
    JWTDecodeAnalyzer,
    MissingSecurityHeadersAnalyzer,
    PIILeakDetectionAnalyzer,
    SensitiveDataCachingAnalyzer,
)

__all__ = [
    "MissingSecurityHeadersAnalyzer",
    "CookieFlagsAnalyzer",
    "PIILeakDetectionAnalyzer",
    "CORSMisconfigAnalyzer",
    "JWTDecodeAnalyzer",
    "SensitiveDataCachingAnalyzer",
]
