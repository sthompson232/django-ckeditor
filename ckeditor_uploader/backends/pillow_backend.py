from __future__ import absolute_import

import os
from io import BytesIO

from django.conf import settings
from django.utils.functional import cached_property

from PIL import Image, ExifTags

from ckeditor_uploader import utils
import random
import logging

logger = logging.getLogger(getattr(settings, 'CKEDITOR_LOGGER', 'django'))

THUMBNAIL_SIZE = getattr(settings, "CKEDITOR_THUMBNAIL_SIZE", (75, 75))
IMAGE_MAX_WIDTH = getattr(settings, "CKEDITOR_IMAGE_MAX_WIDTH", 1024)
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

    def rotate_image(self, image):
        try:
            for orientation in ExifTags.TAGS.keys():
                if ExifTags.TAGS[orientation] == 'Orientation':
                    break

            if hasattr(image, '_getexif') and image._getexif():
                exif = dict(image._getexif().items())
                if orientation in exif:
                    logger.info("orientation key founded in exif")
                    if exif[orientation] == 3:
                        logger.info("rotate image 180 degrees")
                        image = image.rotate(180, expand=True)
                    elif exif[orientation] == 6:
                        logger.info("rotate image 270 degrees")
                        image = image.rotate(270, expand=True)
                    elif exif[orientation] == 8:
                        logger.info("rotate image 90 degrees")
                        image = image.rotate(90, expand=True)
            else:
                logger.info('Image has no exif information. May be a PNG.')
        except Exception as ex:
            logger.warning("Error rotating image! " + str(ex))
            pass

        return image

    def _compress_image(self, image):
        # First, rotate if needed
        image = self.rotate_image(image)

        quality = getattr(settings, "CKEDITOR_IMAGE_QUALITY", 75)
        w, h = image.size
        widthRatio = 1
        heightRatio = 1

        if IMAGE_MAX_WIDTH > 0:
            widthRatio = max(w / IMAGE_MAX_WIDTH, 1)

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
        # Add a unique ID for the file
        unique_id = '%32x' % random.getrandbits(16 * 8)
        filepath = "%s_%s%s" % (os.path.splitext(filepath)[0], unique_id, os.path.splitext(filepath)[1])
        filepath = filepath.lower()

        if not self.is_image:
            saved_path = self.storage_engine.save(filepath, self.file_object)
            return saved_path

        image = Image.open(self.file_object)

        should_compress = getattr(settings, "CKEDITOR_FORCE_JPEG_COMPRESSION", True)
        is_animated = hasattr(image, 'is_animated') and image.is_animated
        
        img_format = getattr(image, "format", None)
        logger.info("Saving image. Image format is %s" % img_format)
        
        if should_compress and (not is_animated or img_format in ['MPO', 'JPEG', 'PNG']):
            logger.info("Go to compress image")
            file_object = self._compress_image(image)
            # Force jpg extension
            filepath = "%s.jpg" % (os.path.splitext(filepath)[0])
            saved_path = self.storage_engine.save(filepath, file_object)
        else:
            file_object = self.file_object
            saved_path = self.storage_engine.save(filepath, self.file_object)

        if not is_animated or img_format in ['MPO', 'JPEG', 'PNG']:
            self.create_thumbnail(file_object, saved_path)

        image.close()
        return saved_path

    def create_thumbnail(self, file_object, file_path):
        logger.info("Start generating thumbnail for file %s" % file_path)
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
        image.close()
        return self.storage_engine.save(thumbnail_filename, thumbnail_io)
