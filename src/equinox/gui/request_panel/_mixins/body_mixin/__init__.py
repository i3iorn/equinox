"""Compatibility aggregator for focused body-related request-panel mixins."""

from .body_search_mixin import BodySearchMixin
from .body_state_mixin import BodyStateMixin
from .captures_mixin import CapturesMixin
from .multipart_data_mixin import MultipartDataMixin
from .request_loading_mixin import RequestLoadingMixin


class RequestBodyMixin(
    CapturesMixin,
    BodyStateMixin,
    BodySearchMixin,
    MultipartDataMixin,
    RequestLoadingMixin,
):
    """Compose focused body, search, capture, multipart, and loading helpers."""
