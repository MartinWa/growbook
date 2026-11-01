"""Stop oversized multipart uploads before they fill temporary storage."""

from django.conf import settings
from django.core.files.uploadhandler import FileUploadHandler, StopUpload
from django.utils.translation import gettext as _


class PhotoLimitUploadHandler(FileUploadHandler):
    def __init__(self, request=None):
        super().__init__(request)
        self.total = 0
        self.current = 0
        self.count = 0

    def reject(self, message):
        self.request.photo_upload_error = message
        raise StopUpload(connection_reset=False)

    def new_file(self, *args, **kwargs):
        super().new_file(*args, **kwargs)
        self.current = 0
        self.count += 1
        if self.count > settings.PHOTO_MAX_COUNT:
            self.reject(_("Too many photos were selected. No entry was saved."))

    def receive_data_chunk(self, raw_data, start):
        self.current += len(raw_data)
        self.total += len(raw_data)
        if self.current > settings.PHOTO_MAX_FILE_BYTES:
            self.reject(_("A photo exceeds the per-photo upload limit. No entry was saved."))
        if self.total > settings.PHOTO_MAX_TOTAL_BYTES:
            self.reject(_("The photos exceed the total upload limit. No entry was saved."))
        return raw_data

    def file_complete(self, file_size):
        return None
