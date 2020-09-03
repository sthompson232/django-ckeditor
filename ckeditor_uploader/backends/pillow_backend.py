from __future__ import absolute_import

import os
from io import BytesIO

from django.conf import settings
from django.utils.functional import cached_property

from PIL import Image

from ckeditor_uploader import utils

THUMBNAIL_SIZE = getattr(settings, "CKEDITOR_THUMBNAIL_SIZE", (75, 75))
IMAGE_MAX_WIDTH = getattr(settings, "CKEDITOR_IMAGE_MAX_WIDTH", 0)
IMAGE_MAX_HEIGHT = getattr(settings, "CKEDITOR_IMAGE_MAX_HEIGHT", 0)


class PillowBackend(object):
    def __init__(self, storage_engine, file_object):
        self.file_object = file_object
        self.storage_engine = storage_engine

    @cached_property
    def is_image(self):
        try:
            Image.open(BytesIO(self.file_object.read())).verify()  # verify closes the file
            return True
        except IOError:
            return False
        finally:
            self.file_object.seek(0)

    def _compress_image(self, image):
        quality = getattr(settings, "CKEDITOR_IMAGE_QUALITY", 75)
        w, h = image.size
        widthRatio = 1
        heightRatio = 1

        if IMAGE_MAX_WIDTH > 0:
            widthRatio = max(w / IMAGE_MAX_HEIGHT, 1)

        if IMAGE_MAX_HEIGHT > 0:
            heightRatio = max(h / IMAGE_MAX_HEIGHT, 1)

        ratio = max(widthRatio, heightRatio)
        newWidth = int(w / ratio)
        newHeight = int(h / ratio)
        newSize = (newWidth, newHeight)

        image = image.resize(newSize, Image.ANTIALIAS).convert('RGB')
        image_tmp = BytesIO()
        image.save(image_tmp, format="JPEG", quality=quality, optimize=True)
        return image_tmp

    def save_as(self, filepath):
        if not self.is_image:
            saved_path = self.storage_engine.save(filepath, self.file_object)
            return saved_path

        image = Image.open(self.file_object)

        should_compress = getattr(settings, "CKEDITOR_FORCE_JPEG_COMPRESSION", False)
        is_animated = hasattr(image, 'is_animated') and image.is_animated
        if should_compress and not is_animated:
            file_object = self._compress_image(image)
            filepath = "{}.jpg".format(os.path.splitext(filepath)[0])
            saved_path = self.storage_engine.save(filepath, file_object)
        else:
            file_object = self.file_object
            saved_path = self.storage_engine.save(filepath, self.file_object)

        if not is_animated:
            self.create_thumbnail(file_object, saved_path)
        return saved_path

    def create_thumbnail(self, file_object, file_path):
        thumbnail_filename = utils.get_thumb_filename(file_path)
        thumbnail_io = BytesIO()
        # File object after saving e.g. to S3 can be closed.
        try:
            image = Image.open(file_object).convert('RGB')
        except ValueError:
            file_object = self.storage_engine.open(file_path)
            image = Image.open(file_object).convert('RGB')
        image.thumbnail(THUMBNAIL_SIZE, Image.ANTIALIAS)
        image.save(thumbnail_io, format='JPEG', optimize=True)
        return self.storage_engine.save(thumbnail_filename, thumbnail_io)
