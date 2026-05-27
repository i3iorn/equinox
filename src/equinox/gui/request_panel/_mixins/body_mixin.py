"""Compatibility aggregator for focused body-related request-panel mixins."""
from equinox.gui.request_panel._mixins.body_search_mixin import BodySearchMixin
from equinox.gui.request_panel._mixins.body_state_mixin import BodyStateMixin
from equinox.gui.request_panel._mixins.captures_mixin import CapturesMixin
from equinox.gui.request_panel._mixins.multipart_data_mixin import MultipartDataMixin
from equinox.gui.request_panel._mixins.request_loading_mixin import RequestLoadingMixin


class RequestBodyMixin(
    CapturesMixin, # type: ignore[misc]
    BodyStateMixin,  # type: ignore[misc]
    BodySearchMixin,  # type: ignore[misc]
    MultipartDataMixin,  # type: ignore[misc]
    RequestLoadingMixin,  # type: ignore[misc]
):
    """Compose focused body, search, capture, multipart, and loading helpers."""
