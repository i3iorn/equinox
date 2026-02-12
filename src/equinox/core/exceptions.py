"""Exceptions for Equinox"""


class EquinoxError(Exception):
    """Base exception for Equinox"""
    pass


class RequestError(EquinoxError):
    """Error during HTTP request"""
    pass


class AuthError(EquinoxError):
    """Authentication error"""
    pass


class StorageError(EquinoxError):
    """Storage/database error"""
    pass


class PluginError(EquinoxError):
    """Plugin loading or execution error"""
    pass


class ValidationError(EquinoxError):
    """Validation error"""
    pass
